import asyncio
import json
import logging
import os
import re
import sqlite3
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands, tasks


# =========================================================
# CẤU HÌNH CHUNG
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

OWNER_ID = read_int_setting("OWNER_ID", 1356498767788118099)

# Đặt TEST_GUILD_ID=0 trong biến môi trường để đồng bộ lệnh toàn cục.
TEST_GUILD_ID = read_int_setting("TEST_GUILD_ID", 1495362331163754589)


# =========================================================
# ID KÊNH
# =========================================================

# Kênh chat tổng. Cũng là kênh nguồn: quét thống kê (/quet)
# và ghi nhận rồi xóa tin nhắn (/on).
SOURCE_CHANNEL_ID = 1495362331964870819
CHAT_CHANNEL_ID = SOURCE_CHANNEL_ID

IMAGE_REACTION_CHANNEL_ID = 1557292130039369798
RECORD_CHANNEL_ID = 1556550052309377064
REPORT_CHANNEL_ID = 1564912638272602142

# Kênh log kiểm duyệt (mute, tin dài, thao tác quản trị)
MOD_LOG_CHANNEL_ID = 1496564307306090526
REMINDER_CHANNEL_ID = 1502014155581030501
LLAUDON_CHANNEL_ID = 1541782500760879214

ALLOWED_LLAUDON_MESSAGES = {"llaudon", "Llaudon"}
LOG_MENTION = "<@1356498767788118099>"
LONG_MESSAGE_LOG_MENTION = (
    "<@1356498767788118099> "
    "<@&1534905534522724614> "
    "<@&1505079149113511987>"
)


# =========================================================
# THỜI GIAN QUÉT & FILE DỮ LIỆU
# =========================================================

START_DATE = date(2026, 4, 19)
END_DATE = date(2026, 10, 28)
REPORT_TIMEZONE = timezone.utc

DB_FILE = "tracking.sqlite3"
DATABASE_PATH = "message_stats.db"
DATA_FILE = Path("violations.json")


# =========================================================
# KIỂM DUYỆT: HẰNG SỐ & REGEX
# =========================================================

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


# =========================================================
# EMOJI REACTION
# =========================================================

IMAGE_REACTIONS = [
    discord.PartialEmoji(name="bucboi", id=1553391349045399722, animated=True),
    discord.PartialEmoji(name="lauhang", id=1552946293062766602, animated=True),
    discord.PartialEmoji(name="ahihi", id=1496351539550224425, animated=True),
    discord.PartialEmoji(name="gomo", id=1552946448688095262, animated=True),
    discord.PartialEmoji(name="TC_blinkblink", id=1500004350674735155, animated=True),
]


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s: %(message)s",
)
logger = logging.getLogger("combined_discord_bot")


# =========================================================
# DATABASE: THEO DÕI THÀNH VIÊN
# =========================================================

db = sqlite3.connect(DB_FILE)
db.execute("""
CREATE TABLE IF NOT EXISTS tracking (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    log_channel_id INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
)
""")
db.commit()


def get_tracking(guild_id: int, user_id: int):
    return db.execute(
        """
        SELECT log_channel_id
        FROM tracking
        WHERE guild_id = ? AND user_id = ?
        """,
        (guild_id, user_id),
    ).fetchone()


# =========================================================
# DATABASE: THỐNG KÊ TIN NHẮN
# =========================================================

def initialize_database():
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                channel_id INTEGER NOT NULL,
                report_date TEXT NOT NULL,
                message_count INTEGER NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (channel_id, report_date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                total_messages INTEGER,
                status TEXT NOT NULL
            )
        """)
        conn.commit()


def save_daily_stats(channel_id: int, day: date, count: int):
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("""
            INSERT INTO daily_stats (
                channel_id, report_date, message_count, completed
            )
            VALUES (?, ?, ?, 1)
            ON CONFLICT(channel_id, report_date)
            DO UPDATE SET
                message_count = excluded.message_count,
                completed = 1
        """, (channel_id, day.isoformat(), count))
        conn.commit()


def get_total_stats(channel_id: int):
    with sqlite3.connect(DATABASE_PATH) as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(message_count), 0)
            FROM daily_stats
            WHERE channel_id = ?
              AND completed = 1
              AND report_date >= ?
              AND report_date <= ?
        """, (
            channel_id,
            START_DATE.isoformat(),
            END_DATE.isoformat(),
        )).fetchone()
    return row[0] if row else 0


def create_scan_run():
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.execute("""
            INSERT INTO scan_runs (
                started_at, start_date, end_date, status
            )
            VALUES (?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            START_DATE.isoformat(),
            END_DATE.isoformat(),
            "running",
        ))
        conn.commit()
        return cursor.lastrowid


def finish_scan_run(run_id: int, total: int, status: str):
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.execute("""
            UPDATE scan_runs
            SET finished_at = ?,
                total_messages = ?,
                status = ?
            WHERE id = ?
        """, (
            datetime.now(timezone.utc).isoformat(),
            total,
            status,
            run_id,
        ))
        conn.commit()


# =========================================================
# DỮ LIỆU VI PHẠM (violations.json)
# =========================================================

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
        logger.warning("Không thể đọc violations.json; bắt đầu với dữ liệu trống.")
        return {}


violations = load_data()


def save_data() -> None:
    with DATA_FILE.open("w", encoding="utf-8") as file:
        json.dump(violations, file, indent=4, ensure_ascii=False)


# =========================================================
# TRẠNG THÁI BOT
# =========================================================

bot_enabled = False
voice_targets: dict[int, int] = {}
voice_locks: dict[int, asyncio.Lock] = {}

# Key: (guild_id, member_id) -> asyncio.Task
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


# =========================================================
# INTENTS & BOT
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


bot = CombinedBot(command_prefix="!", intents=intents)


# =========================================================
# KIỂM TRA QUYỀN
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
# HELPER CHUNG
# =========================================================

def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def day_bounds(day: date):
    start = datetime.combine(
        day, time.min, tzinfo=REPORT_TIMEZONE
    ).astimezone(timezone.utc)
    end = datetime.combine(
        day + timedelta(days=1), time.min, tzinfo=REPORT_TIMEZONE
    ).astimezone(timezone.utc)
    return start, end


async def count_messages(channel: discord.TextChannel, day: date) -> int:
    start, end = day_bounds(day)
    count = 0
    async for _ in channel.history(
        limit=None,
        after=start,
        before=end,
        oldest_first=True,
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
        content = content[len(chunk):].lstrip("\n")


async def safe_followup(interaction: discord.Interaction, content: str):
    """Gửi followup; bỏ qua nếu interaction đã hết hạn (15 phút)."""
    try:
        await interaction.followup.send(content, ephemeral=True)
    except discord.HTTPException:
        logger.warning("Không gửi được followup (interaction có thể đã hết hạn).")


async def get_channel_any(channel_id: int):
    channel = bot.get_channel(channel_id)
    if channel is not None:
        return channel
    try:
        return await bot.fetch_channel(channel_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


# =========================================================
# GỬI NHẬT KÝ THEO DÕI
# =========================================================

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
    channel = await get_channel_any(channel_id)
    if channel is None:
        logger.warning("Không truy cập được kênh log %s.", channel_id)
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


# =========================================================
# LOG KIỂM DUYỆT
# =========================================================

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
        logger.error("Bot không có quyền gửi %s.", error_label)
    except discord.HTTPException as error:
        logger.error("Lỗi gửi %s: %s", error_label, error)


async def log_admin_action(
    context: commands.Context,
    action: str,
    details: str,
) -> None:
    log_channel = bot.get_channel(MOD_LOG_CHANNEL_ID)
    if not isinstance(log_channel, discord.TextChannel):
        logger.warning("Không tìm thấy kênh log để ghi thao tác quản trị.")
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

    await send_log_and_mention(log_channel, embed, "log thao tác quản trị")


# =========================================================
# VOICE: BÁM PHÒNG
# =========================================================

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
            logger.warning("Không tìm thấy phòng voice %s.", channel_id)
            return False

        try:
            voice_client = guild.voice_client
            if voice_client is not None and voice_client.is_connected():
                if (
                    voice_client.channel is not None
                    and voice_client.channel.id == channel.id
                ):
                    return True
                await voice_client.move_to(channel)
                return True

            if voice_client is not None:
                try:
                    await voice_client.disconnect(force=True)
                except (discord.HTTPException, discord.ClientException):
                    logger.exception("Không thể dọn voice client cũ.")

            await channel.connect(reconnect=True, timeout=30.0)
            return True

        except (
            discord.Forbidden,
            discord.HTTPException,
            discord.ClientException,
            asyncio.TimeoutError,
        ):
            logger.exception("Không thể kết nối voice %s.", channel_id)
            return False


async def reconnect_target_voice(guild: discord.Guild, target_id: int):
    for attempt in range(1, 6):
        await asyncio.sleep(3)
        if voice_targets.get(guild.id) != target_id:
            return

        if await connect_to_target_voice(guild):
            logger.info("Đã kết nối lại voice tại server %s.", guild.id)
            return

        logger.warning(
            "Thử kết nối lại lần %s/5 tại server %s.", attempt, guild.id
        )


# =========================================================
# VOICE: HẸN GIỜ NGẮT (/treo)
# =========================================================

def parse_treo_time(value: str) -> int | None:
    """Đổi 30s / 30m / 2h / 1d thành số giây."""
    match = re.fullmatch(r"(\d+)\s*(s|m|h|d)", value.strip().lower())
    if not match:
        return None

    amount = int(match.group(1))
    multiplier = {"s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]
    seconds = amount * multiplier
    return seconds if seconds > 0 else None


async def treo_timer(
    guild_id: int,
    member_id: int,
    seconds: int,
    channel_id: int,
) -> None:
    """Chờ hết thời gian rồi ngắt thành viên khỏi voice."""
    key = (guild_id, member_id)
    current = asyncio.current_task()

    try:
        await asyncio.sleep(seconds)

        guild = bot.get_guild(guild_id)
        if guild is None:
            return

        member = guild.get_member(member_id)
        if member is None:
            return

        channel = bot.get_channel(channel_id)

        if member.voice is None:
            if isinstance(channel, discord.TextChannel):
                await channel.send(
                    f"⏰ Hết giờ của **{member.display_name}**, "
                    "nhưng người này hiện không ở voice room."
                )
            return

        room_name = member.voice.channel.name

        # Chỉ ngắt khỏi voice, không kick khỏi server.
        await member.move_to(None, reason="Hết thời gian /treo")

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
        logger.error("Lỗi Discord API khi thực hiện /treo: %s", error)
    finally:
        # Chỉ xóa nếu đây vẫn là timer hiện tại (tránh xóa nhầm timer mới).
        if voice_timers.get(key) is current:
            voice_timers.pop(key, None)


# =========================================================
# SLASH COMMANDS: CHAT & VOICE
# =========================================================

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

    if channel is None or not hasattr(channel, "send"):
        await interaction.followup.send("❌ Không tìm thấy kênh.", ephemeral=True)
        return

    try:
        await channel.send(
            content=noidung,
            allowed_mentions=discord.AllowedMentions.all(),
        )
        await interaction.followup.send("✅ Đã gửi tin nhắn.", ephemeral=True)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        logger.exception("Lỗi khi sử dụng /chat.")
        await interaction.followup.send(
            "❌ Không thể gửi tin nhắn. Hãy kiểm tra quyền bot.",
            ephemeral=True,
        )


@bot.tree.command(
    name="join",
    description="Cho bot vào phòng voice của bạn và bám phòng",
)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
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
    if guild is None:
        await interaction.followup.send(
            "❌ Lệnh chỉ sử dụng trong server.", ephemeral=True
        )
        return

    voice_targets[guild.id] = channel.id
    success = await connect_to_target_voice(guild)

    if not success:
        voice_targets.pop(guild.id, None)
        await interaction.followup.send(
            "❌ Bot không thể vào phòng. Kiểm tra quyền Connect và View Channel.",
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        f"🎙️ Bot đã vào **{channel.name}**.\n"
        "Bot sẽ thử kết nối lại nếu mất kết nối.\n"
        "Dùng `/leave` để tắt chế độ bám phòng.",
        ephemeral=True,
    )


@bot.tree.command(
    name="leave",
    description="Cho bot rời voice và tắt chế độ bám phòng",
)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@admin_only()
async def leave(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "❌ Lệnh chỉ sử dụng trong server.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)
    voice_targets.pop(guild.id, None)
    voice_client = guild.voice_client

    if voice_client is not None:
        try:
            await voice_client.disconnect(force=True)
        except (discord.HTTPException, discord.ClientException):
            logger.exception("Không thể rời phòng voice.")

    await interaction.followup.send(
        "👋 Bot đã rời phòng.\n🔓 Đã tắt chế độ tự động quay lại.",
        ephemeral=True,
    )


@bot.tree.command(
    name="treo",
    description="Hẹn giờ ngắt một người khỏi voice room",
)
@app_commands.guild_only()
@app_commands.describe(
    ten="Người cần hẹn giờ",
    thoi_gian="Ví dụ: 30s, 30m, 2h, 1d",
)
async def treo(
    interaction: discord.Interaction,
    ten: discord.Member,
    thoi_gian: str,
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "❌ Lệnh này chỉ dùng trong server.", ephemeral=True
        )
        return

    # Người có quyền Moderate Members được treo bất kỳ ai.
    # Người khác chỉ được treo chính mình.
    is_manager = interaction.user.guild_permissions.moderate_members
    if not is_manager and ten.id != interaction.user.id:
        await interaction.response.send_message(
            "❌ Bạn chỉ có thể dùng /treo cho chính mình. "
            "Người có quyền **Moderate Members (Timeout thành viên)** "
            "mới có thể treo người khác.",
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

    bot_member = guild.me
    if bot_member is None:
        await interaction.response.send_message(
            "❌ Không xác định được thành viên bot trong server.",
            ephemeral=True,
        )
        return

    if not bot_member.guild_permissions.move_members:
        await interaction.response.send_message(
            "❌ Bot chưa có quyền **Move Members**.", ephemeral=True
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

    key = (guild.id, ten.id)

    # Nếu đã có timer cũ thì thay bằng timer mới.
    old_task = voice_timers.get(key)
    if old_task is not None:
        old_task.cancel()

    voice_timers[key] = asyncio.create_task(
        treo_timer(guild.id, ten.id, seconds, interaction.channel_id)
    )

    await interaction.response.send_message(
        f"⏰ Đã treo **{ten.display_name}**.\n"
        f"🎧 Room hiện tại: **{ten.voice.channel.name}**\n"
        f"⌛ Thời gian: **{thoi_gian}**\n"
        "➡️ Hết giờ bot sẽ ngắt người này khỏi voice room.",
        ephemeral=True,
    )


@bot.tree.command(name="huytreo", description="Hủy hẹn giờ ngắt khỏi voice")
@app_commands.guild_only()
@app_commands.describe(ten="Người cần hủy hẹn giờ")
async def huytreo(
    interaction: discord.Interaction,
    ten: discord.Member,
) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "❌ Lệnh này chỉ dùng trong server.", ephemeral=True
        )
        return

    is_manager = interaction.user.guild_permissions.moderate_members
    if not is_manager and ten.id != interaction.user.id:
        await interaction.response.send_message(
            "❌ Bạn chỉ có thể dùng /huytreo cho chính mình. "
            "Người có quyền **Moderate Members (Timeout thành viên)** "
            "mới có thể hủy cho người khác.",
            ephemeral=True,
        )
        return

    task = voice_timers.pop((guild.id, ten.id), None)
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


# =========================================================
# SLASH COMMANDS: BẬT/TẮT GHI NHẬN
# =========================================================

@bot.tree.command(
    name="on",
    description="Bật ghi nhận và xóa tin nhắn tại kênh nguồn",
)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@owner_admin_only()
async def turn_on(interaction: discord.Interaction):
    global bot_enabled
    bot_enabled = True
    await interaction.response.send_message(
        "✅ Đã bật chức năng ghi nhận và xóa tin nhắn.", ephemeral=True
    )


@bot.tree.command(
    name="off",
    description="Tắt ghi nhận và xóa tin nhắn tại kênh nguồn",
)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@owner_admin_only()
async def turn_off(interaction: discord.Interaction):
    global bot_enabled
    bot_enabled = False
    await interaction.response.send_message(
        "🛑 Đã tắt chức năng ghi nhận và xóa tin nhắn.", ephemeral=True
    )


# =========================================================
# SLASH COMMANDS: THEO DÕI THÀNH VIÊN
# =========================================================

@bot.tree.command(
    name="theodoi",
    description="Bắt đầu ghi nhật ký hoạt động của thành viên",
)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    user="Thành viên được ghi nhật ký",
    log="Kênh nhận nhật ký",
)
@admin_only()
async def theodoi(
    interaction: discord.Interaction,
    user: discord.Member,
    log: discord.TextChannel,
):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "❌ Lệnh chỉ sử dụng trong server.", ephemeral=True
        )
        return

    if user.bot:
        await interaction.response.send_message(
            "❌ Không thể theo dõi tài khoản bot.", ephemeral=True
        )
        return

    if log.guild.id != guild.id:
        await interaction.response.send_message(
            "❌ Kênh nhật ký phải thuộc server này.", ephemeral=True
        )
        return

    bot_member = guild.me
    if bot_member is None:
        await interaction.response.send_message(
            "❌ Không xác định được thành viên bot.", ephemeral=True
        )
        return

    permissions = log.permissions_for(bot_member)
    if not permissions.view_channel or not permissions.send_messages:
        await interaction.response.send_message(
            "❌ Bot cần quyền xem kênh và gửi tin nhắn trong kênh nhật ký.",
            ephemeral=True,
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
        f"✅ Đã bật ghi nhật ký cho {user.mention} tại {log.mention}.\n"
        "Hãy thông báo cho thành viên rằng hoạt động của họ đang được ghi nhận.",
        ephemeral=True,
    )

    await send_log(
        guild.id,
        user.id,
        "📋 Bắt đầu ghi nhật ký",
        f"Thành viên: {user.mention}\n"
        f"Người thiết lập: {interaction.user.mention}\n"
        f"Kênh nhật ký: {log.mention}",
    )


@bot.tree.command(
    name="dungtheodoi",
    description="Dừng ghi nhật ký của một thành viên",
)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(user="Thành viên cần dừng ghi nhật ký")
@admin_only()
async def dungtheodoi(
    interaction: discord.Interaction,
    user: discord.Member,
):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "❌ Lệnh chỉ sử dụng trong server.", ephemeral=True
        )
        return

    row = get_tracking(guild.id, user.id)
    if not row:
        await interaction.response.send_message(
            "Thành viên này không nằm trong danh sách theo dõi.",
            ephemeral=True,
        )
        return

    channel_id = row[0]
    db.execute(
        "DELETE FROM tracking WHERE guild_id = ? AND user_id = ?",
        (guild.id, user.id),
    )
    db.commit()

    await interaction.response.send_message(
        f"✅ Đã dừng ghi nhật ký cho {user.mention}.", ephemeral=True
    )

    channel = await get_channel_any(channel_id)
    if channel is not None:
        try:
            await channel.send(
                embed=discord.Embed(
                    title="⏹️ Đã dừng ghi nhật ký",
                    description=f"Thành viên: {user.mention}",
                    color=discord.Color.orange(),
                    timestamp=datetime.now(timezone.utc),
                )
            )
        except discord.HTTPException:
            logger.exception("Không gửi được thông báo dừng theo dõi.")


@bot.tree.command(
    name="danhsachtheodoi",
    description="Xem danh sách thành viên đang được ghi nhật ký",
)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@admin_only()
async def danhsachtheodoi(interaction: discord.Interaction):
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message(
            "❌ Lệnh chỉ sử dụng trong server.", ephemeral=True
        )
        return

    rows = db.execute(
        """
        SELECT user_id, log_channel_id
        FROM tracking
        WHERE guild_id = ?
        ORDER BY user_id
        """,
        (guild.id,),
    ).fetchall()

    if not rows:
        await interaction.response.send_message(
            "Hiện không có thành viên nào được ghi nhật ký.",
            ephemeral=True,
        )
        return

    lines = [f"• <@{user_id}> → <#{channel_id}>" for user_id, channel_id in rows]

    embed = discord.Embed(
        title="📋 Danh sách ghi nhật ký",
        description="\n".join(lines)[:4000],
        color=discord.Color.blurple(),
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


# =========================================================
# SLASH COMMAND: QUÉT THỐNG KÊ
# =========================================================

@bot.tree.command(
    name="quet",
    description="Quét thống kê tin nhắn theo từng ngày trong khoảng thời gian định sẵn",
)
@app_commands.guild_only()
@owner_only()
async def quet(interaction: discord.Interaction):
    if scan_lock.locked():
        await interaction.response.send_message(
            "Bot đang quét dữ liệu. Vui lòng đợi lần quét hiện tại hoàn tất.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)

    async with scan_lock:
        source_channel = await get_channel_any(SOURCE_CHANNEL_ID)
        if source_channel is None:
            await safe_followup(
                interaction,
                "Không tìm thấy kênh chat nguồn. Hãy kiểm tra ID và quyền của bot.",
            )
            return

        report_channel = await get_channel_any(REPORT_CHANNEL_ID)
        if report_channel is None:
            await safe_followup(
                interaction,
                "Không tìm thấy kênh lưu kết quả báo cáo. Hãy kiểm tra ID và quyền của bot.",
            )
            return

        if not isinstance(source_channel, discord.TextChannel):
            await safe_followup(
                interaction, "Kênh cần quét không phải kênh văn bản thông thường."
            )
            return

        if not isinstance(report_channel, discord.TextChannel):
            await safe_followup(
                interaction, "Kênh lưu kết quả không phải kênh văn bản thông thường."
            )
            return

        scan_progress["running"] = True
        scan_progress["current"] = 0
        scan_progress["current_date"] = None

        run_id = create_scan_run()
        total_days = scan_progress["total_days"]
        total_messages = 0
        completed_days = 0

        await send_report(
            report_channel,
            (
                "🔎 **BẮT ĐẦU QUÉT TIN NHẮN**\n"
                f"📍 Kênh: <#{SOURCE_CHANNEL_ID}>\n"
                f"📅 Từ: {START_DATE.strftime('%d/%m/%Y')}\n"
                f"📅 Đến: {END_DATE.strftime('%d/%m/%Y')}\n"
                f"📊 Tổng số ngày: {total_days}\n"
                "━━━━━━━━━━━━━━━━━━━━"
            ),
        )

        try:
            day = START_DATE
            while day <= END_DATE:
                completed_days += 1
                scan_progress["current"] = completed_days
                scan_progress["current_date"] = day.isoformat()

                try:
                    count = await count_messages(source_channel, day)
                    save_daily_stats(SOURCE_CHANNEL_ID, day, count)
                    total_messages += count

                    await send_report(
                        report_channel,
                        (
                            f"📅 **Ngày {completed_days}/{total_days}**\n"
                            f"🗓️ Ngày: {day.strftime('%d/%m/%Y')}\n"
                            f"💬 Số tin nhắn: **{count:,}**"
                        ),
                    )

                    logger.info(
                        "Progress %s/%s | %s | %s messages",
                        completed_days,
                        total_days,
                        day.isoformat(),
                        count,
                    )

                except discord.Forbidden:
                    raise RuntimeError(
                        "Bot không có quyền đọc lịch sử tin nhắn của kênh chat."
                    )
                except discord.HTTPException as exc:
                    raise RuntimeError(
                        f"Lỗi Discord khi quét ngày {day.isoformat()}: {exc}"
                    ) from exc

                day += timedelta(days=1)

            total_messages = get_total_stats(SOURCE_CHANNEL_ID)
            finish_scan_run(run_id, total_messages, "completed")

            await send_report(
                report_channel,
                (
                    "━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ **HOÀN TẤT QUÉT DỮ LIỆU**\n"
                    f"📅 Khoảng thời gian: {START_DATE.strftime('%d/%m/%Y')} – {END_DATE.strftime('%d/%m/%Y')}\n"
                    f"📆 Số ngày đã quét: **{completed_days}/{total_days}**\n"
                    f"💬 Tổng số tin nhắn: **{total_messages:,}**\n"
                    "💾 Dữ liệu đã được lưu vào SQLite."
                ),
            )

            await safe_followup(
                interaction,
                "✅ Đã quét xong toàn bộ dữ liệu.\n"
                f"📆 Số ngày: {completed_days}/{total_days}\n"
                f"💬 Tổng số tin nhắn: {total_messages:,}\n"
                f"📁 Kết quả đã gửi vào <#{REPORT_CHANNEL_ID}>.",
            )

        except Exception as exc:
            logger.exception("Scan failed")
            finish_scan_run(run_id, get_total_stats(SOURCE_CHANNEL_ID), "failed")
            try:
                await send_report(
                    report_channel,
                    (
                        "⚠️ **QUÉT BỊ GIÁN ĐOẠN**\n"
                        f"📊 Tiến độ: {completed_days}/{total_days}\n"
                        f"📅 Ngày đang xử lý: {scan_progress['current_date'] or 'Chưa xác định'}\n"
                        f"❗ Lỗi: {str(exc)[:1000]}\n"
                        "💾 Các ngày đã hoàn tất vẫn được lưu trong SQLite."
                    ),
                )
            except Exception:
                logger.exception("Could not send failure report")

            await safe_followup(
                interaction,
                "⚠️ Quá trình quét bị gián đoạn.\n"
                f"Tiến độ: {completed_days}/{total_days}\n"
                f"Lỗi: {str(exc)[:1500]}",
            )
        finally:
            scan_progress["running"] = False
            scan_progress["current_date"] = None


# =========================================================
# PREFIX COMMANDS: RESET VI PHẠM
# =========================================================

@bot.command(name="reset_violation")
@commands.has_guild_permissions(manage_guild=True)
async def reset_violation(
    context: commands.Context,
    member: discord.Member,
) -> None:
    """Đặt lại số lần vi phạm của một thành viên."""
    previous_count = violations.pop(str(member.id), 0)
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
        logger.error("Lỗi lệnh reset_violation: %s", error)


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
        logger.error("Lỗi lệnh reset_all: %s", error)


@bot.event
async def on_command_error(
    context: commands.Context,
    error: commands.CommandError,
) -> None:
    # Bỏ qua tin bắt đầu bằng "!" nhưng không phải lệnh, và lệnh đã có handler riêng.
    if isinstance(error, commands.CommandNotFound):
        return
    if context.command is not None and context.command.has_error_handler():
        return
    logger.error("Lỗi lệnh prefix: %s", error)


# =========================================================
# NHẬC NHỞ KÊNH TÌM BẠN CHƠI
# =========================================================

@tasks.loop(minutes=2)
async def reminder_loop() -> None:
    """Giữ đúng một tin nhắc ở cuối kênh tìm bạn chơi."""
    channel = bot.get_channel(REMINDER_CHANNEL_ID)
    if not isinstance(channel, discord.TextChannel):
        logger.warning(
            "Không tìm thấy kênh nhắc nhở hoặc bot không truy cập được kênh %s.",
            REMINDER_CHANNEL_ID,
        )
        return

    try:
        recent = [message async for message in channel.history(limit=50)]
    except discord.Forbidden:
        logger.error("Bot không có quyền đọc lịch sử kênh nhắc nhở.")
        return
    except discord.HTTPException as error:
        logger.error("Lỗi đọc lịch sử kênh nhắc nhở: %s", error)
        return

    # Nếu tin nhắc đã là tin cuối cùng của kênh thì không cần gửi lại.
    if (
        recent
        and recent[0].author.id == bot.user.id
        and recent[0].content == REMINDER_TEXT
    ):
        return

    # Xóa các tin nhắc cũ của bot (kể cả tin tồn tại trước khi bot restart).
    for message in recent:
        if message.author.id == bot.user.id and message.content == REMINDER_TEXT:
            try:
                await message.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                logger.error("Bot không có quyền xóa tin nhắc cũ.")
                break

    try:
        await channel.send(REMINDER_TEXT)
    except discord.Forbidden:
        logger.error("Bot không có quyền gửi tin nhắc vào kênh nhắc nhở.")
    except discord.HTTPException as error:
        logger.error("Lỗi gửi tin nhắc: %s", error)


@reminder_loop.before_loop
async def before_reminder_loop() -> None:
    await bot.wait_until_ready()


# =========================================================
# KIỂM DUYỆT: ĐẾM TỪ, EMOJI, ID
# =========================================================

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
    content = MENTION_PATTERN.sub("", content)
    content = CUSTOM_EMOJI_PATTERN.sub("", content)
    text = "".join(c for c in content if not is_emoji_character(c))
    return len(text.split())


def content_for_id_check(content: str) -> str:
    """Bỏ mã custom emoji và keycap emoji trước khi tìm ID 6 chữ số."""
    without_custom_emojis = CUSTOM_EMOJI_PATTERN.sub("", content)
    return KEYCAP_EMOJI_PATTERN.sub("", without_custom_emojis)


async def handle_long_message(
    message: discord.Message,
    effective_length: int,
) -> None:
    """Xóa, timeout và ghi log tin nhắn vượt quá giới hạn số từ."""
    try:
        await message.delete()
    except discord.Forbidden:
        logger.error("Bot không có quyền xóa tin nhắn dài.")
    except discord.NotFound:
        pass

    try:
        await message.author.timeout(
            timedelta(minutes=5),
            reason="Gửi tin nhắn vượt quá 80 từ",
        )
    except discord.Forbidden:
        logger.error(
            "Không thể timeout %s vì tin nhắn dài. "
            "Kiểm tra quyền Moderate Members và role của bot.",
            message.author,
        )
    except discord.HTTPException as error:
        logger.error("Lỗi timeout tin nhắn dài: %s", error)

    try:
        await message.author.send(LONG_MESSAGE_DM)
    except discord.Forbidden:
        logger.warning("Không thể gửi DM vi phạm giới hạn cho %s.", message.author)
    except discord.HTTPException as error:
        logger.error("Lỗi gửi DM vi phạm giới hạn: %s", error)

    log_channel = await get_channel_any(MOD_LOG_CHANNEL_ID)
    if not isinstance(log_channel, discord.TextChannel):
        logger.warning("Không lấy được kênh log tin nhắn dài.")
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
    embed.add_field(name="Kênh", value=message.channel.mention, inline=True)
    embed.add_field(
        name="Xử lý",
        value="Timeout 5 phút & xóa tin nhắn",
        inline=False,
    )
    embed.add_field(
        name="Nội dung vi phạm",
        value=message.content[:1024] or "(Trống)",
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


async def handle_game_id_violation(message: discord.Message) -> None:
    """Xử lý tin nhắn chứa ID game 6 số ở kênh chat tổng."""
    user_id = str(message.author.id)
    violations[user_id] = violations.get(user_id, 0) + 1
    count = violations[user_id]

    mute_minutes = min(count * 5, MAX_TIMEOUT_MINUTES)
    save_data()

    try:
        await message.delete()
    except discord.Forbidden:
        logger.error("Bot không có quyền xóa tin nhắn.")
    except discord.NotFound:
        pass

    try:
        await message.author.timeout(
            timedelta(minutes=mute_minutes),
            reason="Gửi ID game Liên Quân lên chat tổng",
        )
    except discord.Forbidden:
        logger.error(
            "Không thể mute %s. Kiểm tra quyền Moderate Members và role của bot.",
            message.author,
        )
    except discord.HTTPException as error:
        logger.error("Lỗi Discord API khi timeout: %s", error)

    text = (
        f"{message.author.display_name}, bạn đã bị cấm chat "
        f"trong vòng {mute_minutes} phút. "
        "Lý do: gửi ID game lên chat tổng."
    )

    try:
        await message.author.send(text)
    except discord.Forbidden:
        logger.warning(
            "Không thể gửi DM cho %s. Có thể người dùng đã tắt DM.",
            message.author,
        )
    except discord.HTTPException as error:
        logger.error("Lỗi gửi DM: %s", error)

    log_channel = bot.get_channel(MOD_LOG_CHANNEL_ID)
    if not isinstance(log_channel, discord.TextChannel):
        return

    embed = discord.Embed(title="🚫 Thành viên bị mute", color=discord.Color.red())
    embed.add_field(
        name="👤 Người vi phạm",
        value=(
            f"{message.author.mention}\n"
            f"`{message.author}`\n"
            f"ID: `{message.author.id}`"
        ),
        inline=False,
    )
    embed.add_field(name="⏱️ Thời gian mute", value=f"`{mute_minutes} phút`", inline=True)
    embed.add_field(name="🔢 Lần vi phạm", value=f"`Lần {count}`", inline=True)
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

    await send_log_and_mention(log_channel, embed, "log vi phạm ID")


async def moderate_message(message: discord.Message) -> bool:
    """
    Kiểm duyệt tin nhắn. Trả về True nếu tin nhắn đã bị xử lý (xóa).
    Mỗi tin nhắn chỉ bị xử phạt tối đa một lần.
    """
    guild = message.guild
    if guild is None or not isinstance(message.author, discord.Member):
        return False

    bot_member = guild.me
    if bot_member is None:
        try:
            bot_member = await guild.fetch_member(bot.user.id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as error:
            logger.error("Không thể xác định role cao nhất của bot: %s", error)
            return False

    # Discord không cho bot kiểm duyệt thành viên có top role ngang/cao hơn bot.
    if message.author.top_role >= bot_member.top_role:
        return False

    # Kênh Llaudon chỉ cho phép đúng hai cách viết này.
    if (
        message.channel.id == LLAUDON_CHANNEL_ID
        and message.content not in ALLOWED_LLAUDON_MESSAGES
    ):
        try:
            await message.delete()
        except discord.Forbidden:
            logger.error("Bot không có quyền xóa tin nhắn trong kênh Llaudon.")
            return False
        except discord.NotFound:
            pass
        return True

    # Giới hạn số từ (tin có link được bỏ qua).
    effective_words = count_effective_words(message.content)
    if effective_words > MAX_MESSAGE_WORDS and not LINK_PATTERN.search(message.content):
        await handle_long_message(message, effective_words)
        return True

    # ID game 6 số ở kênh chat tổng.
    if message.channel.id == CHAT_CHANNEL_ID:
        if ID_PATTERN.search(content_for_id_check(message.content)):
            await handle_game_id_violation(message)
            return True

    return False


# =========================================================
# NHẬN DIỆN ẢNH & REACTION
# =========================================================

def message_has_image(message: discord.Message) -> bool:
    image_extensions = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
    for attachment in message.attachments:
        if attachment.filename.lower().endswith(image_extensions):
            return True
        if attachment.content_type and attachment.content_type.startswith("image/"):
            return True
    for embed in message.embeds:
        if embed.image and embed.image.url:
            return True
        if embed.thumbnail and embed.thumbnail.url:
            return True
    return False


async def react_to_image(message: discord.Message):
    if message.channel.id != IMAGE_REACTION_CHANNEL_ID:
        return
    if message.author.bot:
        return
    if not message_has_image(message):
        return

    for emoji in IMAGE_REACTIONS:
        try:
            await message.add_reaction(emoji)
        except discord.Forbidden:
            logger.error("Bot thiếu quyền reaction hoặc không dùng được emoji %s.", emoji)
        except discord.NotFound:
            logger.warning("Tin nhắn hoặc emoji không tồn tại: %s.", emoji)
        except discord.HTTPException:
            logger.exception("Không thể reaction tin nhắn %s.", message.id)


# =========================================================
# GHI NHẬN TIN NHẮN RỒI XÓA (/on)
# =========================================================

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
        name="🏠 Máy chủ",
        value=f"{message.guild.name}\nID: `{message.guild.id}`",
        inline=False,
    )
    embed.add_field(
        name="📍 Kênh gốc",
        value=f"<#{message.channel.id}>",
        inline=False,
    )
    embed.add_field(name="💬 Nội dung", value=content[:1024], inline=False)

    if len(content) > 1024:
        embed.add_field(
            name="⚠️ Lưu ý",
            value="Nội dung dài đã bị cắt bớt trong embed.",
            inline=False,
        )

    if message.attachments:
        attachments_text = "\n".join(
            f"[{attachment.filename}]({attachment.url})"
            for attachment in message.attachments
        )
        embed.add_field(
            name="📎 Tệp đính kèm",
            value=attachments_text[:1024],
            inline=False,
        )

    embed.set_footer(text=f"Message ID: {message.id}")

    try:
        record_channel = await get_channel_any(RECORD_CHANNEL_ID)
        if record_channel is None:
            logger.error("Không tìm thấy kênh ghi nhận. Tin nhắn gốc được giữ lại.")
            return False
        await record_channel.send(embed=embed)
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        logger.exception("Không gửi được bản ghi. Tin nhắn gốc được giữ lại.")
        return False

    try:
        await message.delete()
        logger.info("Đã ghi nhận và xóa tin nhắn %s của %s.", message.id, message.author)
        return True
    except (discord.Forbidden, discord.NotFound, discord.HTTPException):
        logger.exception("Không thể xóa tin nhắn %s.", message.id)
        return False


# =========================================================
# EVENTS: TIN NHẮN
# =========================================================

async def log_tracked_message(message: discord.Message) -> None:
    content = message.content.strip() or "(Không có nội dung văn bản)"
    attachments = [
        f"• {attachment.filename} — {attachment.url}"
        for attachment in message.attachments
    ]

    description = (
        f"**Thành viên:** {message.author.mention}\n"
        f"**Kênh:** {message.channel.mention}\n"
        f"**Nội dung:**\n{content}\n"
        f"**Thời gian:** {timestamp()}"
    )
    if attachments:
        description += "\n**Tệp đính kèm:**\n" + "\n".join(attachments)

    await send_log(
        message.guild.id,
        message.author.id,
        "💬 Tin nhắn mới",
        description,
    )


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Tin nhắn riêng: chỉ xử lý lệnh prefix.
    if message.guild is None:
        await bot.process_commands(message)
        return

    # 1. Ghi nhật ký theo dõi (nếu thành viên đang được theo dõi).
    await log_tracked_message(message)

    # 2. Kiểm duyệt: Llaudon, tin dài, ID game.
    deleted = await moderate_message(message)

    # 3. Tự reaction ảnh.
    if not deleted:
        await react_to_image(message)

    # 4. Ghi nhận rồi xóa tin ở kênh nguồn khi /on bật.
    if not deleted and message.channel.id == SOURCE_CHANNEL_ID and bot_enabled:
        deleted = await record_and_delete_message(message)

    # 5. Lệnh prefix (!reset_violation, !reset_all).
    if not deleted:
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
        f"**Thành viên:** {after.author.mention}\n"
        f"**Kênh:** {after.channel.mention}\n"
        f"**Nội dung trước:**\n{before.content or '(Trống)'}\n"
        f"**Nội dung sau:**\n{after.content or '(Trống)'}",
    )


@bot.event
async def on_message_delete(message: discord.Message):
    if message.guild is None or message.author is None or message.author.bot:
        return

    # Tin bị xóa bởi chức năng /on đã có bản ghi riêng.
    if message.channel.id == SOURCE_CHANNEL_ID and bot_enabled:
        return

    await send_log(
        message.guild.id,
        message.author.id,
        "🗑️ Tin nhắn bị xóa",
        f"**Thành viên:** {message.author.mention}\n"
        f"**Kênh:** {message.channel.mention}\n"
        f"**Nội dung đã ghi nhận:**\n{message.content or '(Không có nội dung trong cache)'}",
    )


# =========================================================
# EVENT: VOICE STATE UPDATE
# =========================================================

@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    # Chính bot bị rớt/di chuyển khỏi phòng bám.
    if bot.user is not None and member.id == bot.user.id:
        target_id = voice_targets.get(member.guild.id)
        if target_id is not None:
            current_channel = after.channel
            if current_channel is None or current_channel.id != target_id:
                asyncio.create_task(
                    reconnect_target_voice(member.guild, target_id)
                )
        return

    if member.bot or before.channel == after.channel:
        return

    if before.channel is None and after.channel is not None:
        title = "🔊 Vào phòng thoại"
        description = f"**Thành viên:** {member.mention}\n**Phòng:** {after.channel.name}"
    elif before.channel is not None and after.channel is None:
        title = "🔇 Rời phòng thoại"
        description = f"**Thành viên:** {member.mention}\n**Phòng:** {before.channel.name}"
    else:
        title = "🔄 Chuyển phòng thoại"
        description = (
            f"**Thành viên:** {member.mention}\n"
            f"**Phòng cũ:** {before.channel.name}\n"
            f"**Phòng mới:** {after.channel.name}"
        )

    await send_log(member.guild.id, member.id, title, description)

    no_ping = discord.AllowedMentions.none()

    if before.channel is not None:
        try:
            await before.channel.send(
                f"{member.mention} vừa rời khỏi kênh thoại",
                allowed_mentions=no_ping,
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            logger.exception("Không thông báo được người rời phòng.")

    if after.channel is not None:
        try:
            await after.channel.send(
                f"{member.mention} vừa vào trong room",
                allowed_mentions=no_ping,
            )
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            logger.exception("Không thông báo được người vào phòng.")


# =========================================================
# ERROR HANDLER (SLASH COMMAND)
# =========================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    if isinstance(error, app_commands.CheckFailure):
        message = "❌ Bạn không có quyền sử dụng lệnh này."
    else:
        logger.error(
            "Lỗi slash command.",
            exc_info=(type(error), error, error.__traceback__),
        )
        message = "❌ Lệnh xảy ra lỗi. Hãy kiểm tra log bot."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        logger.exception("Không gửi được thông báo lỗi.")


# =========================================================
# ON READY
# =========================================================

@bot.event
async def on_ready():
    logger.info(
        "Bot online: %s (%s)",
        bot.user,
        bot.user.id if bot.user else "unknown",
    )


# =========================================================
# KHỞI ĐỘNG
# =========================================================

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("Thiếu DISCORD_TOKEN trong Environment Variables.")
    bot.run(TOKEN)
