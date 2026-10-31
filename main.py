import asyncio
import json
import logging
import os
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks

# =========================================================
# CẤU HÌNH CHUNG & BIẾN MÔI TRƯỜNG
# =========================================================


def read_int_setting(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise RuntimeError(f"{name} phải là số nguyên.") from error


TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

OWNER_ID = read_int_setting(
    "OWNER_ID",
    1356498767788118099,
)

TEST_GUILD_ID = read_int_setting(
    "TEST_GUILD_ID",
    1495362331163754589,
)

# =========================================================
# ID KÊNH & CẤU HÌNH HỆ THỐNG
# =========================================================

CHAT_CHANNEL_ID = 1495362331964870819
ADMIN_LOG_CHANNEL_ID = 1496564307306090526
REMINDER_CHANNEL_ID = 1502014155581030501
LLAUDON_CHANNEL_ID = 1541782500760879214

IMAGE_REACTION_CHANNEL_ID = 1557292130039369798
RECORD_CHANNEL_ID = 1556550052309377064
REPORT_CHANNEL_ID = 1564912638272602142

START_DATE = date(2026, 4, 19)
END_DATE = date(2026, 10, 28)
REPORT_TIMEZONE = timezone.utc

DB_FILE = "tracking.sqlite3"
DATABASE_PATH = "message_stats.db"
DATA_FILE = Path("violations.json")

ALLOWED_LLAUDON_MESSAGES = {"llaudon", "Llaudon"}
LOG_MENTION = "<@1356498767788118099>"
LONG_MESSAGE_LOG_MENTION = (
    "<@1356498767788118099> "
    "<@&1534905534522724614> "
    "<@&1505079149113511987>"
)

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
    "Nếu bạn thấy đây là 1 sự nhầm lẫn thì bạn có thể nhắn tin cho "
    "<@1356498767788118099> <@1264245404287897711> "
    "hoặc những người có role hướng dẫn tân thủ trong sv để được mở nha"
)

REMINDER_TEXT = (
    "**Nhắc nhở thân thiện**\n"
    "> `-` Vui lòng @game muốn chơi + ID room + mức rank hiện tại, "
    "để tìm bạn chơi phù hợp. Hạn chế chat linh tinh ở kênh này. "
    "Chuyển sang kênh chat tổng để tám chuyện, Cám ơn!"
)

IMAGE_REACTIONS = [
    discord.PartialEmoji(name="bucboi", id=1553391349045399722, animated=True),
    discord.PartialEmoji(name="lauhang", id=1552946293062766602, animated=True),
    discord.PartialEmoji(name="ahihi", id=1496351539550224425, animated=True),
    discord.PartialEmoji(name="gomo", id=1552946448688095262, animated=True),
    discord.PartialEmoji(
        name="TC_blinkblink", id=1500004350674735155, animated=True
    ),
]

# =========================================================
# LOGGING SETUP
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s: %(message)s",
)
logger = logging.getLogger("combined_discord_bot")

# =========================================================
# DATABASE SETUP (SQLITE & JSON)
# =========================================================

db = sqlite3.connect(DB_FILE)
db.execute(
    """
CREATE TABLE IF NOT EXISTS tracking (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    log_channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
)
"""
)
db.commit()


def initialize_database():
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_stats (
                channel_id INTEGER NOT NULL,
                report_date TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (channel_id, report_date)
            )
        """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                total_messages INTEGER,
                status TEXT NOT NULL
            )
        """
        )
        conn.commit()


def load_violations_data() -> dict[str, int]:
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
        logger.warning(
            "Không thể đọc violations.json; bắt đầu với dữ liệu trống."
        )
        return {}


violations = load_violations_data()


def save_violations_data() -> None:
    with DATA_FILE.open("w", encoding="utf-8") as file:
        json.dump(violations, file, indent=4, ensure_ascii=False)


def get_tracking(guild_id: int, user_id: int):
    return db.execute(
        """
        SELECT log_channel_id
        FROM tracking
        WHERE guild_id = ? AND user_id = ?
        """,
        (guild_id, user_id),
    ).fetchone()


def save_daily_stats(channel_id: int, day: date, count: int):
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute(
            """
            INSERT INTO daily_stats (
                channel_id, report_date, message_count, completed
            )
            VALUES (?, ?, ?, 1)
            ON CONFLICT(channel_id, report_date)
            DO UPDATE SET
                message_count = excluded.message_count,
                completed = 1
        """,
            (channel_id, day.isoformat(), count),
        )
        conn.commit()


def get_total_stats(channel_id: int):
    with sqlite3.connect(DATABASE_PATH) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(message_count), 0)
            FROM daily_stats
            WHERE channel_id = ?
              AND completed = 1
              AND report_date >= ?
              AND report_date <= ?
        """,
            (
                channel_id,
                START_DATE.isoformat(),
                END_DATE.isoformat(),
            ),
        ).fetchone()
    return row[0] if row else 0


def create_scan_run():
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.execute(
            """
            INSERT INTO scan_runs (
                started_at, start_date, end_date, status
            )
            VALUES (?, ?, ?, ?)
        """,
            (
                datetime.now(timezone.utc).isoformat(),
                START_DATE.isoformat(),
                END_DATE.isoformat(),
                "running",
            ),
        )
        conn.commit()
        return cursor.lastrowid


def finish_scan_run(run_id: int, total: int, status: str):
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute(
            """
            UPDATE scan_runs
            SET finished_at = ?,
                total_messages = ?,
                status = ?
            WHERE id = ?
        """,
            (
                datetime.now(timezone.utc).isoformat(),
                total,
                status,
                run_id,
            ),
        )
        conn.commit()


# =========================================================
# STATE & HELPER FUNCTIONS
# =========================================================

bot_enabled = False
voice_targets: dict[int, int] = {}
voice_locks: dict[int, asyncio.Lock] = {}
voice_timers: dict[tuple[int, int], asyncio.Task] = {}

scan_lock = asyncio.Lock()
scan_progress = {
    "running": False,
    "current": 0,
    "total_days": (END_DATE - START_DATE).days + 1,
    "current_date": None,
}


def get_voice_lock(guild_id: int) -> asyncio.Lock:
    if guild_id not in voice_locks:
        voice_locks[guild_id] = asyncio.Lock()
    return voice_locks[guild_id]


def is_emoji_character(character: str) -> bool:
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
    without_custom_emojis = CUSTOM_EMOJI_PATTERN.sub("", content)
    return KEYCAP_EMOJI_PATTERN.sub("", without_custom_emojis)


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def day_bounds(day: date):
    start = datetime.combine(
        day, time.min, tzinfo=REPORT_TIMEZONE
    ).astimezone(timezone.utc)
    end = datetime.combine(
        day + timedelta(days=1),
        time.min,
        tzinfo=REPORT_TIMEZONE,
    ).astimezone(timezone.utc)
    return start, end


async def count_messages(channel: discord.TextChannel, day: date) -> int:
    start, end = day_bounds(day)
    count = 0
    async for _ in channel.history(
        limit=None, after=start, before=end, oldest_first=True
    ):
        count += 1
    return count


async def send_report(channel, content: str):
    limit = 1900
    while content:
        chunk = content[:limit]
        split_at = chunk.rfind("\n")
        if split_at > 0 and len(content) > limit:
            chunk = content[:split_at]
        await channel.send(chunk)
        content = content[len(chunk) :].lstrip("\n")


async def send_log_and_mention(
    log_channel: discord.TextChannel,
    embed: discord.Embed,
    error_label: str,
    mention: str = LOG_MENTION,
) -> None:
    try:
        await log_channel.send(embed=embed)
        await log_channel.send(mention)
    except discord.Forbidden:
        logger.error(f"Bot không có quyền gửi {error_label}.")
    except discord.HTTPException as error:
        logger.error(f"Lỗi gửi {error_label}: {error}")


async def send_log(
    guild_id: int,
    user_id: int,
    title: str,
    description: str,
):
    row = get_tracking(guild_id, user_id)
    if not row:
        return
    channel_id = row[0]
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

    embed = discord.Embed(
        title=title,
        description=description[:4000],
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Thời gian: {timestamp()}")
    try:
        await channel.send(embed=embed)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        logger.exception("Không gửi được nhật ký.")


async def log_admin_action(
    context: commands.Context,
    action: str,
    details: str,
) -> None:
    log_channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
    if not isinstance(log_channel, discord.TextChannel):
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
    embed.set_footer(
        text=f"Guild ID: {context.guild.id if context.guild else 'DM'}"
    )

    await send_log_and_mention(
        log_channel,
        embed,
        "log thao tác quản trị",
    )


# =========================================================
# BOT INITIALIZATION & SETUP HOOK
# =========================================================

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
intents.voice_states = True


class CombinedBot(commands.Bot):

    async def setup_hook(self):
        initialize_database()
        try:
            if TEST_GUILD_ID:
                guild = discord.Object(id=TEST_GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logger.info("Đã đồng bộ %s lệnh vào server test.", len(synced))
            else:
                synced = await self.tree.sync()
                logger.info("Đã đồng bộ %s slash command.", len(synced))
        except discord.HTTPException:
            logger.exception("Không thể đồng bộ slash command.")

        if not reminder_loop.is_running():
            reminder_loop.start()


bot = CombinedBot(command_prefix="none", intents=intents)


# =========================================================
# PERMISSION CHECKS
# =========================================================


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == OWNER_ID


def is_admin(interaction: discord.Interaction) -> bool:
    return (
        interaction.guild is not None
        and isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.administrator
    )


def owner_only():
    async def predicate(interaction: discord.Interaction):
        return is_owner(interaction)

    return app_commands.check(predicate)


def admin_only():
    async def predicate(interaction: discord.Interaction):
        return is_admin(interaction)

    return app_commands.check(predicate)


def owner_admin_only():
    async def predicate(interaction: discord.Interaction):
        return is_owner(interaction) and is_admin(interaction)

    return app_commands.check(predicate)


# =========================================================
# VOICE HELPERS & VOICE TIMERS
# =========================================================


def parse_treo_time(value: str) -> int | None:
    match = re.fullmatch(r"(\d+)\s*(s|m|h|d)", value.strip().lower())
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    seconds = amount * multiplier
    return seconds if seconds > 0 else None


async def treo_timer(
    guild_id: int,
    member_id: int,
    seconds: int,
    channel_id: int,
) -> None:
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
        await member.move_to(None, reason="Hết thời gian /treo")
        channel = bot.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            await channel.send(
                f"⏰ **{member.display_name}** đã hết giờ và "
                f"đã được ngắt khỏi voice room **{room_name}**."
            )
    except asyncio.CancelledError:
        return
    except discord.Forbidden:
        channel = bot.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            await channel.send(
                "❌ Không thể ngắt thành viên khỏi voice. "
                "Kiểm tra quyền **Move Members**."
            )
    except discord.HTTPException as error:
        logger.error(f"Lỗi Discord API khi thực hiện /treo: {error}")
    finally:
        voice_timers.pop((guild_id, member_id), None)


def get_invoker_voice_channel(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        return None
    voice_state = interaction.user.voice
    if voice_state is None:
        return None
    channel = voice_state.channel
    if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
        return channel
    return None


async def connect_to_target_voice(guild: discord.Guild) -> bool:
    async with get_voice_lock(guild.id):
        channel_id = voice_targets.get(guild.id)
        if channel_id is None:
            return False
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            return False
        try:
            voice_client = guild.voice_client
            if voice_client and voice_client.is_connected():
                if voice_client.channel and voice_client.channel.id == channel.id:
                    return True
                await voice_client.move_to(channel)
                return True
            if voice_client:
                try:
                    await voice_client.disconnect(force=True)
                except Exception:
                    pass
            await channel.connect(reconnect=True, timeout=30.0)
            return True
        except Exception:
            logger.exception("Không thể kết nối voice.")
            return False


async def reconnect_target_voice(guild: discord.Guild, target_id: int):
    for attempt in range(1, 6):
        await asyncio.sleep(3)
        if voice_targets.get(guild.id) != target_id:
            return
        if await connect_to_target_voice(guild):
            return


# =========================================================
# BACKGROUND TASKS
# =========================================================


@tasks.loop(minutes=2)
async def reminder_loop() -> None:
    channel = bot.get_channel(REMINDER_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        return
    try:
        async for message in channel.history(limit=50):
            if (
                message.author.id == bot.user.id
                and message.content == REMINDER_TEXT
            ):
                try:
                    await message.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass
    except Exception:
        pass

    try:
        await channel.send(REMINDER_TEXT)
    except Exception:
        pass


@reminder_loop.before_loop
async def before_reminder_loop() -> None:
    await bot.wait_until_ready()


# =========================================================
# PREFIX COMMANDS (VIOLATION RESET)
# =========================================================


@bot.command(name="reset_violation")
@commands.has_guild_permissions(manage_guild=True)
async def reset_violation(
    context: commands.Context,
    member: discord.Member,
) -> None:
    user_id = str(member.id)
    previous_count = violations.pop(user_id, 0)
    save_violations_data()
    await log_admin_action(
        context,
        "!reset_violation",
        f"Đã reset {member.mention}; số lần trước đó: `{previous_count}`.",
    )
    if previous_count:
        await context.send(
            f"Đã reset số lần vi phạm của {member.mention} (từ {previous_count} về 0)."
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


@bot.command(name="reset_all")
@commands.has_guild_permissions(manage_guild=True)
async def reset_all(
    context: commands.Context,
    confirmation: str = "",
) -> None:
    if confirmation.lower() != "confirm":
        await context.send(
            "Lệnh này sẽ xóa toàn bộ lịch sử vi phạm. "
            "Nếu chắc chắn, dùng: `!reset_all confirm`"
        )
        return

    total_members = len(violations)
    violations.clear()
    save_violations_data()
    await log_admin_action(
        context,
        "!reset_all",
        f"Đã xóa lịch sử vi phạm của `{total_members}` tài khoản.",
    )
    await context.send(
        f"Đã reset lịch sử vi phạm của toàn bộ thành viên ({total_members} tài khoản)."
    )


@reset_all.error
async def reset_all_error(
    context: commands.Context,
    error: commands.CommandError,
) -> None:
    if isinstance(error, commands.MissingPermissions):
        await context.send("Bạn cần quyền Manage Server để dùng lệnh này.")


# =========================================================
# SLASH COMMANDS
# =========================================================


@bot.tree.command(name="treo", description="Hẹn giờ ngắt một người khỏi voice room")
@app_commands.describe(ten="Người cần hẹn giờ", thoi_gian="Ví dụ: 30s, 30m, 2h, 1d")
async def treo(
    interaction: discord.Interaction,
    ten: discord.Member,
    thoi_gian: str,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Lệnh này chỉ dùng trong server.", ephemeral=True
        )
        return

    is_manager = interaction.user.guild_permissions.manage_members
    if not is_manager and ten.id != interaction.user.id:
        await interaction.response.send_message(
            "❌ Bạn chỉ có thể dùng /treo cho chính mình.", ephemeral=True
        )
        return

    seconds = parse_treo_time(thoi_gian)
    if seconds is None:
        await interaction.response.send_message(
            "❌ Thời gian không hợp lệ (`30s`, `30m`, `2h`, `1d`).", ephemeral=True
        )
        return

    if ten.voice is None:
        await interaction.response.send_message(
            f"❌ **{ten.display_name}** không ở voice room.", ephemeral=True
        )
        return

    bot_member = interaction.guild.me
    if not bot_member or not bot_member.guild_permissions.move_members:
        await interaction.response.send_message(
            "❌ Bot chưa có quyền **Move Members**.", ephemeral=True
        )
        return

    if ten.top_role >= bot_member.top_role:
        await interaction.response.send_message(
            f"❌ Không thể treo **{ten.display_name}** vì role cao hơn hoặc bằng bot.",
            ephemeral=True,
        )
        return

    key = (interaction.guild.id, ten.id)
    if old_task := voice_timers.get(key):
        old_task.cancel()

    voice_timers[key] = asyncio.create_task(
        treo_timer(
            interaction.guild.id, ten.id, seconds, interaction.channel_id
        )
    )
    await interaction.response.send_message(
        f"⏰ Đã treo **{ten.display_name}** trong **{thoi_gian}**.", ephemeral=True
    )


@bot.tree.command(name="huytreo", description="Hủy hẹn giờ ngắt khỏi voice")
@app_commands.describe(ten="Người cần hủy hẹn giờ")
async def huytreo(
    interaction: discord.Interaction, ten: discord.Member
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ Lệnh này chỉ dùng trong server.", ephemeral=True
        )
        return

    is_manager = interaction.user.guild_permissions.manage_members
    if not is_manager and ten.id != interaction.user.id:
        await interaction.response.send_message(
            "❌ Bạn chỉ có thể hủy hẹn giờ của chính mình.", ephemeral=True
        )
        return

    key = (interaction.guild.id, ten.id)
    task = voice_timers.pop(key, None)
    if task is None:
        await interaction.response.send_message(
            f"❌ **{ten.display_name}** không có hẹn giờ nào.", ephemeral=True
        )
        return

    task.cancel()
    await interaction.response.send_message(
        f"✅ Đã hủy hẹn giờ của **{ten.display_name}**.", ephemeral=True
    )


@bot.tree.command(name="chat", description="Gửi tin nhắn vào kênh hiện tại")
@app_commands.guild_only()
@app_commands.describe(noidung="Nội dung tin nhắn cần gửi")
@owner_only()
async def chat(interaction: discord.Interaction, noidung: str):
    if not noidung.strip():
        await interaction.response.send_message(
            "❌ Nội dung không được để trống.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    channel = interaction.channel
    try:
        await channel.send(
            content=noidung, allowed_mentions=discord.AllowedMentions.all()
        )
        await interaction.followup.send("✅ Đã gửi tin nhắn.", ephemeral=True)
    except Exception:
        await interaction.followup.send(
            "❌ Không thể gửi tin nhắn.", ephemeral=True
        )


@bot.tree.command(name="join", description="Cho bot vào phòng voice và bám phòng")
@app_commands.guild_only()
@admin_only()
async def join(interaction: discord.Interaction):
    channel = get_invoker_voice_channel(interaction)
    if channel is None:
        await interaction.response.send_message(
            "❌ Bạn phải vào phòng voice trước.", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    voice_targets[guild.id] = channel.id
    if not await connect_to_target_voice(guild):
        voice_targets.pop(guild.id, None)
        await interaction.followup.send(
            "❌ Không thể vào phòng. Kiểm tra quyền Connect.", ephemeral=True
        )
        return
    await interaction.followup.send(
        f"🎙️ Bot đã vào **{channel.name}** và bám phòng.", ephemeral=True
    )


@bot.tree.command(name="leave", description="Cho bot rời voice và tắt bám phòng")
@app_commands.guild_only()
@admin_only()
async def leave(interaction: discord.Interaction):
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True)
    voice_targets.pop(guild.id, None)
    if guild.voice_client:
        try:
            await guild.voice_client.disconnect(force=True)
        except Exception:
            pass
    await interaction.followup.send(
        "👋 Bot đã rời phòng và tắt chế độ bám phòng.", ephemeral=True
    )


@bot.tree.command(name="on", description="Bật ghi nhận và xóa tin nhắn tại kênh nguồn")
@app_commands.guild_only()
@owner_admin_only()
async def turn_on(interaction: discord.Interaction):
    global bot_enabled
    bot_enabled = True
    await interaction.response.send_message(
        "✅ Đã bật ghi nhận và xóa tin nhắn.", ephemeral=True
    )


@bot.tree.command(name="off", description="Tắt ghi nhận và xóa tin nhắn tại kênh nguồn")
@app_commands.guild_only()
@owner_admin_only()
async def turn_off(interaction: discord.Interaction):
    global bot_enabled
    bot_enabled = False
    await interaction.response.send_message(
        "🛑 Đã tắt ghi nhận và xóa tin nhắn.", ephemeral=True
    )


@bot.tree.command(name="theodoi", description="Bắt đầu ghi nhật ký hoạt động thành viên")
@app_commands.guild_only()
@admin_only()
@app_commands.describe(user="Thành viên được ghi nhật ký", log="Kênh nhận nhật ký")
async def theodoi(
    interaction: discord.Interaction, user: discord.Member, log: discord.TextChannel
):
    guild = interaction.guild
    if user.bot:
        await interaction.response.send_message(
            "❌ Không thể theo dõi bot.", ephemeral=True
        )
        return
    if log.guild.id != guild.id:
        await interaction.response.send_message(
            "❌ Kênh nhật ký phải thuộc server này.", ephemeral=True
        )
        return

    db.execute(
        """
        INSERT INTO tracking (guild_id, user_id, log_channel_id)
        VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id)
        DO UPDATE SET log_channel_id = excluded.log_channel_id
        """,
        (guild.id, user.id, log.id),
    )
    db.commit()

    await interaction.response.send_message(
        f"✅ Đã bật ghi nhật ký cho {user.mention} tại {log.mention}.", ephemeral=True
    )
    await send_log(
        guild.id,
        user.id,
        "📋 Bắt đầu ghi nhật ký",
        f"Thành viên: {user.mention}\nNgười thiết lập: {interaction.user.mention}",
    )


@bot.tree.command(name="dungtheodoi", description="Dừng ghi nhật ký của thành viên")
@app_commands.guild_only()
@admin_only()
@app_commands.describe(user="Thành viên cần dừng ghi nhật ký")
async def dungtheodoi(interaction: discord.Interaction, user: discord.Member):
    guild = interaction.guild
    row = get_tracking(guild.id, user.id)
    if not row:
        await interaction.response.send_message(
            "Thành viên này không nằm trong danh sách theo dõi.", ephemeral=True
        )
        return

    db.execute(
        "DELETE FROM tracking WHERE guild_id = ? AND user_id = ?",
        (guild.id, user.id),
    )
    db.commit()
    await interaction.response.send_message(
        f"✅ Đã dừng ghi nhật ký cho {user.mention}.", ephemeral=True
    )


@bot.tree.command(
    name="danhsachtheodoi", description="Xem danh sách thành viên đang theo dõi"
)
@app_commands.guild_only()
@admin_only()
async def danhsachtheodoi(interaction: discord.Interaction):
    guild = interaction.guild
    rows = db.execute(
        "SELECT user_id, log_channel_id FROM tracking WHERE guild_id = ?",
        (guild.id,),
    ).fetchall()
    if not rows:
        await interaction.response.send_message(
            "Hiện không có thành viên nào được ghi nhật ký.", ephemeral=True
        )
        return

    lines = [f"• <@{user_id}> → <#{channel_id}>" for user_id, channel_id in rows]
    embed = discord.Embed(
        title="📋 Danh sách ghi nhật ký",
        description="\n".join(lines)[:4000],
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="quet", description="Quét thống kê tin nhắn theo từng ngày"
)
@app_commands.guild_only()
@owner_only()
async def quet(interaction: discord.Interaction):
    if scan_lock.locked():
        await interaction.response.send_message(
            "Bot đang quét dữ liệu. Vui lòng đợi.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    async with scan_lock:
        source_channel = bot.get_channel(CHAT_CHANNEL_ID)
        report_channel = bot.get_channel(REPORT_CHANNEL_ID)
        if not isinstance(source_channel, discord.TextChannel) or not isinstance(
            report_channel, discord.TextChannel
        ):
            await interaction.followup.send(
                "Không tìm thấy kênh nguồn hoặc kênh báo cáo.", ephemeral=True
            )
            return

        run_id = create_scan_run()
        total_days = scan_progress["total_days"]
        total_messages = 0
        completed_days = 0

        await send_report(
            report_channel,
            (
                "🔎 **BẮT ĐẦU QUÉT TIN NHẮN**\n"
                f"📍 Kênh: <#{CHAT_CHANNEL_ID}>\n"
                f"📅 Từ: {START_DATE.strftime('%d/%m/%Y')} Đến: {END_DATE.strftime('%d/%m/%Y')}"
            ),
        )

        try:
            day = START_DATE
            while day <= END_DATE:
                completed_days += 1
                count = await count_messages(source_channel, day)
                save_daily_stats(CHAT_CHANNEL_ID, day, count)
                total_messages += count
                day += timedelta(days=1)

            total_messages = get_total_stats(CHAT_CHANNEL_ID)
            finish_scan_run(run_id, total_messages, "completed")
            await send_report(
                report_channel,
                f"✅ **HOÀN TẤT QUÉT DỮ LIỆU**\n💬 Tổng tin nhắn: **{total_messages:,}**",
            )
            await interaction.followup.send(
                "✅ Đã quét xong toàn bộ dữ liệu.", ephemeral=True
            )
        except Exception as exc:
            finish_scan_run(
                run_id, get_total_stats(CHAT_CHANNEL_ID), "failed"
            )
            await interaction.followup.send(
                f"⚠️ Quá trình quét bị gián đoạn: {exc}", ephemeral=True
            )


# =========================================================
# EVENT HANDLERS (MESSAGES & VOICE)
# =========================================================


async def handle_long_message(
    message: discord.Message, effective_length: int
) -> None:
    try:
        await message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass

    try:
        await message.author.timeout(
            timedelta(minutes=5), reason="Gửi tin nhắn vượt quá 80 từ"
        )
    except Exception:
        pass

    try:
        await message.author.send(LONG_MESSAGE_DM)
    except Exception:
        pass

    log_channel = message.guild.get_channel(ADMIN_LOG_CHANNEL_ID)
    if isinstance(log_channel, discord.TextChannel):
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
        embed.add_field(name="Kênh", value=message.channel.mention, inline=True)
        embed.add_field(
            name="Nội dung vi phạm", value=message.content[:1024], inline=False
        )
        await send_log_and_mention(
            log_channel, embed, "log tin nhắn dài", mention=LONG_MESSAGE_LOG_MENTION
        )


def message_has_image(message: discord.Message) -> bool:
    image_extensions = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
    for att in message.attachments:
        if att.filename.lower().endswith(image_extensions) or (
            att.content_type and att.content_type.startswith("image/")
        ):
            return True
    for embed in message.embeds:
        if (embed.image and embed.image.url) or (
            embed.thumbnail and embed.thumbnail.url
        ):
            return True
    return False


async def react_to_image(message: discord.Message):
    if message.channel.id != IMAGE_REACTION_CHANNEL_ID or message.author.bot:
        return
    if not message_has_image(message):
        return
    for emoji in IMAGE_REACTIONS:
        try:
            await message.add_reaction(emoji)
        except Exception:
            pass


async def record_and_delete_message(message: discord.Message) -> bool:
    content = message.content or "(Không có nội dung văn bản)"
    embed = discord.Embed(
        title="📋 BẢN GHI TIN NHẮN",
        color=discord.Color.orange(),
        timestamp=message.created_at,
    )
    embed.add_field(
        name="👤 Người gửi",
        value=f"{message.author.mention}\n`{message.author}`\nID: `{message.author.id}`",
        inline=False,
    )
    embed.add_field(
        name="📍 Kênh gốc", value=f"<#{message.channel.id}>", inline=False
    )
    embed.add_field(name="💬 Nội dung", value=content[:1024], inline=False)
    embed.set_footer(text=f"Message ID: {message.id}")

    try:
        record_channel = bot.get_channel(RECORD_CHANNEL_ID)
        if record_channel is None:
            record_channel = await bot.fetch_channel(RECORD_CHANNEL_ID)
        await record_channel.send(embed=embed)
        await message.delete()
        return True
    except Exception:
        return False


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return

    if not message.guild:
        await bot.process_commands(message)
        return

    # 1. Tự động react ảnh
    await react_to_image(message)

    # 2. Kiểm tra kênh Llaudon
    if (
        message.channel.id == LLAUDON_CHANNEL_ID
        and message.content not in ALLOWED_LLAUDON_MESSAGES
    ):
        try:
            await message.delete()
        except Exception:
            pass
        return

    # Kiểm tra quyền kiểm duyệt của bot với member
    bot_member = message.guild.me
    can_moderate = True
    if bot_member and message.author.top_role >= bot_member.top_role:
        can_moderate = False

    # 3. Kiểm tra giới hạn 80 từ
    effective_words = count_effective_words(message.content)
    if (
        effective_words > MAX_MESSAGE_WORDS
        and not LINK_PATTERN.search(message.content)
        and can_moderate
    ):
        await handle_long_message(message, effective_words)

    # 4. Ghi nhận và xóa tin nhắn nếu chế độ /on được bật ở CHAT_CHANNEL_ID
    if message.channel.id == CHAT_CHANNEL_ID and bot_enabled:
        if await record_and_delete_message(message):
            return

    # 5. Kiểm tra ID game Liên Quân ở CHAT_CHANNEL_ID
    if message.channel.id == CHAT_CHANNEL_ID:
        id_check_content = content_for_id_check(message.content)
        if ID_PATTERN.search(id_check_content) and can_moderate:
            user_id = str(message.author.id)
            violations[user_id] = violations.get(user_id, 0) + 1
            count = violations[user_id]
            mute_minutes = min(count * 5, MAX_TIMEOUT_MINUTES)
            save_violations_data()

            try:
                await message.delete()
            except Exception:
                pass

            try:
                await message.author.timeout(
                    timedelta(minutes=mute_minutes),
                    reason="Gửi ID game Liên Quân lên chat tổng",
                )
            except Exception:
                pass

            try:
                await message.author.send(
                    f"{message.author.display_name}, bạn đã bị cấm chat trong vòng {mute_minutes} phút do gửi ID game lên chat tổng."
                )
            except Exception:
                pass

            log_channel = bot.get_channel(ADMIN_LOG_CHANNEL_ID)
            if isinstance(log_channel, discord.TextChannel):
                embed = discord.Embed(
                    title="🚫 Thành viên bị mute", color=discord.Color.red()
                )
                embed.add_field(
                    name="👤 Người vi phạm",
                    value=f"{message.author.mention} (`{message.author}`)",
                    inline=False,
                )
                embed.add_field(
                    name="⏱️ Thời gian mute",
                    value=f"`{mute_minutes} phút`",
                    inline=True,
                )
                embed.add_field(
                    name="🔢 Lần vi phạm", value=f"`Lần {count}`", inline=True
                )
                await send_log_and_mention(
                    log_channel, embed, "log vi phạm ID"
                )

    # 6. Ghi log hoạt động nhắn tin của thành viên được theo dõi (/theodoi)
    content = message.content.strip() or "(Không có nội dung văn bản)"
    attachments_list = [
        f"• {att.filename} — {att.url}" for att in message.attachments
    ]
    description = (
        f"**Thành viên:** {message.author.mention}\n"
        f"**Kênh:** {message.channel.mention}\n"
        f"**Nội dung:**\n{content}\n"
        f"**Thời gian:** {timestamp()}"
    )
    if attachments_list:
        description += "\n**Tệp đính kèm:**\n" + "\n".join(attachments_list)

    await send_log(message.guild.id, message.author.id, "💬 Tin nhắn mới", description)
    await bot.process_commands(message)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.guild is None or after.author.bot:
        return
    if before.content == after.content and before.attachments == after.attachments:
        return
    await send_log(
        after.guild.id,
        after.author.id,
        "✏️ Tin nhắn được chỉnh sửa",
        f"**Thành viên:** {after.author.mention}\n**Kênh:** {after.channel.mention}\n**Trước:** {before.content or '(Trống)'}\n**Sau:** {after.content or '(Trống)'}",
    )


@bot.event
async def on_message_delete(message: discord.Message):
    if message.guild is None or message.author is None or message.author.bot:
        return
    if message.channel.id == CHAT_CHANNEL_ID and bot_enabled:
        return
    await send_log(
        message.guild.id,
        message.author.id,
        "🗑️ Tin nhắn bị xóa",
        f"**Thành viên:** {message.author.mention}\n**Kênh:** {message.channel.mention}",
    )


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    if bot.user and member.id == bot.user.id:
        target_id = voice_targets.get(member.guild.id)
        if target_id and (after.channel is None or after.channel.id != target_id):
            asyncio.create_task(reconnect_target_voice(member.guild, target_id))
        return

    if member.bot or before.channel == after.channel:
        return

    if before.channel is None and after.channel is not None:
        title, desc = "🔊 Vào phòng thoại", f"**Thành viên:** {member.mention}\n**Phòng:** {after.channel.name}"
    elif before.channel is not None and after.channel is None:
        title, desc = "🔇 Rời phòng thoại", f"**Thành viên:** {member.mention}\n**Phòng:** {before.channel.name}"
    else:
        title, desc = "🔄 Chuyển phòng", f"**Thành viên:** {member.mention}\n**Từ:** {before.channel.name} ➡️ **Đến:** {after.channel.name}"

    await send_log(member.guild.id, member.id, title, desc)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
):
    message = (
        "❌ Bạn không có quyền sử dụng lệnh này."
        if isinstance(error, app_commands.CheckFailure)
        else "❌ Lệnh xảy ra lỗi."
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except Exception:
        pass


@bot.event
async def on_ready():
    logger.info("Bot online: %s (%s)", bot.user, bot.user.id if bot.user else "unknown")


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Thiếu biến môi trường DISCORD_TOKEN.")
    bot.run(TOKEN)
