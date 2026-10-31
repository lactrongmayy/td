import asyncio
import json
import os
import re
import unicodedata
from datetime import timedelta
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks


# Set DISCORD_TOKEN in Secrets instead of hard-coding the bot token.
TOKEN = os.getenv("DISCORD_TOKEN")

CHAT_CHANNEL_ID = 1495362331964870819
LOG_CHANNEL_ID = 1496564307306090526
REMINDER_CHANNEL_ID = 1502014155581030501
LLAUDON_CHANNEL_ID = 1541782500760879214
ALLOWED_LLAUDON_MESSAGES = {"llaudon", "Llaudon"}
LOG_MENTION = "<@1356498767788118099>"
LONG_MESSAGE_LOG_MENTION = (
    "<@1356498767788118099> "
    "<@&1534905534522724614> "
    "<@&1505079149113511987>"
)

DATA_FILE = Path("violations.json")
ID_PATTERN = re.compile(r"(?<!\d)\d{6}(?!\d)")
LINK_PATTERN = re.compile(
    r"(?:https?://|www\.|discord\.gg/)",
    re.IGNORECASE,
)
MAX_TIMEOUT_MINUTES = 28 * 24 * 60
MAX_MESSAGE_WORDS = 80
MENTION_PATTERN = re.compile(r"<@!?\d+>|<@&\d+>")
CUSTOM_EMOJI_PATTERN = re.compile(r"<a?:[A-Za-z0-9_~]+:\d+>")
KEYCAP_EMOJI_PATTERN = re.compile(r"[0-9#*]\ufe0f?\u20e3")
LONG_MESSAGE_DM = (
    "Bạn bị mute là do bạn chat câu dài quá giới hạn mà sv đưa ra. "
    "Nếu  bạn thấy đây là 1 sự nhầm lẫn thì bạn có thể nhắn tin cho "
    "<@1356498767788118099> <@1264245404287897711> "
    "hoặc những người có role hướng dẫn tân thủ trong sv để được mở nha"
)
REMINDER_TEXT = (
    "**Nhắc nhở thân thiện**\n"
    "> `-` Vui lòng @game muốn chơi + ID room + mức rank hiện tại, "
    "để tìm bạn chơi phù hợp. Hạn chế chat linh tinh ở kênh này. "
    "Chuyển sang kênh chat tổng để tám chuyện, Cám ơn!"
)


def is_emoji_character(character: str) -> bool:
    """Nhận diện các nhóm emoji Unicode và ký hiệu emoji phổ biến."""
    codepoint = ord(character)
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or 0x2300 <= codepoint <= 0x23FF
        or 0xFE00 <= codepoint <= 0xFE0F
        or codepoint in {0x200D, 0x20E3}
        or 0x1F3FB <= codepoint <= 0x1F3FF
        or unicodedata.category(character) in {"So", "Sk"}
    )


def count_effective_words(content: str) -> int:
    """Đếm số từ sau khi bỏ mention và emoji khỏi nội dung."""
    content_without_mentions = MENTION_PATTERN.sub("", content)
    content_without_custom_emojis = CUSTOM_EMOJI_PATTERN.sub(
        "", content_without_mentions
    )
    text_without_emojis = "".join(
        character
        for character in content_without_custom_emojis
        if not is_emoji_character(character)
    )
    return len(text_without_emojis.split())


def content_for_id_check(content: str) -> str:
    """Bỏ mã custom emoji và keycap emoji trước khi tìm ID 6 chữ số."""
    without_custom_emojis = CUSTOM_EMOJI_PATTERN.sub("", content)
    return KEYCAP_EMOJI_PATTERN.sub("", without_custom_emojis)


def load_data() -> dict[str, int]:
    if not DATA_FILE.exists():
        return {}

    try:
        with DATA_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            return {}
        return {
            str(user_id): int(count)
            for user_id, count in data.items()
            if isinstance(count, int) and count >= 0
        }
    except (OSError, ValueError, TypeError):
        print("Không thể đọc violations.json; bắt đầu với dữ liệu trống.")
        return {}


violations = load_data()


def save_data() -> None:
    with DATA_FILE.open("w", encoding="utf-8") as file:
        json.dump(violations, file, indent=4, ensure_ascii=False)


intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="none", intents=intents)

# =========================
# HẸN GIỜ NGẮT KHỎI VOICE
# =========================
# Key: (guild_id, member_id) -> asyncio.Task
voice_timers: dict[tuple[int, int], asyncio.Task] = {}


def parse_treo_time(value: str) -> int | None:
    """Đổi 30s / 30m / 2h / 1d thành số giây."""
    match = re.fullmatch(r"(\d+)\s*(s|m|h|d)", value.strip().lower())
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    multiplier = {
        "s": 1,
        "m": 60,
        "h": 60 * 60,
        "d": 24 * 60 * 60,
    }[unit]

    seconds = amount * multiplier
    return seconds if seconds > 0 else None


async def treo_timer(
    guild_id: int,
    member_id: int,
    seconds: int,
    channel_id: int,
) -> None:
    """Chờ hết thời gian rồi ngắt thành viên khỏi voice."""
    try:
        await asyncio.sleep(seconds)

        guild = bot.get_guild(guild_id)
        if guild is None:
            return

        member = guild.get_member(member_id)
        if member is None:
            return

        if member.voice is None:
            channel = bot.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    f"⏰ Hết giờ của **{member.display_name}**, "
                    "nhưng người này hiện không ở voice room."
                )
            return

        room_name = member.voice.channel.name

        # Chỉ ngắt khỏi voice, không kick khỏi server.
        await member.move_to(
            None,
            reason="Hết thời gian /treo",
        )

        channel = bot.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            await channel.send(
                f"⏰ **{member.display_name}** đã hết giờ và "
                f"đã được ngắt khỏi voice room **{room_name}**."
            )

    except asyncio.CancelledError:
        # Timer bị /huytreo hoặc /treo lại nên không làm gì.
        return
    except discord.Forbidden:
        channel = bot.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            await channel.send(
                "❌ Không thể ngắt thành viên khỏi voice. "
                "Hãy cấp cho bot quyền **Move Members** và kiểm tra "
                "role của bot cao hơn role của người đó."
            )
    except discord.HTTPException as error:
        print(f"Lỗi Discord API khi thực hiện /treo: {error}")
    finally:
        voice_timers.pop((guild_id, member_id), None)


@bot.tree.command(
    name="treo",
    description="Hẹn giờ ngắt một người khỏi voice room",
)
@app_commands.describe(
    ten="Người cần hẹn giờ",
    thoi_gian="Ví dụ: 30s, 30m, 2h, 1d",
)
async def treo(
    interaction: discord.Interaction,
    ten: discord.Member,
    thoi_gian: str,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Lệnh này chỉ dùng trong server.",
            ephemeral=True,
        )
        return

    # Thành viên có role "ng quản lí" được treo bất kỳ ai.
    # Người khác chỉ được treo chính mình.
    is_manager = interaction.user.guild_permissions.manage_members
    if not is_manager and ten.id != interaction.user.id:
        await interaction.response.send_message(
            "❌ Bạn chỉ có thể dùng /treo cho chính mình. "
            "Người có quyền **Manage Members (Quản lý thành viên)** mới có thể treo người khác.",
            ephemeral=True,
        )
        return

    seconds = parse_treo_time(thoi_gian)
    if seconds is None:
        await interaction.response.send_message(
            "❌ Thời gian không hợp lệ. Dùng dạng `30s`, `30m`, `2h` hoặc `1d`.",
            ephemeral=True,
        )
        return

    if ten.voice is None:
        await interaction.response.send_message(
            f"❌ **{ten.display_name}** hiện không ở voice room.",
            ephemeral=True,
        )
        return

    bot_member = interaction.guild.me
    if bot_member is None:
        await interaction.response.send_message(
            "❌ Không xác định được thành viên bot trong server.",
            ephemeral=True,
        )
        return

    if not bot_member.guild_permissions.move_members:
        await interaction.response.send_message(
            "❌ Bot chưa có quyền **Move Members**.",
            ephemeral=True,
        )
        return

    # Discord không cho bot move người có role ngang/cao hơn bot.
    if ten.top_role >= bot_member.top_role:
        await interaction.response.send_message(
            f"❌ Không thể treo **{ten.display_name}** vì role cao nhất "
            "của người này ngang hoặc cao hơn role của bot.",
            ephemeral=True,
        )
        return

    key = (interaction.guild.id, ten.id)

    # Nếu đã có timer cũ thì thay bằng timer mới.
    old_task = voice_timers.get(key)
    if old_task is not None:
        old_task.cancel()

    task = asyncio.create_task(
        treo_timer(
            interaction.guild.id,
            ten.id,
            seconds,
            interaction.channel_id,
        )
    )
    voice_timers[key] = task

    await interaction.response.send_message(
        f"⏰ Đã treo **{ten.display_name}**.\n"
        f"🎧 Room hiện tại: **{ten.voice.channel.name}**\n"
        f"⌛ Thời gian: **{thoi_gian}**\n"
        "➡️ Hết giờ bot sẽ ngắt người này khỏi voice room.",
        ephemeral=True,
    )


@bot.tree.command(
    name="huytreo",
    description="Hủy hẹn giờ ngắt khỏi voice",
)
@app_commands.describe(
    ten="Người cần hủy hẹn giờ",
)
async def huytreo(
    interaction: discord.Interaction,
    ten: discord.Member,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Lệnh này chỉ dùng trong server.",
            ephemeral=True,
        )
        return

    # Thành viên có role "ng quản lí" được hủy timer của bất kỳ ai.
    # Người khác chỉ được hủy timer của chính mình.
    is_manager = interaction.user.guild_permissions.manage_members
    if not is_manager and ten.id != interaction.user.id:
        await interaction.response.send_message(
            "❌ Bạn chỉ có thể dùng /huytreo cho chính mình. "
            "Người có quyền **Manage Members (Quản lý thành viên)** mới có thể hủy cho người khác.",
            ephemeral=True,
        )
        return

    key = (interaction.guild.id, ten.id)
    task = voice_timers.pop(key, None)

    if task is None:
        await interaction.response.send_message(
            f"❌ **{ten.display_name}** hiện không có hẹn giờ.",
            ephemeral=True,
        )
        return

    task.cancel()

    await interaction.response.send_message(
        f"✅ Đã hủy hẹn giờ của **{ten.display_name}**.",
        ephemeral=True,
    )



async def send_log_and_mention(
    log_channel: discord.TextChannel,
    embed: discord.Embed,
    error_label: str,
    mention: str = LOG_MENTION,
) -> None:
    """Gửi embed log rồi gửi mention theo sau."""
    try:
        await log_channel.send(embed=embed)
        await log_channel.send(mention)
    except discord.Forbidden:
        print(f"Bot không có quyền gửi {error_label}.")
    except discord.HTTPException as error:
        print(f"Lỗi gửi {error_label}: {error}")


async def log_admin_action(
    context: commands.Context,
    action: str,
    details: str,
) -> None:
    log_channel = bot.get_channel(LOG_CHANNEL_ID)
    if not isinstance(log_channel, discord.TextChannel):
        print("Không tìm thấy kênh log để ghi thao tác quản trị.")
        return

    actor = context.author
    embed = discord.Embed(
        title="🛠️ Thao tác quản trị",
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="👤 Người thực hiện",
        value=f"{actor.mention}\n`{actor}`\nID: `{actor.id}`",
        inline=False,
    )
    embed.add_field(name="📌 Thao tác", value=f"`{action}`", inline=True)
    embed.add_field(name="📝 Chi tiết", value=details, inline=False)
    embed.set_footer(text=f"Guild ID: {context.guild.id if context.guild else 'DM'}")

    await send_log_and_mention(
        log_channel,
        embed,
        "log thao tác quản trị",
    )


@bot.event
async def on_ready() -> None:
    print(f"Bot đã đăng nhập: {bot.user}")
    print(f"ID bot: {bot.user.id}")

    try:
        synced = await bot.tree.sync()
        print(f"Đã đồng bộ {len(synced)} slash command.")
    except discord.HTTPException as error:
        print(f"Lỗi đồng bộ slash command: {error}")

    if not reminder_loop.is_running():
        reminder_loop.start()


@tasks.loop(minutes=2)
async def reminder_loop() -> None:
    """Giữ đúng một tin nhắc mới nhất trong kênh tìm bạn chơi."""
    channel = bot.get_channel(REMINDER_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        print(
            f"Không tìm thấy kênh nhắc nhở hoặc bot không truy cập được "
            f"kênh {REMINDER_CHANNEL_ID}."
        )
        return

    # Xóa các tin nhắc cũ của bot, kể cả tin đã tồn tại trước khi bot restart.
    try:
        async for message in channel.history(limit=50):
            if message.author.id == bot.user.id and message.content == REMINDER_TEXT:
                try:
                    await message.delete()
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    print("Bot không có quyền xóa tin nhắc cũ.")
    except discord.Forbidden:
        print("Bot không có quyền đọc lịch sử kênh nhắc nhở.")
        return
    except discord.HTTPException as error:
        print(f"Lỗi đọc lịch sử kênh nhắc nhở: {error}")
        return

    try:
        await channel.send(REMINDER_TEXT)
    except discord.Forbidden:
        print("Bot không có quyền gửi tin nhắc vào kênh nhắc nhở.")
    except discord.HTTPException as error:
        print(f"Lỗi gửi tin nhắc: {error}")


@reminder_loop.before_loop
async def before_reminder_loop() -> None:
    await bot.wait_until_ready()


@bot.command(name="reset_violation")
@commands.has_guild_permissions(manage_guild=True)
async def reset_violation(
    context: commands.Context,
    member: discord.Member,
) -> None:
    """Đặt lại số lần vi phạm của một thành viên."""
    user_id = str(member.id)
    previous_count = violations.pop(user_id, 0)
    save_data()
    await log_admin_action(
        context,
        "!reset_violation",
        f"Đã reset {member.mention}; số lần trước đó: `{previous_count}`.",
    )

    if previous_count:
        await context.send(
            f"Đã reset số lần vi phạm của {member.mention} "
            f"(từ {previous_count} về 0)."
        )
    else:
        await context.send(f"{member.mention} hiện không có lịch sử vi phạm.")


@reset_violation.error
async def reset_violation_error(
    context: commands.Context,
    error: commands.CommandError,
) -> None:
    if isinstance(error, commands.MissingPermissions):
        await context.send("Bạn cần quyền Manage Server để dùng lệnh này.")
    elif isinstance(error, commands.MemberNotFound):
        await context.send("Không tìm thấy thành viên. Hãy mention người đó.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await context.send("Cách dùng: `!reset_violation @thành_viên`")
    else:
        print(f"Lỗi lệnh reset_violation: {error}")


@bot.command(name="reset_all")
@commands.has_guild_permissions(manage_guild=True)
async def reset_all(
    context: commands.Context,
    confirmation: str = "",
) -> None:
    """Xóa lịch sử vi phạm của toàn bộ thành viên."""
    if confirmation.lower() != "confirm":
        await context.send(
            "Lệnh này sẽ xóa toàn bộ lịch sử vi phạm. "
            "Nếu chắc chắn, dùng: `!reset_all confirm`"
        )
        return

    total_members = len(violations)
    violations.clear()
    save_data()
    await log_admin_action(
        context,
        "!reset_all",
        f"Đã xóa lịch sử vi phạm của `{total_members}` tài khoản.",
    )
    await context.send(
        f"Đã reset lịch sử vi phạm của toàn bộ thành viên "
        f"({total_members} tài khoản)."
    )


@reset_all.error
async def reset_all_error(
    context: commands.Context,
    error: commands.CommandError,
) -> None:
    if isinstance(error, commands.MissingPermissions):
        await context.send("Bạn cần quyền Manage Server để dùng lệnh này.")
    else:
        print(f"Lỗi lệnh reset_all: {error}")


async def handle_long_message(
    message: discord.Message,
    effective_length: int,
) -> None:
    """Xóa, timeout và ghi log tin nhắn vượt quá 80 từ."""
    try:
        await message.delete()
    except discord.Forbidden:
        print("Bot không có quyền xóa tin nhắn dài.")
    except discord.NotFound:
        pass

    try:
        await message.author.timeout(
            timedelta(minutes=5),
            reason="Gửi tin nhắn vượt quá 80 từ",
        )
    except discord.Forbidden:
        print(
            f"Không thể timeout {message.author} vì tin nhắn dài. "
            "Kiểm tra quyền Moderate Members và role của bot."
        )
    except discord.HTTPException as error:
        print(f"Lỗi timeout tin nhắn dài: {error}")

    try:
        await message.author.send(LONG_MESSAGE_DM)
    except discord.Forbidden:
        print(f"Không thể gửi DM vi phạm giới hạn cho {message.author}.")
    except discord.HTTPException as error:
        print(f"Lỗi gửi DM vi phạm giới hạn: {error}")

    log_channel = message.guild.get_channel(LOG_CHANNEL_ID)
    if log_channel is None:
        try:
            log_channel = await message.guild.fetch_channel(LOG_CHANNEL_ID)
        except discord.HTTPException as error:
            print(f"Không thể lấy kênh log tin nhắn dài: {error}")
            return

    if not isinstance(log_channel, discord.TextChannel):
        print("Kênh log không phải là kênh văn bản.")
        return

    embed = discord.Embed(
        title="⚠️ Vi phạm giới hạn số từ",
        color=discord.Color.red(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Người vi phạm",
        value=f"{message.author.mention} ({message.author})",
        inline=True,
    )
    embed.add_field(
        name="Kênh",
        value=message.channel.mention,
        inline=True,
    )
    embed.add_field(
        name="Xử lý",
        value="Timeout 5 phút & xóa tin nhắn",
        inline=False,
    )
    embed.add_field(
        name="Nội dung vi phạm",
        value=message.content[:1024],
        inline=False,
    )
    embed.set_footer(
        text=(
            f"Số từ tính giới hạn: {effective_length} | "
            f"Số từ gốc: {len(message.content.split())}"
        )
    )

    await send_log_and_mention(
        log_channel,
        embed,
        "log tin nhắn dài",
        mention=LONG_MESSAGE_LOG_MENTION,
    )


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    # Bỏ qua tin nhắn riêng và chỉ xử lý giới hạn số từ trong server.
    if not message.guild:
        await bot.process_commands(message)
        return

    # Bảo vệ thành viên có thứ hạng role ngang hoặc cao hơn bot.
    # Discord không cho bot kiểm duyệt thành viên có top role bằng/cao hơn bot.
    bot_member = message.guild.me
    if bot_member is None:
        try:
            bot_member = await message.guild.fetch_member(bot.user.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as error:
            print(f"Không thể xác định role cao nhất của bot: {error}")
            await bot.process_commands(message)
            return

    if message.author.top_role >= bot_member.top_role:
        await bot.process_commands(message)
        return

    # Kênh Llaudon chỉ cho phép đúng hai cách viết này.
    if (
        message.channel.id == LLAUDON_CHANNEL_ID
        and message.content not in ALLOWED_LLAUDON_MESSAGES
    ):
        try:
            await message.delete()
        except discord.Forbidden:
            print("Bot không có quyền xóa tin nhắn trong kênh Llaudon.")
        except discord.NotFound:
            pass
        return

    # Xử lý giới hạn 80 từ trước, nhưng vẫn tiếp tục kiểm tra ID 6 số bên dưới.
    effective_words = count_effective_words(message.content)
    if effective_words > MAX_MESSAGE_WORDS and not LINK_PATTERN.search(message.content):
        await handle_long_message(message, effective_words)

    if message.channel.id != CHAT_CHANNEL_ID:
        await bot.process_commands(message)
        return

    id_check_content = content_for_id_check(message.content)
    if ID_PATTERN.search(id_check_content):
        user_id = str(message.author.id)
        violations[user_id] = violations.get(user_id, 0) + 1
        count = violations[user_id]

        requested_minutes = count * 5
        mute_minutes = min(requested_minutes, MAX_TIMEOUT_MINUTES)
        save_data()

        try:
            await message.delete()
        except discord.Forbidden:
            print("Bot không có quyền xóa tin nhắn.")
        except discord.NotFound:
            pass

        try:
            await message.author.timeout(
                timedelta(minutes=mute_minutes),
                reason="Gửi ID game Liên Quân lên chat tổng",
            )
        except discord.Forbidden:
            print(
                f"Không thể mute {message.author}. "
                "Kiểm tra quyền Moderate Members và role của bot."
            )
        except discord.HTTPException as error:
            print(f"Lỗi Discord API khi timeout: {error}")

        text = (
            f"{message.author.display_name}, bạn đã bị cấm chat "
            f"trong vòng {mute_minutes} phút. "
            "Lý do: gửi ID game lên chat tổng."
        )

        try:
            await message.author.send(text)
        except discord.Forbidden:
            print(
                f"Không thể gửi DM cho {message.author}. "
                "Có thể người dùng đã tắt DM."
            )
        except discord.HTTPException as error:
            print(f"Lỗi gửi DM: {error}")

        log_channel = bot.get_channel(LOG_CHANNEL_ID)
        if isinstance(log_channel, discord.TextChannel):
            embed = discord.Embed(
                title="🚫 Thành viên bị mute",
                color=discord.Color.red(),
            )
            embed.add_field(
                name="👤 Người vi phạm",
                value=(
                    f"{message.author.mention}\n"
                    f"`{message.author}`\n"
                    f"ID: `{message.author.id}`"
                ),
                inline=False,
            )
            embed.add_field(
                name="⏱️ Thời gian mute",
                value=f"`{mute_minutes} phút`",
                inline=True,
            )
            embed.add_field(
                name="🔢 Lần vi phạm",
                value=f"`Lần {count}`",
                inline=True,
            )
            embed.add_field(
                name="📌 Lý do",
                value="Gửi ID game Liên Quân lên chat tổng",
                inline=False,
            )
            embed.add_field(
                name="🗑️ Tin nhắn",
                value="Đã xóa tin nhắn chứa ID 6 số.",
                inline=False,
            )
            embed.set_footer(text=f"User ID: {message.author.id}")

            await send_log_and_mention(
                log_channel,
                embed,
                "log vi phạm ID",
            )

    await bot.process_commands(message)


if not TOKEN:
    raise RuntimeError(
        "Thiếu biến môi trường DISCORD_TOKEN. "
        "Hãy đặt token trong Secrets/Environment Variables."
    )

bot.run(TOKEN)
