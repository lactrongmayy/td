
import os
import re
import asyncio
import logging
import sqlite3
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord import app_commands

# =========================================================
# CONFIG
# =========================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()

OWNER_ID = 1356498767788118099
SOURCE_GUILD_ID = 1495362331163754589

STORAGE_GUILD_IDS = [
    1562337763804844087,
    1562338023151108118,
    1562338303129424003,
    1562338556163129346,
    1562338815815712810,
    1562339088428965929,
    1562339341974380564,
    1562339598632489000,
    1562339890610569236,
    1562340146865508443,
]

CHANNEL_LIMIT = 450
DB_PATH = "members.sqlite3"
SCAN_DELAY = 0.15

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("member-manager")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
db_lock = asyncio.Lock()
channel_lock = asyncio.Lock()
initialized = False


# =========================================================
# DATABASE
# =========================================================

db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

db.execute("""
CREATE TABLE IF NOT EXISTS members (
    user_id INTEGER PRIMARY KEY,
    stt INTEGER NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    username TEXT NOT NULL,
    storage_guild_id INTEGER,
    channel_id INTEGER,
    is_member INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS message_logs (
    source_message_id INTEGER PRIMARY KEY,
    target_channel_id INTEGER NOT NULL,
    target_message_id INTEGER NOT NULL
)
""")
db.commit()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_member_row(user_id: int):
    return db.execute(
        "SELECT * FROM members WHERE user_id = ?",
        (user_id,),
    ).fetchone()


def get_next_stt():
    row = db.execute(
        "SELECT COALESCE(MAX(stt), 0) + 1 AS next_stt FROM members"
    ).fetchone()
    return int(row["next_stt"])


def save_member(member: discord.Member):
    """Save by Discord ID. Existing members keep their STT."""
    current = get_member_row(member.id)
    timestamp = now_iso()

    if current:
        db.execute("""
            UPDATE members
            SET display_name = ?,
                username = ?,
                is_member = 1,
                updated_at = ?
            WHERE user_id = ?
        """, (
            member.display_name[:100],
            member.name[:100],
            timestamp,
            member.id,
        ))
    else:
        stt = get_next_stt()
        db.execute("""
            INSERT INTO members (
                user_id, stt, display_name, username,
                storage_guild_id, channel_id, is_member,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, NULL, NULL, 1, ?, ?)
        """, (
            member.id,
            stt,
            member.display_name[:100],
            member.name[:100],
            timestamp,
            timestamp,
        ))

    db.commit()
    return get_member_row(member.id)


def set_channel(user_id: int, guild_id: int, channel_id: int):
    db.execute("""
        UPDATE members
        SET storage_guild_id = ?, channel_id = ?, updated_at = ?
        WHERE user_id = ?
    """, (guild_id, channel_id, now_iso(), user_id))
    db.commit()


def set_member_status(user_id: int, is_member: bool):
    db.execute("""
        UPDATE members
        SET is_member = ?, updated_at = ?
        WHERE user_id = ?
    """, (1 if is_member else 0, now_iso(), user_id))
    db.commit()


def remember_message(source_id: int, channel_id: int, target_id: int):
    db.execute("""
        INSERT OR REPLACE INTO message_logs
        (source_message_id, target_channel_id, target_message_id)
        VALUES (?, ?, ?)
    """, (source_id, channel_id, target_id))
    db.commit()


def get_message_log(source_id: int):
    return db.execute(
        "SELECT * FROM message_logs WHERE source_message_id = ?",
        (source_id,),
    ).fetchone()


# =========================================================
# CHANNEL HELPERS
# =========================================================

def safe_channel_part(text: str, limit: int = 35):
    text = re.sub(r"[/\\#:@`*<>|]", "-", text or "")
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-+", "-", text)
    return (text or "unknown")[:limit].strip("-") or "unknown"


def make_channel_name(row):
    status = "✅" if row["is_member"] else "🛑"
    name = safe_channel_part(row["display_name"], 35)
    # Discord channel names cannot contain slash characters.
    return f'{row["stt"]}-{name}-{row["user_id"]}-{status}'[:100]


def get_storage_guilds():
    result = []
    for guild_id in STORAGE_GUILD_IDS:
        guild = bot.get_guild(guild_id)
        if guild is not None:
            result.append(guild)
    return result


async def choose_storage_guild():
    """Choose first available storage server under the channel limit."""
    for guild_id in STORAGE_GUILD_IDS:
        guild = bot.get_guild(guild_id)
        if guild is None:
            continue

        # Count all channels conservatively.
        if len(guild.channels) < CHANNEL_LIMIT:
            return guild

    return None


async def find_existing_channel(user_id: int):
    """Try saved channel ID first, then search by unique user ID."""
    row = get_member_row(user_id)

    if row and row["channel_id"]:
        channel = bot.get_channel(int(row["channel_id"]))
        if isinstance(channel, discord.TextChannel):
            return channel

    marker = f"-{user_id}-"
    for guild in get_storage_guilds():
        for channel in guild.text_channels:
            if marker in f"-{channel.name}-":
                return channel

    return None


async def create_member_channel(row):
    guild = await choose_storage_guild()
    if guild is None:
        log.error(
            "No available storage server for user %s. "
            "Check bot membership and channel limits.",
            row["user_id"],
        )
        return None

    bot_member = guild.me
    if bot_member is None:
        return None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=False
        ),
        bot_member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
            manage_channels=True,
        ),
    }

    owner = guild.get_member(OWNER_ID)
    if owner is not None:
        overwrites[owner] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
        )

    channel = await guild.create_text_channel(
        name=make_channel_name(row),
        overwrites=overwrites,
        reason=f"Member tracking: Discord ID {row['user_id']}",
        topic=f"member_id={row['user_id']} | stt={row['stt']}",
    )

    set_channel(row["user_id"], guild.id, channel.id)
    log.info(
        "Created channel for user=%s stt=%s guild=%s channel=%s",
        row["user_id"], row["stt"], guild.id, channel.id,
    )
    return channel


async def ensure_member_channel(member: discord.Member):
    async with channel_lock:
        row = save_member(member)
        channel = await find_existing_channel(member.id)

        if channel is None:
            channel = await create_member_channel(row)
        else:
            # Save the actual channel location if it was found by scanning.
            set_channel(member.id, channel.guild.id, channel.id)

        if channel is not None:
            latest = get_member_row(member.id)
            desired_name = make_channel_name(latest)

            if channel.name != desired_name:
                try:
                    await channel.edit(
                        name=desired_name,
                        reason="Update member name/status",
                    )
                except discord.HTTPException:
                    log.exception("Could not rename channel %s", channel.id)

        return channel


async def update_channel_status(user_id: int, is_member: bool):
    row = get_member_row(user_id)
    if row is None:
        return

    set_member_status(user_id, is_member)
    row = get_member_row(user_id)

    channel = await find_existing_channel(user_id)
    if channel is None:
        return

    set_channel(user_id, channel.guild.id, channel.id)

    try:
        await channel.edit(
            name=make_channel_name(row),
            reason="Update member status",
        )
    except discord.HTTPException:
        log.exception("Could not update status for user %s", user_id)


# =========================================================
# EMBED LOGGING
# =========================================================

def message_embed(message: discord.Message, edited=False):
    description = message.content.strip() if message.content else ""
    if not description:
        description = "*Không có nội dung văn bản.*"

    if edited:
        description += "\n\n✏️ Tin nhắn đã được chỉnh sửa."

    if len(description) > 4000:
        description = description[:3990] + "\n…"

    embed = discord.Embed(
        description=description,
        color=discord.Color.blurple(),
        timestamp=message.created_at,
    )
    embed.set_author(
        name=f"{message.author.display_name} | ID {message.author.id}",
        icon_url=message.author.display_avatar.url,
    )
    embed.add_field(
        name="Kênh nguồn",
        value=f"#{message.channel.name}",
        inline=True,
    )
    embed.add_field(
        name="Tin nhắn",
        value=f"[Mở tin nhắn gốc]({message.jump_url})",
        inline=True,
    )
    embed.set_footer(text=f"Message ID: {message.id}")

    for attachment in message.attachments:
        if attachment.content_type and attachment.content_type.startswith("image/"):
            if embed.image.url is None:
                embed.set_image(url=attachment.url)
            else:
                embed.add_field(
                    name="Tệp đính kèm",
                    value=f"[{attachment.filename}]({attachment.url})",
                    inline=False,
                )
        else:
            embed.add_field(
                name="Tệp đính kèm",
                value=f"[{attachment.filename}]({attachment.url})",
                inline=False,
            )

    for sticker in message.stickers:
        embed.add_field(
            name="Sticker",
            value=f"{sticker.name} — {sticker.url}",
            inline=False,
        )

    return embed


async def copy_source_message(message: discord.Message, edited=False):
    if message.guild is None or message.guild.id != SOURCE_GUILD_ID:
        return

    if message.author.bot:
        return

    if not isinstance(message.author, discord.Member):
        return

    target = await ensure_member_channel(message.author)
    if target is None:
        return

    embed = message_embed(message, edited=edited)

    try:
        await target.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException:
        log.exception("Could not copy message %s", message.id)


# =========================================================
# OWNER-ONLY SLASH COMMAND
# =========================================================

@app_commands.command(
    name="timnguoi",
    description="Tìm thành viên theo Discord User ID",
)
@app_commands.describe(user_id="Discord User ID cần tìm")
async def timnguoi(
    interaction: discord.Interaction,
    user_id: str,
):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "Bạn không có quyền sử dụng lệnh này.",
            ephemeral=True,
        )
        return

    try:
        uid = int(user_id.strip())
    except ValueError:
        await interaction.response.send_message(
            "ID không hợp lệ. Hãy nhập Discord User ID dạng số.",
            ephemeral=True,
        )
        return

    row = get_member_row(uid)
    if row is None:
        await interaction.response.send_message(
            "Không tìm thấy ID này trong cơ sở dữ liệu.",
            ephemeral=True,
        )
        return

    guild = bot.get_guild(row["storage_guild_id"]) if row["storage_guild_id"] else None
    channel = bot.get_channel(row["channel_id"]) if row["channel_id"] else None

    embed = discord.Embed(
        title="Kết quả tìm thành viên",
        color=discord.Color.green() if row["is_member"] else discord.Color.red(),
    )
    embed.add_field(name="STT", value=str(row["stt"]), inline=True)
    embed.add_field(name="Tên", value=row["display_name"], inline=True)
    embed.add_field(name="Discord ID", value=str(row["user_id"]), inline=False)
    embed.add_field(
        name="Trạng thái",
        value="✅ Đang ở server gốc" if row["is_member"] else "🛑 Đã rời server gốc",
        inline=False,
    )
    embed.add_field(
        name="Server lưu trữ",
        value=guild.name if guild else "Không xác định / bot chưa vào server",
        inline=True,
    )
    embed.add_field(
        name="Kênh",
        value=channel.mention if isinstance(channel, discord.TextChannel) else "Chưa có kênh",
        inline=True,
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(timnguoi)


# =========================================================
# INITIAL SCAN
# =========================================================

async def initial_scan():
    source = bot.get_guild(SOURCE_GUILD_ID)
    if source is None:
        log.error("Bot is not in source guild %s", SOURCE_GUILD_ID)
        return

    # fetch_members asks Discord for the complete member list.
    # It can take time on large servers and may fail due to API/network limits.
    try:
        members = [m async for m in source.fetch_members(limit=None)]
    except (discord.HTTPException, discord.Forbidden):
        log.exception("Could not fetch source guild members; scan not completed")
        return

    # Stable order for first-time STT assignment. No join timestamps used.
    members.sort(key=lambda m: (m.id, m.name.lower()))

    total = len(members)
    log.info("Scan started: %s members", total)

    for index, member in enumerate(members, start=1):
        try:
            await ensure_member_channel(member)
        except discord.HTTPException:
            log.exception("Failed to process member %s", member.id)

        if index == 1 or index % 25 == 0 or index == total:
            log.info("Scanning members: %s/%s", index, total)

        await asyncio.sleep(SCAN_DELAY)

    # Mark known members absent from the completed scan as left.
    # Do this only after the entire fetch succeeded.
    scanned_ids = {m.id for m in members}
    rows = db.execute("SELECT user_id FROM members").fetchall()

    for row in rows:
        uid = int(row["user_id"])
        if uid not in scanned_ids and get_member_row(uid)["is_member"]:
            await update_channel_status(uid, False)

    log.info("Initial scan complete: %s members processed", total)


# =========================================================
# EVENTS
# =========================================================

@bot.event
async def on_ready():
    global initialized

    log.info("Logged in as %s (%s)", bot.user, bot.user.id)

    if not initialized:
        try:
            synced = await bot.tree.sync()
            log.info("Synced %s slash commands", len(synced))
        except discord.HTTPException:
            log.exception("Could not sync slash commands")

        initialized = True
        await initial_scan()


@bot.event
async def on_member_join(member: discord.Member):
    if member.guild.id != SOURCE_GUILD_ID:
        return

    try:
        await ensure_member_channel(member)
        await update_channel_status(member.id, True)
    except discord.HTTPException:
        log.exception("Could not process member join %s", member.id)


@bot.event
async def on_member_remove(member: discord.Member):
    if member.guild.id != SOURCE_GUILD_ID:
        return

    try:
        await update_channel_status(member.id, False)
    except discord.HTTPException:
        log.exception("Could not process member leave %s", member.id)


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    if after.guild.id != SOURCE_GUILD_ID:
        return

    if before.display_name != after.display_name:
        try:
            await ensure_member_channel(after)
        except discord.HTTPException:
            log.exception("Could not update member name %s", after.id)


@bot.event
async def on_message(message: discord.Message):
    if message.guild and message.guild.id == SOURCE_GUILD_ID:
        await copy_source_message(message)

    await bot.process_commands(message)


@bot.event
async def on_message_edit(before: discord.Message, after: discord.Message):
    if after.guild is None or after.guild.id != SOURCE_GUILD_ID:
        return
    if after.author.bot:
        return

    log_row = get_message_log(after.id)
    if log_row is None:
        # The original message may have been sent before the bot started.
        return

    channel = bot.get_channel(log_row["target_channel_id"])
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        target_message = await channel.fetch_message(log_row["target_message_id"])
        await target_message.edit(embed=message_embed(after, edited=True))
    except discord.HTTPException:
        log.exception("Could not update copied message %s", after.id)


@bot.event
async def on_message_delete(message: discord.Message):
    if message.guild is None or message.guild.id != SOURCE_GUILD_ID:
        return

    log_row = get_message_log(message.id)
    if log_row is None:
        return

    channel = bot.get_channel(log_row["target_channel_id"])
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        target_message = await channel.fetch_message(log_row["target_message_id"])
        embed = target_message.embeds[0] if target_message.embeds else discord.Embed()
        embed.add_field(
            name="Trạng thái",
            value="🗑️ Tin nhắn gốc đã bị xóa.",
            inline=False,
        )
        await target_message.edit(embed=embed)
    except discord.HTTPException:
        log.exception("Could not mark deleted message %s", message.id)


# =========================================================
# START
# =========================================================

if not TOKEN:
    raise RuntimeError(
        "Thiếu DISCORD_TOKEN. Hãy đặt biến môi trường DISCORD_TOKEN "
        "thành token bot Discord."
    )

bot.run(TOKEN)
