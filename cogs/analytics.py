import os
import io
import asyncio
from datetime import datetime, timedelta
import matplotlib.pyplot as plt
import discord
from discord.ext import commands

from utils import (
    load_json_data,
    save_json_data,
    get_env_int,
    get_env_int_list,
)

# ---------------------------------------------------------
# MODULE-LEVEL CONSTANTS
# ---------------------------------------------------------
STATS_DIR = "monthly_stats"
EXCLUDED_CHANNEL_SUFFIX = "logs"

ARCHIVE_MONTHS_COUNT = 12
ACTIVITY_DAYS_WINDOW = 30
TOP_USERS_LIMIT = 10
STATUS_UPDATE_INTERVAL = 5

COLOR_HOURLY_BAR = "#3b82f6"
COLOR_DAILY_LINE = "#10b981"
COLOR_PRONOUN_BAR = "#f97316"
COLOR_UNKNOWN_BAR = "#64748b"


class Analytics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Async lock to prevent concurrent scraping/writing collisions
        self.archive_lock = asyncio.Lock()

        # Load Settings & Role IDs from Environment
        self.timezone_offset = get_env_int("ANALYTICS_TIMEZONE_OFFSET_HOURS", 3)
        self.active_msg_threshold = get_env_int("PRONOUN_ACTIVE_MSG_THRESHOLD", 50)
        self.pronoun_role_ids = get_env_int_list("PRONOUN_ROLE_IDS")

        os.makedirs(STATS_DIR, exist_ok=True)

    # ---------------------------------------------------------
    # INTERNAL HELPERS
    # ---------------------------------------------------------
    @staticmethod
    def _is_enabled(env_key: str) -> bool:
        return os.getenv(env_key, "True").lower() in ("true", "1", "t")

    @staticmethod
    def _get_stats_filepath(year: int, month: int) -> str:
        return os.path.join(STATS_DIR, f"stats_{year}_{month:02d}.json")

    @staticmethod
    def _get_month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
        start_dt = datetime(year, month, 1)
        end_dt = (
            datetime(year + 1, 1, 1) if month == 12 else datetime(year, month + 1, 1)
        ) - timedelta(seconds=1)
        return start_dt, end_dt

    async def _scrape_month_data(
        self,
        guild: discord.Guild,
        year: int,
        month: int,
        status_msg: discord.Message = None,
        progress_prefix: str = "",
    ) -> dict:
        """Scrapes message history for a specific month and saves it to the JSON cache."""
        start_dt, end_dt = self._get_month_bounds(year, month)
        is_complete = end_dt < datetime.now()
        filepath = self._get_stats_filepath(year, month)

        month_data = {"complete": is_complete, "users": {}}
        total_channels = len(guild.text_channels)

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

                    uid = str(msg.author.id)
                    cname = channel.name

                    if uid not in month_data["users"]:
                        month_data["users"][uid] = {"name": str(msg.author), "channels": {}}
                    if cname not in month_data["users"][uid]["channels"]:
                        month_data["users"][uid]["channels"][cname] = 0

                    month_data["users"][uid]["channels"][cname] += 1
            except discord.Forbidden:
                pass

        async with self.archive_lock:
            save_json_data(filepath, month_data)

        return month_data

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
            filepath = self._get_stats_filepath(y, m)

            existing_data = load_json_data(filepath)
            if existing_data.get("complete", False):
                continue

            prefix = f"🗄️ Scraping data for {y}-{m:02d} (Month {i + 1} of {ARCHIVE_MONTHS_COUNT})"
            await status_msg.edit(content=f"{prefix}...")
            await self._scrape_month_data(ctx.guild, y, m, status_msg, prefix)

        await status_msg.edit(content=f"✅ {ARCHIVE_MONTHS_COUNT}-month data archive is up to date!")

    @commands.command(name="activity30")
    async def activity30(self, ctx):
        if not self._is_enabled("ENABLE_CMD_ACTIVITY30"):
            return

        status_msg = await ctx.send(
            f"📈 Compiling {ACTIVITY_DAYS_WINDOW}-day activity metrics for the server..."
        )

        now = datetime.now()
        start_dt = now - timedelta(days=ACTIVITY_DAYS_WINDOW)
        hourly_counts = {i: 0 for i in range(24)}
        daily_counts = {
            (start_dt + timedelta(days=i)).strftime("%Y-%m-%d"): 0
            for i in range(ACTIVITY_DAYS_WINDOW + 1)
        }

        for channel in ctx.guild.text_channels:
            if not channel.permissions_for(ctx.guild.me).read_message_history:
                continue
            if channel.name.lower().endswith(EXCLUDED_CHANNEL_SUFFIX):
                continue

            try:
                async for msg in channel.history(limit=None, after=start_dt):
                    if msg.author.bot:
                        continue

                    local_time = msg.created_at + timedelta(hours=self.timezone_offset)
                    hourly_counts[local_time.hour] += 1

                    day_str = msg.created_at.strftime("%Y-%m-%d")
                    if day_str in daily_counts:
                        daily_counts[day_str] += 1
            except discord.Forbidden:
                pass

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

        aggregated_users = {}
        all_months_complete = True
        status_msg = None

        for m in target_months:
            start_dt, _ = self._get_month_bounds(year, m)
            if start_dt > now:
                continue

            filepath = self._get_stats_filepath(year, m)
            existing_data = load_json_data(filepath)

            if not existing_data.get("complete", False):
                all_months_complete = False
                prefix = f"⏳ Gathering data for **{title}** (Scanning {year}-{m:02d})"
                if not status_msg:
                    status_msg = await ctx.send(f"{prefix}...")
                else:
                    await status_msg.edit(content=f"{prefix}...")

                existing_data = await self._scrape_month_data(
                    ctx.guild, year, m, status_msg, prefix
                )

            users_dict = existing_data.get("users", {})
            for uid, uinfo in users_dict.items():
                if uid not in aggregated_users:
                    aggregated_users[uid] = {
                        "name": uinfo.get("name", "Unknown User"),
                        "total": 0,
                    }
                aggregated_users[uid]["total"] += sum(uinfo.get("channels", {}).values())

        if status_msg:
            await status_msg.delete()

        if not aggregated_users:
            return await ctx.send(f"ℹ️ No messages recorded for {title}.")

        user_totals = [(info["name"], info["total"]) for info in aggregated_users.values()]
        user_totals.sort(key=lambda x: x[1], reverse=True)
        top_users = user_totals[:TOP_USERS_LIMIT]

        embed = discord.Embed(title=f"🏆 Top Active Users ({title})", color=discord.Color.gold())
        embed.description = "\n".join(
            [
                f"**{rank}.** {name} — {count:,} messages"
                for rank, (name, count) in enumerate(top_users, 1)
            ]
        )
        embed.set_footer(
            text="Status: Complete (Loaded from Cache)"
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

        # Map configured role IDs to their live Discord role names for chart labels
        role_id_to_label = {}
        for idx, rid in enumerate(self.pronoun_role_ids, 1):
            role_obj = ctx.guild.get_role(rid)
            role_id_to_label[rid] = role_obj.name if role_obj else f"Role {idx}"

        role_counts = {rid: 0 for rid in self.pronoun_role_ids}
        unknown_count = 0
        active_users = set()

        # 1. Identify all active users for the specified year using the archives
        for month in range(1, 13):
            filepath = self._get_stats_filepath(year, month)
            data = load_json_data(filepath)
            for uid, info in data.get("users", {}).items():
                total_msgs = sum(info.get("channels", {}).values())
                if total_msgs >= self.active_msg_threshold:
                    active_users.add(int(uid))

        if not active_users:
            await status_msg.delete()
            return await ctx.send(
                f"ℹ️ No active users ({self.active_msg_threshold}+ messages in a month) found in the archives for {year}."
            )

        # 2. Check current server roles by ID for each active user
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

        # 3. Generate the Bar Chart
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


async def setup(bot):
    await bot.add_cog(Analytics(bot))