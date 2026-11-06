import os
import io
import re
import asyncio
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
from typing import Optional, Dict, Tuple

import matplotlib.pyplot as plt
import discord
from discord.ext import commands, tasks

from utils import (
    get_env_int,
    get_env_int_list,
)
from database import db as default_db, Database

# ---------------------------------------------------------
# MODULE-LEVEL CONSTANTS
# ---------------------------------------------------------
EXCLUDED_CHANNEL_SUFFIXES = ("logs", "log")
EXCLUDED_CHANNEL_SUFFIX = EXCLUDED_CHANNEL_SUFFIXES

ARCHIVE_MONTHS_COUNT = 12
ACTIVITY_DAYS_WINDOW = 30
TOP_USERS_LIMIT = 10
STATUS_UPDATE_INTERVAL = 5
EMOJI_SELF_LIMIT = 10
EMOJI_LEADERBOARD_LIMIT = 15
EMOJI_DAYS_WINDOW = 30

COLOR_HOURLY_BAR = "#3b82f6"
COLOR_DAILY_LINE = "#10b981"
COLOR_PRONOUN_BAR = "#f97316"
COLOR_UNKNOWN_BAR = "#64748b"

CUSTOM_EMOJI_REGEX = re.compile(r"<a?:[a-zA-Z0-9_]+:[0-9]+>")

UNICODE_EMOJI_REGEX = re.compile(
    r"("
    r"[\U0001F1E6-\U0001F1FF]{2}"
    r"|[0-9#*]\uFE0F?\u20E3"
    r"|[\u00A9\u00AE\u203C\u2049\u2122\u2139\u2194-\u2199\u21A9-\u21AA\u231A-\u231B\u2328\u23CF\u23E9-\u23F3\u23F8-\u23FA\u24C2\u25AA-\u25AB\u25B6\u25C0\u25FB-\u25FE\u2600-\u26FF\u2700-\u27BF\u2934-\u2935\u2B05-\u2B07\u2B1B-\u2B1C\u2B50\u2B55\u3030\u303D\u3297\u3299]"
    r"|[\U0001F000-\U0001F02F]"
    r"|[\U0001F0A0-\U0001F0FF]"
    r"|[\U0001F100-\U0001F64F]"
    r"|[\U0001F680-\U0001F6FF]"
    r"|[\U0001F700-\U0001F77F]"
    r"|[\U0001F780-\U0001F7FF]"
    r"|[\U0001F800-\U0001F8FF]"
    r"|[\U0001F900-\U0001F9FF]"
    r"|[\U0001FA00-\U0001FA6F]"
    r"|[\U0001FA70-\U0001FAFF]"
    r")"
    r"([\U0001F3FB-\U0001F3FF])?"
    r"(\uFE0F)?"
    r"(\u200D("
    r"[\U0001F1E6-\U0001F1FF]{2}"
    r"|[0-9#*]\uFE0F?\u20E3"
    r"|[\u00A9\u00AE\u203C\u2049\u2122\u2139\u2194-\u2199\u21A9-\u21AA\u231A-\u231B\u2328\u23CF\u23E9-\u23F3\u23F8-\u23FA\u24C2\u25AA-\u25AB\u25B6\u25C0\u25FB-\u25FE\u2600-\u26FF\u2700-\u27BF\u2934-\u2935\u2B05-\u2B07\u2B1B-\u2B1C\u2B50\u2B55\u3030\u303D\u3297\u3299]"
    r"|[\U0001F000-\U0001F02F]"
    r"|[\U0001F0A0-\U0001F0FF]"
    r"|[\U0001F100-\U0001F64F]"
    r"|[\U0001F680-\U0001F6FF]"
    r"|[\U0001F700-\U0001F77F]"
    r"|[\U0001F780-\U0001F7FF]"
    r"|[\U0001F800-\U0001F8FF]"
    r"|[\U0001F900-\U0001F9FF]"
    r"|[\U0001FA00-\U0001FA6F]"
    r"|[\U0001FA70-\U0001FAFF]"
    r")([\U0001F3FB-\U0001F3FF])?(\uFE0F)?)*"
)


def extract_emojis(content: str) -> list[str]:
    """Extracts all custom Discord and Unicode emojis from a message string in order."""
    if not content:
        return []
    customs = CUSTOM_EMOJI_REGEX.findall(content)
    cleaned = CUSTOM_EMOJI_REGEX.sub(" ", content)
    unicodes = [m.group(0) for m in UNICODE_EMOJI_REGEX.finditer(cleaned)]
    return customs + unicodes


class Analytics(commands.Cog):
    def __init__(self, bot, db: Optional[Database] = None):
        self.bot = bot
        self.db = db or default_db

        # Async lock to prevent concurrent scraping/writing collisions
        self.archive_lock = asyncio.Lock()
        self.is_syncing = False

        # In-Memory Buffers for Batch Writing
        self.buffer_lock = asyncio.Lock()
        self.user_buffer: Dict[Tuple[int, int, int, str], int] = defaultdict(int)
        self.hourly_buffer: Dict[str, int] = defaultdict(int)
        self.channel_sync_buffer: Dict[int, Tuple[int, str]] = {}
        self.emoji_buffer: Dict[Tuple[str, int, str, str], int] = defaultdict(int)

        # Load Settings & Role IDs from Environment
        self.timezone_offset = get_env_int("ANALYTICS_TIMEZONE_OFFSET_HOURS", 3)
        self.active_msg_threshold = get_env_int("PRONOUN_ACTIVE_MSG_THRESHOLD", 50)
        self.pronoun_role_ids = get_env_int_list("PRONOUN_ROLE_IDS")

    async def cog_load(self):
        await self.db.initialize()
        if not self.flush_buffer_task.is_running():
            self.flush_buffer_task.start()
        if not self.prune_retention_task.is_running():
            self.prune_retention_task.start()

    async def cog_unload(self):
        self.flush_buffer_task.cancel()
        self.prune_retention_task.cancel()
        await self.flush_buffer()

    # ---------------------------------------------------------
    # INTERNAL HELPERS & BUFFER MANAGEMENT
    # ---------------------------------------------------------
    @staticmethod
    def _is_enabled(env_key: str) -> bool:
        return os.getenv(env_key, "True").lower() in ("true", "1", "t")

    @staticmethod
    def _get_month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
        start_dt = datetime(year, month, 1)
        end_dt = (
            datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        ) - timedelta(seconds=1)
        return start_dt, end_dt

    async def flush_buffer(self) -> None:
        """Flushes in-memory accumulated metrics to SQLite in single transactions."""
        if self.is_syncing:
            return

        async with self.buffer_lock:
            if not self.user_buffer and not self.hourly_buffer and not self.channel_sync_buffer and not self.emoji_buffer:
                return

            users_snapshot = dict(self.user_buffer)
            hourly_snapshot = dict(self.hourly_buffer)
            sync_snapshot = dict(self.channel_sync_buffer)
            emoji_snapshot = dict(self.emoji_buffer)

            self.user_buffer.clear()
            self.hourly_buffer.clear()
            self.channel_sync_buffer.clear()
            self.emoji_buffer.clear()

        if users_snapshot or hourly_snapshot or sync_snapshot:
            await self.db.record_messages_batch(users_snapshot, hourly_snapshot, sync_snapshot)
        if emoji_snapshot:
            await self.db.record_emoji_batch(emoji_snapshot)

    @tasks.loop(seconds=15)
    async def flush_buffer_task(self):
        await self.flush_buffer()

    @tasks.loop(hours=1)
    async def prune_retention_task(self):
        """Prunes activity and emoji data older than retention limit."""
        now_utc = datetime.now(timezone.utc)
        hourly_cutoff = (now_utc - timedelta(days=ACTIVITY_DAYS_WINDOW + 1)).strftime("%Y-%m-%d %H")
        emoji_cutoff = (now_utc - timedelta(days=EMOJI_DAYS_WINDOW + 1)).strftime("%Y-%m-%d")

        await self.db.prune_activity_hourly(hourly_cutoff)
        await self.db.prune_emoji_daily_usage(emoji_cutoff)

    # ---------------------------------------------------------
    # EVENT LISTENERS
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Real-time event capture for server analytics and emoji usage."""
        if message.author.bot:
            return
        if not isinstance(message.channel, discord.TextChannel):
            return
        if message.channel.name.lower().endswith(EXCLUDED_CHANNEL_SUFFIX):
            return

        now_utc = message.created_at.astimezone(timezone.utc) if message.created_at.tzinfo else message.created_at.replace(tzinfo=timezone.utc)
        user_key = (now_utc.year, now_utc.month, message.author.id, str(message.author))
        date_hour = now_utc.strftime("%Y-%m-%d %H")
        date_str = now_utc.strftime("%Y-%m-%d")

        emojis = extract_emojis(message.content)

        async with self.buffer_lock:
            self.user_buffer[user_key] += 1
            self.hourly_buffer[date_hour] += 1
            self.channel_sync_buffer[message.channel.id] = (message.id, now_utc.isoformat())

            if emojis:
                for emoji, count in Counter(emojis).items():
                    self.emoji_buffer[(date_str, message.author.id, message.author.display_name, emoji)] += count

    @commands.Cog.listener()
    async def on_ready(self):
        """Performs initial 30-day baseline backfill on first boot, or catch-up sync across server channels."""
        is_synced = await self.db.is_initial_sync_done()
        if not is_synced:
            print("⚙️ Initial SQLite startup detected. Starting one-time 30-day baseline backfill across channels...")
            for guild in self.bot.guilds:
                try:
                    await self.initial_30d_backfill(guild)
                except Exception as e:
                    print(f"⚠️ Initial backfill error for {guild.name}: {e}")
            await self.db.set_initial_sync_done(True)
            print("✅ One-time 30-day baseline backfill complete! Future boots will use fast delta catch-up.")
        else:
            for guild in self.bot.guilds:
                try:
                    await self.catch_up_sync(guild)
                except Exception as e:
                    print(f"⚠️ Catch-up sync warning for {guild.name}: {e}")

    async def initial_30d_backfill(self, guild: discord.Guild) -> None:
        """One-time 30-day baseline history scan covering activity, emojis, current month users, and watermarks."""
        self.is_syncing = True
        try:
            now_utc = datetime.now(timezone.utc)
            cutoff_snowflake = discord.utils.time_snowflake(now_utc)
            thirty_days_ago = now_utc - timedelta(days=ACTIVITY_DAYS_WINDOW)
            start_of_month = datetime(now_utc.year, now_utc.month, 1, tzinfo=timezone.utc)
            scan_start_dt = min(thirty_days_ago, start_of_month)

            # Clear current incomplete month's user activity so we rebuild it accurately without double counting
            await self.db.clear_month_user_activity(now_utc.year, now_utc.month)

            user_increments: Dict[Tuple[int, int, int, str], int] = defaultdict(int)
            hourly_increments: Dict[str, int] = defaultdict(int)
            sync_updates: Dict[int, Tuple[int, str]] = {}
            emoji_increments: Dict[Tuple[str, int, str, str], int] = defaultdict(int)

            for channel in guild.text_channels:
                if not channel.permissions_for(guild.me).read_message_history:
                    continue
                if channel.name.lower().endswith(EXCLUDED_CHANNEL_SUFFIX):
                    continue

                try:
                    latest_id = None
                    latest_ts = None
                    async for msg in channel.history(
                        limit=None,
                        after=scan_start_dt,
                        before=discord.Object(id=cutoff_snowflake),
                        oldest_first=True,
                    ):
                        if msg.author.bot:
                            continue
                        m_utc = msg.created_at.astimezone(timezone.utc) if msg.created_at.tzinfo else msg.created_at.replace(tzinfo=timezone.utc)

                        # Only count current month in user_monthly_activity (prior months are preserved from legacy migration)
                        if m_utc.year == now_utc.year and m_utc.month == now_utc.month:
                            ukey = (m_utc.year, m_utc.month, msg.author.id, str(msg.author))
                            user_increments[ukey] += 1

                        # Hourly activity (last 30 days)
                        if m_utc >= thirty_days_ago:
                            hourly_increments[m_utc.strftime("%Y-%m-%d %H")] += 1

                        # Emojis (last 30 days)
                        if m_utc >= thirty_days_ago:
                            emojis = extract_emojis(msg.content)
                            if emojis:
                                dstr = m_utc.strftime("%Y-%m-%d")
                                for emo, ecount in Counter(emojis).items():
                                    emoji_increments[(dstr, msg.author.id, msg.author.display_name, emo)] += ecount

                        latest_id = msg.id
                        latest_ts = m_utc.isoformat()

                    if latest_id:
                        sync_updates[channel.id] = (latest_id, latest_ts)
                    elif channel.last_message_id:
                        sync_updates[channel.id] = (channel.last_message_id, now_utc.isoformat())
                except discord.Forbidden:
                    pass

            if user_increments or hourly_increments or sync_updates:
                await self.db.record_messages_batch(user_increments, hourly_increments, sync_updates)
            if emoji_increments:
                await self.db.record_emoji_batch(emoji_increments)
        finally:
            self.is_syncing = False
            await self.flush_buffer()

    async def catch_up_sync(self, guild: discord.Guild) -> None:
        """Pulls only missed messages since channel last watermark (zero data loss)."""
        self.is_syncing = True
        try:
            now_utc = datetime.now(timezone.utc)
            cutoff_snowflake = discord.utils.time_snowflake(now_utc)
            channel_syncs = await self.db.get_all_channel_syncs()
            user_increments: Dict[Tuple[int, int, int, str], int] = defaultdict(int)
            hourly_increments: Dict[str, int] = defaultdict(int)
            sync_updates: Dict[int, Tuple[int, str]] = {}
            emoji_increments: Dict[Tuple[str, int, str, str], int] = defaultdict(int)

            for channel in guild.text_channels:
                if not channel.permissions_for(guild.me).read_message_history:
                    continue
                if channel.name.lower().endswith(EXCLUDED_CHANNEL_SUFFIX):
                    continue

                last_id = channel_syncs.get(channel.id)
                if last_id:
                    try:
                        latest_id = last_id
                        latest_ts = None
                        async for msg in channel.history(
                            limit=None,
                            after=discord.Object(id=last_id),
                            before=discord.Object(id=cutoff_snowflake),
                            oldest_first=True,
                        ):
                            if msg.author.bot:
                                continue
                            now_utc = msg.created_at.astimezone(timezone.utc) if msg.created_at.tzinfo else msg.created_at.replace(tzinfo=timezone.utc)
                            ukey = (now_utc.year, now_utc.month, msg.author.id, str(msg.author))
                            user_increments[ukey] += 1
                            hourly_increments[now_utc.strftime("%Y-%m-%d %H")] += 1
                            latest_id = msg.id
                            latest_ts = now_utc.isoformat()

                            emojis = extract_emojis(msg.content)
                            if emojis:
                                dstr = now_utc.strftime("%Y-%m-%d")
                                for emo, ecount in Counter(emojis).items():
                                    emoji_increments[(dstr, msg.author.id, msg.author.display_name, emo)] += ecount

                        if latest_ts and latest_id != last_id:
                            sync_updates[channel.id] = (latest_id, latest_ts)
                    except discord.Forbidden:
                        pass
                else:
                    if channel.last_message_id:
                        sync_updates[channel.id] = (channel.last_message_id, now_utc.isoformat())

            if user_increments or hourly_increments or sync_updates:
                await self.db.record_messages_batch(user_increments, hourly_increments, sync_updates)
            if emoji_increments:
                await self.db.record_emoji_batch(emoji_increments)
        finally:
            self.is_syncing = False
            await self.flush_buffer()

    async def _scrape_month_data(
        self,
        guild: discord.Guild,
        year: int,
        month: int,
        status_msg: discord.Message = None,
        progress_prefix: str = "",
    ) -> None:
        """Scrapes message history for a specific month and stores directly into SQLite."""
        start_dt, end_dt = self._get_month_bounds(year, month)
        is_complete = end_dt < datetime.now()
        total_channels = len(guild.text_channels)

        user_counts = defaultdict(lambda: {"name": "Unknown User", "count": 0})
        hourly_counts: Dict[str, int] = defaultdict(int)
        sync_updates: Dict[int, Tuple[int, str]] = {}

        for idx, channel in enumerate(guild.text_channels, 1):
            if not channel.permissions_for(guild.me).read_message_history:
                continue
            if channel.name.lower().endswith(EXCLUDED_CHANNEL_SUFFIX):
                continue

            if status_msg and idx % STATUS_UPDATE_INTERVAL == 0:
                await status_msg.edit(
                    content=f"{progress_prefix} — Scanning channel {idx}/{total_channels}..."
                )

            try:
                async for msg in channel.history(limit=None, after=start_dt, before=end_dt):
                    if msg.author.bot:
                        continue
                    uid = msg.author.id
                    user_counts[uid]["name"] = str(msg.author)
                    user_counts[uid]["count"] += 1

                    now_utc = msg.created_at.astimezone(timezone.utc) if msg.created_at.tzinfo else msg.created_at.replace(tzinfo=timezone.utc)
                    hourly_counts[now_utc.strftime("%Y-%m-%d %H")] += 1
                    sync_updates[channel.id] = (msg.id, now_utc.isoformat())
            except discord.Forbidden:
                pass

        batch_users = {
            (year, month, uid, info["name"]): info["count"]
            for uid, info in user_counts.items()
        }
        await self.db.record_messages_batch(batch_users, hourly_counts, sync_updates)
        if is_complete:
            await self.db.set_month_complete(year, month, is_complete=True)

    # ---------------------------------------------------------
    # ANALYTICS COMMANDS
    # ---------------------------------------------------------
    @commands.command(name="archive")
    async def archive(self, ctx):
        if not self._is_enabled("ENABLE_CMD_ARCHIVE"):
            return

        status_msg = await ctx.send(
            f"🗄️ Initializing {ARCHIVE_MONTHS_COUNT}-month deep scan. This will take a while..."
        )
        now = datetime.now()

        for i in range(ARCHIVE_MONTHS_COUNT):
            y = now.year + (now.month - 1 - i) // 12
            m = (now.month - 1 - i) % 12 + 1

            if await self.db.is_month_complete(y, m):
                continue

            prefix = f"🗄️ Scraping data for {y}-{m:02d} (Month {i + 1} of {ARCHIVE_MONTHS_COUNT})"
            await status_msg.edit(content=f"{prefix}...")
            await self._scrape_month_data(ctx.guild, y, m, status_msg, prefix)

        await status_msg.edit(content=f"✅ {ARCHIVE_MONTHS_COUNT}-month data archive is up to date in SQLite!")

    @commands.command(name="syncbaseline", aliases=["backfill30"])
    @commands.has_permissions(administrator=True)
    async def syncbaseline(self, ctx):
        """Forces a fresh 30-day baseline backfill across all server channels (Admin only)."""
        status_msg = await ctx.send("⏳ Initiating 30-day baseline backfill across all server channels...")
        try:
            await self.initial_30d_backfill(ctx.guild)
            await self.db.set_initial_sync_done(True)
            await status_msg.edit(content="✅ 30-day baseline backfill successfully completed!")
        except Exception as e:
            await status_msg.edit(content=f"⚠️ Failed to complete baseline sync: {e}")

    @commands.command(name="activity30")
    async def activity30(self, ctx):
        if not self._is_enabled("ENABLE_CMD_ACTIVITY30"):
            return

        await self.flush_buffer()

        status_msg = await ctx.send(
            f"📈 Compiling {ACTIVITY_DAYS_WINDOW}-day activity metrics for the server..."
        )

        now_utc = datetime.now(timezone.utc)
        start_utc = now_utc - timedelta(days=ACTIVITY_DAYS_WINDOW)
        start_date_hour = start_utc.strftime("%Y-%m-%d %H")

        rows = await self.db.get_activity_30_days(start_date_hour)

        # Baseline backfill if activity table has no rows
        if not rows:
            await status_msg.edit(content="📊 Initializing 30-day activity baseline cache...")
            user_increments = defaultdict(int)
            hourly_increments = defaultdict(int)
            sync_updates = {}

            for channel in ctx.guild.text_channels:
                if not channel.permissions_for(ctx.guild.me).read_message_history:
                    continue
                if channel.name.lower().endswith(EXCLUDED_CHANNEL_SUFFIX):
                    continue

                try:
                    async for msg in channel.history(limit=None, after=start_utc):
                        if msg.author.bot:
                            continue
                        m_utc = msg.created_at.astimezone(timezone.utc) if msg.created_at.tzinfo else msg.created_at.replace(tzinfo=timezone.utc)
                        ukey = (m_utc.year, m_utc.month, msg.author.id, str(msg.author))
                        user_increments[ukey] += 1
                        hourly_increments[m_utc.strftime("%Y-%m-%d %H")] += 1
                        sync_updates[channel.id] = (msg.id, m_utc.isoformat())
                except discord.Forbidden:
                    pass

            if user_increments or hourly_increments:
                await self.db.record_messages_batch(user_increments, hourly_increments, sync_updates)
                rows = await self.db.get_activity_30_days(start_date_hour)

        hourly_counts = {i: 0 for i in range(24)}
        daily_counts = {
            (start_utc + timedelta(days=i)).strftime("%Y-%m-%d"): 0
            for i in range(ACTIVITY_DAYS_WINDOW + 1)
        }

        for date_hour, count in rows:
            day_str = date_hour[:10]
            if day_str in daily_counts:
                daily_counts[day_str] += count

            utc_hour = int(date_hour[11:13])
            local_hour = (utc_hour + self.timezone_offset) % 24
            hourly_counts[local_hour] += count

        # 1. Generate Hourly Bar Chart
        plt.figure(figsize=(10, 5))
        plt.bar(list(hourly_counts.keys()), list(hourly_counts.values()), color=COLOR_HOURLY_BAR)
        plt.title(f"Activity by Hour (UTC+{self.timezone_offset}) [Server-wide]")
        plt.xlabel("Hour (0:00 - 23:00)")
        plt.ylabel("Total Messages")
        plt.xticks(range(24))
        plt.grid(axis="y", linestyle="--", alpha=0.7)
        plt.tight_layout()

        buf_hourly = io.BytesIO()
        plt.savefig(buf_hourly, format="png")
        buf_hourly.seek(0)
        plt.close()

        # 2. Generate Daily Line Chart
        plt.figure(figsize=(12, 5))
        sorted_days = sorted(daily_counts.items())
        plt.plot(
            [i[0] for i in sorted_days],
            [i[1] for i in sorted_days],
            marker="o",
            color=COLOR_DAILY_LINE,
            linewidth=2,
        )
        plt.title(f"Daily Activity (Last {ACTIVITY_DAYS_WINDOW} Days) [Server-wide]")
        plt.xlabel("Date")
        plt.ylabel("Messages")
        plt.xticks(rotation=45, ha="right")
        plt.grid(True, linestyle="--", alpha=0.5)
        plt.tight_layout()

        buf_daily = io.BytesIO()
        plt.savefig(buf_daily, format="png")
        buf_daily.seek(0)
        plt.close()

        file_hourly = discord.File(fp=buf_hourly, filename="hourly_activity.png")
        file_daily = discord.File(fp=buf_daily, filename="daily_activity.png")

        await status_msg.delete()
        await ctx.send(
            f"📊 **Server Activity (Last {ACTIVITY_DAYS_WINDOW} Days):**",
            files=[file_hourly, file_daily],
        )

    @commands.command(name="topusers")
    async def topusers(self, ctx, year: int = None, month: int = None):
        if not self._is_enabled("ENABLE_CMD_TOPUSERS"):
            return

        if year is None:
            return await ctx.send(
                "⚠️ Please provide at least a year. Example: `!topusers 2026` or `!topusers 2026 9`"
            )

        now = datetime.now()

        if month is None:
            target_months = list(range(1, 13))
            title = f"{year} Yearly Total"
        else:
            if not (1 <= month <= 12):
                return await ctx.send("⚠️ Month must be between 1 and 12.")
            target_months = [month]
            title = f"{year}-{month:02d}"

        # Flush pending buffer so messages sent seconds ago are fully counted
        await self.flush_buffer()

        status_msg = None
        all_months_complete = True

        for m in target_months:
            start_dt, end_dt = self._get_month_bounds(year, m)
            if start_dt > now:
                continue

            is_complete = await self.db.is_month_complete(year, m)
            if not is_complete and end_dt < now:
                all_months_complete = False
                prefix = f"⏳ Gathering data for **{title}** (Scanning {year}-{m:02d})"
                if not status_msg:
                    status_msg = await ctx.send(f"{prefix}...")
                else:
                    await status_msg.edit(content=f"{prefix}...")

                await self._scrape_month_data(ctx.guild, year, m, status_msg, prefix)

        if status_msg:
            await status_msg.delete()

        # Query SQLite directly in < 5ms
        top_users = await self.db.get_top_users(year, month, limit=TOP_USERS_LIMIT)

        if not top_users:
            return await ctx.send(f"ℹ️ No messages recorded for {title}.")

        embed = discord.Embed(title=f"🏆 Top Active Users ({title})", color=discord.Color.gold())
        embed.description = "\n".join(
            [
                f"**{rank}.** {name} — {count:,} messages"
                for rank, (name, count) in enumerate(top_users, 1)
            ]
        )
        embed.set_footer(
            text="Status: Complete (Loaded from SQLite Database)"
            if all_months_complete
            else "Status: Data Updated to Present"
        )

        await ctx.send(embed=embed)

    @commands.command(name="pronounstats")
    async def pronounstats(self, ctx, year: int = None):
        if not self._is_enabled("ENABLE_CMD_PRONOUNSTATS"):
            return

        if not self.pronoun_role_ids:
            return await ctx.send("⚠️ `PRONOUN_ROLE_IDS` is not configured in `.env`.")

        if year is None:
            year = datetime.now().year

        status_msg = await ctx.send(
            f"📊 Calculating active users and retrieving roles for {year}..."
        )

        await self.flush_buffer()

        active_users = await self.db.get_active_users_for_year(year, self.active_msg_threshold)

        if not active_users:
            await status_msg.delete()
            return await ctx.send(
                f"ℹ️ No active users ({self.active_msg_threshold}+ messages in a month) found in the database for {year}."
            )

        role_counts = {rid: 0 for rid in self.pronoun_role_ids}
        role_id_to_label = {}
        for rid in self.pronoun_role_ids:
            role_obj = ctx.guild.get_role(rid)
            role_id_to_label[rid] = role_obj.name if role_obj else f"Role {rid}"

        unknown_count = 0

        # Check current server roles by ID for each active user
        for uid in active_users:
            member = ctx.guild.get_member(uid)
            if member:
                member_role_ids = {r.id for r in member.roles}
                matched_any = False

                for rid in self.pronoun_role_ids:
                    if rid in member_role_ids:
                        role_counts[rid] += 1
                        matched_any = True

                if not matched_any:
                    unknown_count += 1
            else:
                unknown_count += 1

        # Generate Bar Chart
        plt.figure(figsize=(12, 6))
        categories = [role_id_to_label[rid] for rid in self.pronoun_role_ids] + ["Unknown"]
        counts = [role_counts[rid] for rid in self.pronoun_role_ids] + [unknown_count]
        colors = [COLOR_PRONOUN_BAR] * len(self.pronoun_role_ids) + [COLOR_UNKNOWN_BAR]

        bars = plt.bar(categories, counts, color=colors)
        plt.title(
            f"Active User Demographics for {year}\n(Active = {self.active_msg_threshold}+ messages in a single month)"
        )
        plt.xlabel("Assigned Roles")
        plt.ylabel("Number of Active Users")
        plt.xticks(rotation=45, ha="right")
        plt.grid(axis="y", linestyle="--", alpha=0.7)

        max_count = max(counts) if counts else 1
        for bar in bars:
            yval = bar.get_height()
            plt.text(
                bar.get_x() + bar.get_width() / 2,
                yval + (max_count * 0.02),
                int(yval),
                ha="center",
                va="bottom",
                fontweight="bold",
            )

        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close()

        file = discord.File(fp=buf, filename="pronoun_stats.png")
        await status_msg.delete()
        await ctx.send(
            f"Here are the active user demographic statistics for {year}:", file=file
        )

    @commands.command(name="myemojis", aliases=["emojiself", "selfemojis"])
    async def myemojis(self, ctx, *args):
        """Shows your top 10 most used emojis from the past 30 days. Personal only."""
        if not self._is_enabled("ENABLE_CMD_MYEMOJIS"):
            return

        if args:
            return await ctx.send(
                "🔒 **Privacy Notice:** `!myemojis` is a personal command that only inspects your own messages. "
                "Scanning other users' messages is not allowed."
            )

        status_msg = await ctx.send(
            f"🔍 Analyzing your emojis from the past {EMOJI_DAYS_WINDOW} days, {ctx.author.mention}..."
        )

        await self.flush_buffer()

        start_dt = discord.utils.utcnow() - timedelta(days=EMOJI_DAYS_WINDOW)
        start_date = start_dt.strftime("%Y-%m-%d")

        top_emojis, unique_count, total_uses = await self.db.get_user_top_emojis(
            ctx.author.id, start_date, limit=EMOJI_SELF_LIMIT
        )

        # Baseline fallback if database table has no data for this user
        if not top_emojis:
            user_emoji_counts = Counter()
            for channel in ctx.guild.text_channels:
                if not channel.permissions_for(ctx.guild.me).read_message_history:
                    continue
                if channel.name.lower().endswith(EXCLUDED_CHANNEL_SUFFIX):
                    continue

                try:
                    async for msg in channel.history(limit=None, after=start_dt):
                        if msg.author.id != ctx.author.id:
                            continue
                        found_emojis = extract_emojis(msg.content)
                        if found_emojis:
                            user_emoji_counts.update(found_emojis)
                            d_str = msg.created_at.strftime("%Y-%m-%d")
                            batch = {
                                (d_str, msg.author.id, msg.author.display_name, emo): cnt
                                for emo, cnt in Counter(found_emojis).items()
                            }
                            await self.db.record_emoji_batch(batch)
                except discord.Forbidden:
                    pass

            if user_emoji_counts:
                top_emojis = user_emoji_counts.most_common(EMOJI_SELF_LIMIT)
                unique_count = len(user_emoji_counts)
                total_uses = sum(user_emoji_counts.values())

        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass

        if not top_emojis:
            return await ctx.send(
                f"ℹ️ No emojis found in your messages over the past {EMOJI_DAYS_WINDOW} days."
            )

        embed = discord.Embed(
            title=f"📊 Your Top {len(top_emojis)} Emojis (Past {EMOJI_DAYS_WINDOW} Days)",
            color=discord.Color.blue(),
        )
        embed.set_author(
            name=f"{ctx.author.display_name}'s Emoji Stats",
            icon_url=ctx.author.display_avatar.url if ctx.author.display_avatar else None,
        )
        lines = [
            f"**{rank}.** {emoji} — **{count:,}** use{'s' if count != 1 else ''}"
            for rank, (emoji, count) in enumerate(top_emojis, 1)
        ]
        embed.description = "\n".join(lines)
        embed.set_footer(
            text=f"Total unique emojis: {unique_count} | Total uses: {total_uses:,}"
        )

        await ctx.send(embed=embed)

    @commands.command(name="emojileaderboard", aliases=["topemojis", "emojitop"])
    async def emojileaderboard(self, ctx):
        """Server-wide leaderboard of top 15 most used emojis and their top user over the past 30 days."""
        if not self._is_enabled("ENABLE_CMD_EMOJILEADERBOARD"):
            return

        status_msg = await ctx.send(
            f"📊 Compiling server-wide emoji leaderboard for the past {EMOJI_DAYS_WINDOW} days..."
        )

        await self.flush_buffer()

        start_dt = discord.utils.utcnow() - timedelta(days=EMOJI_DAYS_WINDOW)
        start_date = start_dt.strftime("%Y-%m-%d")

        leaderboard, unique_count, total_uses = await self.db.get_server_emoji_leaderboard(
            start_date, limit=EMOJI_LEADERBOARD_LIMIT
        )

        # Baseline fallback if database table has no data yet
        if not leaderboard:
            total_counts = Counter()
            emoji_user_counts = defaultdict(Counter)
            user_names = {}

            total_channels = len(ctx.guild.text_channels)
            for idx, channel in enumerate(ctx.guild.text_channels, 1):
                if not channel.permissions_for(ctx.guild.me).read_message_history:
                    continue
                if channel.name.lower().endswith(EXCLUDED_CHANNEL_SUFFIX):
                    continue

                if idx % STATUS_UPDATE_INTERVAL == 0:
                    try:
                        await status_msg.edit(
                            content=f"📊 Compiling emoji leaderboard — Scanning channel {idx}/{total_channels}..."
                        )
                    except Exception:
                        pass

                try:
                    async for msg in channel.history(limit=None, after=start_dt):
                        if msg.author.bot:
                            continue

                        found_emojis = extract_emojis(msg.content)
                        if not found_emojis:
                            continue

                        total_counts.update(found_emojis)
                        user_names[msg.author.id] = msg.author.display_name
                        d_str = msg.created_at.strftime("%Y-%m-%d")
                        batch = {
                            (d_str, msg.author.id, msg.author.display_name, emo): cnt
                            for emo, cnt in Counter(found_emojis).items()
                        }
                        await self.db.record_emoji_batch(batch)

                        for emoji, count in Counter(found_emojis).items():
                            emoji_user_counts[emoji][msg.author.id] += count
                except discord.Forbidden:
                    pass

            if total_counts:
                top_emojis = total_counts.most_common(EMOJI_LEADERBOARD_LIMIT)
                leaderboard = []
                for emoji, total in top_emojis:
                    top_uid, top_uses = emoji_user_counts[emoji].most_common(1)[0]
                    top_name = user_names.get(top_uid, "Unknown User")
                    leaderboard.append((emoji, total, top_name, top_uses))
                unique_count = len(total_counts)
                total_uses = sum(total_counts.values())

        if status_msg:
            try:
                await status_msg.delete()
            except Exception:
                pass

        if not leaderboard:
            return await ctx.send(
                f"ℹ️ No emojis found in server messages over the past {EMOJI_DAYS_WINDOW} days."
            )

        lines = [
            f"**{rank}.** {emoji} — **{total:,}** use{'s' if total != 1 else ''} "
            f"(Top user: **{top_name}** with {top_uses:,})"
            for rank, (emoji, total, top_name, top_uses) in enumerate(leaderboard, 1)
        ]

        embed = discord.Embed(
            title=f"🏆 Server Emoji Leaderboard (Past {EMOJI_DAYS_WINDOW} Days)",
            color=discord.Color.gold(),
        )
        embed.description = "\n".join(lines)
        embed.set_footer(
            text=f"Total unique emojis: {unique_count} | Total uses: {total_uses:,}"
        )

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Analytics(bot))
