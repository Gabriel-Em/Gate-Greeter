import os
import sys
import sqlite3
import unittest
import tempfile
import shutil
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import discord
from discord.ext import commands
from cogs.analytics import Analytics, extract_emojis, EMOJI_SELF_LIMIT, EMOJI_LEADERBOARD_LIMIT
from database import Database


class TestAnalyticsEmojiExtraction(unittest.TestCase):
    """Unit tests for the emoji extraction helper function."""

    def test_extract_unicode_emojis(self):
        text = "Hello! 😀 Having fun 🎉 with 🚀"
        emojis = extract_emojis(text)
        self.assertEqual(emojis, ["😀", "🎉", "🚀"])

    def test_extract_complex_unicode_emojis(self):
        # ZWJ sequences, skin tone modifiers, regional indicator flags, symbols
        text = "Family: 👨‍👩‍👧‍👦, Thumbs up: 👍🏽, Flag: 🇺🇸, Love: ❤️, Dingbat: ✨, Keycap: 1️⃣"
        emojis = extract_emojis(text)
        self.assertIn("👨‍👩‍👧‍👦", emojis)
        self.assertIn("👍🏽", emojis)
        self.assertIn("🇺🇸", emojis)
        self.assertIn("❤️", emojis)
        self.assertIn("✨", emojis)
        self.assertIn("1️⃣", emojis)

    def test_extract_custom_and_animated_emojis(self):
        text = "Look at <:custom_dog:123456789> and <a:party_parrot:987654321>!"
        emojis = extract_emojis(text)
        self.assertEqual(emojis, ["<:custom_dog:123456789>", "<a:party_parrot:987654321>"])

    def test_extract_mixed_emojis_and_duplicates(self):
        text = "🔥 Hot! <:fire_emoji:111> 🔥 Also 😂😂😂"
        emojis = extract_emojis(text)
        self.assertEqual(
            emojis,
            ["<:fire_emoji:111>", "🔥", "🔥", "😂", "😂", "😂"],
        )

    def test_extract_plain_text(self):
        text = "Just standard text with numbers 1 2 3 and symbols #hashtag $100 *bold*."
        emojis = extract_emojis(text)
        self.assertEqual(emojis, [])

    def test_extract_empty_and_whitespace(self):
        self.assertEqual(extract_emojis(""), [])
        self.assertEqual(extract_emojis("    "), [])
        self.assertEqual(extract_emojis(None), [])


class TestEmojiCommands(unittest.IsolatedAsyncioTestCase):
    """Unit tests for the !myemojis and !emojileaderboard commands."""

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_db_file = os.path.join(self.test_dir, "test_analytics.db")
        self.test_db = Database(self.test_db_file)

        self.bot = MagicMock(spec=commands.Bot)
        self.cog = Analytics(self.bot, db=self.test_db)

        # Mock author
        self.author = MagicMock(spec=discord.Member)
        self.author.id = 1001
        self.author.display_name = "Alice"
        self.author.mention = "<@1001>"
        self.author.bot = False
        self.author.display_avatar = MagicMock()
        self.author.display_avatar.url = "http://example.com/alice.png"

        # Mock other user
        self.other_user = MagicMock(spec=discord.Member)
        self.other_user.id = 1002
        self.other_user.display_name = "Bob"
        self.other_user.mention = "<@1002>"
        self.other_user.bot = False
        self.other_user.display_avatar = MagicMock()
        self.other_user.display_avatar.url = "http://example.com/bob.png"

        # Mock bot user
        self.bot_user = MagicMock(spec=discord.Member)
        self.bot_user.id = 9999
        self.bot_user.display_name = "ServerBot"
        self.bot_user.bot = True

        # Mock Guild and Text Channels
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.me = MagicMock()

        self.channel1 = MagicMock(spec=discord.TextChannel)
        self.channel1.name = "general"
        perm1 = MagicMock()
        perm1.read_message_history = True
        self.channel1.permissions_for.return_value = perm1

        self.logs_channel = MagicMock(spec=discord.TextChannel)
        self.logs_channel.name = "mod-logs"
        perm_logs = MagicMock()
        perm_logs.read_message_history = True
        self.logs_channel.permissions_for.return_value = perm_logs

        self.guild.text_channels = [self.channel1, self.logs_channel]

        # Mock Context
        self.ctx = MagicMock()
        self.ctx.guild = self.guild
        self.ctx.author = self.author
        self.ctx.send = AsyncMock()

    async def asyncTearDown(self):
        await self.test_db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_mock_message(self, author, content, days_ago=1):
        msg = MagicMock(spec=discord.Message)
        msg.author = author
        msg.content = content
        msg.created_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return msg

    async def test_myemojis_disabled_by_env(self):
        """Should exit immediately if ENABLE_CMD_MYEMOJIS is False."""
        with patch.object(self.cog, "_is_enabled", return_value=False):
            await self.cog.myemojis.callback(self.cog, self.ctx)
            self.ctx.send.assert_not_called()

    async def test_myemojis_privacy_guard_with_args(self):
        """Should block attempts to scan other users and show a privacy notice."""
        await self.cog.myemojis.callback(self.cog, self.ctx, "@Bob")
        self.ctx.send.assert_called_once()
        sent_content = self.ctx.send.call_args[0][0]
        self.assertIn("Privacy Notice", sent_content)
        self.assertIn("only inspects your own messages", sent_content)

    async def test_myemojis_tallies_own_messages_only(self):
        """Should scan only ctx.author's messages, ignoring other users and excluded channels."""
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        self.ctx.send.return_value = status_msg

        # Channel 1 history
        msgs = [
            self._create_mock_message(self.author, "I love pizza! 🍕 🍕 🎉"),
            self._create_mock_message(self.other_user, "Bob uses rockets! 🚀 🚀 🚀 🚀"),
            self._create_mock_message(self.author, "Another pizza! 🍕 and a cat <:cat:123>"),
            self._create_mock_message(self.bot_user, "Bot emoji: 🤖 🤖"),
        ]

        async def history_iter(*args, **kwargs):
            for m in msgs:
                yield m

        self.channel1.history = MagicMock(side_effect=history_iter)

        await self.cog.myemojis.callback(self.cog, self.ctx)

        # Verify status message was deleted
        status_msg.delete.assert_called_once()

        # Verify embed was sent
        self.assertEqual(self.ctx.send.call_count, 2)
        embed_call = self.ctx.send.call_args_list[1]
        embed = embed_call[1].get("embed")
        self.assertIsNotNone(embed)
        self.assertIn("Alice", embed.author.name)

        # Author used 🍕 (3 times), 🎉 (1 time), <:cat:123> (1 time)
        # Other user's 🚀 and bot's 🤖 should NOT be present
        self.assertIn("🍕", embed.description)
        self.assertIn("🎉", embed.description)
        self.assertIn("<:cat:123>", embed.description)
        self.assertNotIn("🚀", embed.description)
        self.assertNotIn("🤖", embed.description)

    async def test_myemojis_no_emojis_found(self):
        """Should notify when no emojis are found for the author."""
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        self.ctx.send.return_value = status_msg

        msgs = [
            self._create_mock_message(self.author, "Plain text message with no emojis"),
            self._create_mock_message(self.other_user, "Bob used 🚀"),
        ]

        async def history_iter(*args, **kwargs):
            for m in msgs:
                yield m

        self.channel1.history = MagicMock(side_effect=history_iter)

        await self.cog.myemojis.callback(self.cog, self.ctx)

        status_msg.delete.assert_called_once()
        self.assertEqual(self.ctx.send.call_count, 2)
        sent_text = self.ctx.send.call_args_list[1][0][0]
        self.assertIn("No emojis found in your messages", sent_text)

    async def test_emojileaderboard_disabled_by_env(self):
        """Should exit immediately if ENABLE_CMD_EMOJILEADERBOARD is False."""
        with patch.object(self.cog, "_is_enabled", return_value=False):
            await self.cog.emojileaderboard.callback(self.cog, self.ctx)
            self.ctx.send.assert_not_called()

    async def test_emojileaderboard_ranks_and_attributes_top_user(self):
        """Should rank top emojis serverwide and attribute the user who used each emoji the most."""
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        self.ctx.send.return_value = status_msg

        msgs = [
            # Alice uses 💀 2 times, 🔥 5 times
            self._create_mock_message(self.author, "💀 💀 🔥 🔥 🔥 🔥 🔥"),
            # Bob uses 💀 8 times, 🔥 1 time
            self._create_mock_message(self.other_user, "💀 💀 💀 💀 💀 💀 💀 💀 🔥"),
            # Bot uses 🤖 10 times (should be ignored)
            self._create_mock_message(self.bot_user, "🤖 " * 10),
        ]

        async def history_iter(*args, **kwargs):
            for m in msgs:
                yield m

        self.channel1.history = MagicMock(side_effect=history_iter)

        await self.cog.emojileaderboard.callback(self.cog, self.ctx)

        # Confirm log channels are never inspected
        self.logs_channel.history.assert_not_called()

        status_msg.delete.assert_called_once()
        self.assertEqual(self.ctx.send.call_count, 2)
        embed = self.ctx.send.call_args_list[1][1].get("embed")
        self.assertIsNotNone(embed)

        # 💀 total: 10 uses (Top user: Bob with 8)
        # 🔥 total: 6 uses (Top user: Alice with 5)
        # 🤖 should NOT be present
        self.assertIn("💀", embed.description)
        self.assertIn("Top user: **Bob** with 8", embed.description)
        self.assertIn("🔥", embed.description)
        self.assertIn("Top user: **Alice** with 5", embed.description)
        self.assertNotIn("🤖", embed.description)

    async def test_myemojis_top_10_limit(self):
        """Should cap personal emojis at top 10 even if author used more."""
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        self.ctx.send.return_value = status_msg

        # 12 distinct emojis with distinct counts
        emojis_list = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟", "🔥", "✨"]
        # Author uses emoji i, (i+1) times
        msg_parts = []
        for count, emo in enumerate(emojis_list, 1):
            msg_parts.append((emo + " ") * count)

        msgs = [self._create_mock_message(self.author, " ".join(msg_parts))]

        async def history_iter(*args, **kwargs):
            for m in msgs:
                yield m

        self.channel1.history = MagicMock(side_effect=history_iter)

        await self.cog.myemojis.callback(self.cog, self.ctx)

        embed = self.ctx.send.call_args_list[1][1].get("embed")
        lines = embed.description.split("\n")
        self.assertEqual(len(lines), 10)
        # Lowest count emojis ("1️⃣" and "2️⃣") should be excluded
        self.assertNotIn("1️⃣", embed.description)
        self.assertNotIn("2️⃣", embed.description)
        # Highest count emojis should be included
        self.assertIn("✨", embed.description)
        self.assertIn("🔥", embed.description)

    async def test_emojileaderboard_no_emojis_found(self):
        """Should notify when no emojis are found serverwide."""
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        self.ctx.send.return_value = status_msg

        msgs = [
            self._create_mock_message(self.author, "No emojis here"),
            self._create_mock_message(self.other_user, "None here either"),
        ]

        async def history_iter(*args, **kwargs):
            for m in msgs:
                yield m

        self.channel1.history = MagicMock(side_effect=history_iter)

        await self.cog.emojileaderboard.callback(self.cog, self.ctx)

        # Confirm log channels are never inspected
        self.logs_channel.history.assert_not_called()

        status_msg.delete.assert_called_once()
        self.assertEqual(self.ctx.send.call_count, 2)
        sent_text = self.ctx.send.call_args_list[1][0][0]
        self.assertIn("No emojis found in server messages", sent_text)


    async def test_emojileaderboard_top_15_limit(self):
        """Should cap server leaderboard at top 15 even if 20 emojis are used."""
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        self.ctx.send.return_value = status_msg

        # 18 distinct emojis
        emojis_list = [
            "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟",
            "🔥", "✨", "🎉", "🚀", "💀", "❤️", "🍕", "🐶"
        ]
        msg_parts = []
        for count, emo in enumerate(emojis_list, 1):
            msg_parts.append((emo + " ") * count)

        msgs = [self._create_mock_message(self.author, " ".join(msg_parts))]

        async def history_iter(*args, **kwargs):
            for m in msgs:
                yield m

        self.channel1.history = MagicMock(side_effect=history_iter)

        await self.cog.emojileaderboard.callback(self.cog, self.ctx)

        embed = self.ctx.send.call_args_list[1][1].get("embed")
        lines = embed.description.split("\n")
        self.assertEqual(len(lines), 15)
        # Lowest count emojis ("1️⃣", "2️⃣", "3️⃣") should be excluded
        self.assertNotIn("1️⃣", embed.description)
        self.assertNotIn("2️⃣", embed.description)
        self.assertNotIn("3️⃣", embed.description)
        # Highest count emojis should be included
        self.assertIn("🐶", embed.description)
        self.assertIn("🍕", embed.description)


class TestMonthlyActivityCommand(unittest.IsolatedAsyncioTestCase):
    """Unit tests for the get_past_months calculation and !activity monthly command."""

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_db_file = os.path.join(self.test_dir, "test_monthly.db")
        self.test_db = Database(self.test_db_file)
        await self.test_db.initialize()

        self.bot = MagicMock(spec=commands.Bot)
        self.cog = Analytics(self.bot, db=self.test_db)

        self.guild = MagicMock(spec=discord.Guild)
        self.guild.me = MagicMock()
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.name = "general"
        self.channel.permissions_for.return_value.read_message_history = True
        self.guild.text_channels = [self.channel]

        self.ctx = MagicMock()
        self.ctx.guild = self.guild
        self.ctx.send = AsyncMock()

    async def asyncTearDown(self):
        await self.test_db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_get_past_months_calculation(self):
        """Verifies rolling month window calculation across years and boundaries."""
        ref_dt = datetime(2026, 11, 10, tzinfo=timezone.utc)

        # 1 month: current month only
        m1 = self.cog._get_past_months(1, ref_dt)
        self.assertEqual(m1, [(2026, 11)])

        # 2 months
        m2 = self.cog._get_past_months(2, ref_dt)
        self.assertEqual(m2, [(2026, 10), (2026, 11)])

        # 12 months: exactly 1 year (Dec 2025 to Nov 2026)
        m12 = self.cog._get_past_months(12, ref_dt)
        self.assertEqual(len(m12), 12)
        self.assertEqual(m12[0], (2025, 12))
        self.assertEqual(m12[-1], (2026, 11))

        # 24 months: exactly 2 years (Dec 2024 to Nov 2026)
        m24 = self.cog._get_past_months(24, ref_dt)
        self.assertEqual(len(m24), 24)
        self.assertEqual(m24[0], (2024, 12))
        self.assertEqual(m24[-1], (2026, 11))

        # Boundary condition: year rollover across January
        jan_ref = datetime(2026, 1, 15, tzinfo=timezone.utc)
        m3_jan = self.cog._get_past_months(3, jan_ref)
        self.assertEqual(m3_jan, [(2025, 11), (2025, 12), (2026, 1)])

        # Degenerate inputs: 0 or negative
        self.assertEqual(self.cog._get_past_months(0, ref_dt), [])
        self.assertEqual(self.cog._get_past_months(-5, ref_dt), [])

    async def test_activity_invalid_or_extreme_arguments(self):
        """Rejects zero, negative, or excessively large month arguments."""
        await self.cog.activity.callback(self.cog, self.ctx, months=0)
        self.ctx.send.assert_called_once()
        self.assertIn("positive number of months", self.ctx.send.call_args[0][0])

        self.ctx.send.reset_mock()
        await self.cog.activity.callback(self.cog, self.ctx, months=-6)
        self.ctx.send.assert_called_once()
        self.assertIn("positive number of months", self.ctx.send.call_args[0][0])

        self.ctx.send.reset_mock()
        await self.cog.activity.callback(self.cog, self.ctx, months=121)
        self.ctx.send.assert_called_once()
        self.assertIn("Maximum allowed range", self.ctx.send.call_args[0][0])

    async def test_activity_disabled_by_env(self):
        """Immediately returns if ENABLE_CMD_ACTIVITY is set to False."""
        with patch.object(self.cog, "_is_enabled", return_value=False):
            await self.cog.activity.callback(self.cog, self.ctx, months=12)
            self.ctx.send.assert_not_called()

    async def test_activity_no_data(self):
        """Notifies user if no messages are recorded in the requested window."""
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        self.ctx.send.return_value = status_msg

        # Patch scraping to do nothing and past months are marked complete
        fixed_now = datetime(2026, 11, 10, tzinfo=timezone.utc)
        with patch("cogs.analytics.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
            # Mark the past month complete so it does not scrape
            await self.test_db.set_month_complete(2026, 10, True)

            await self.cog.activity.callback(self.cog, self.ctx, months=2)

        # Confirm message indicates no message activity
        last_send = self.ctx.send.call_args_list[-1][0][0]
        self.assertIn("No message activity recorded", last_send)

    async def test_activity_scrapes_missing_past_months_and_generates_graph(self):
        """Option A: Automatically scrapes incomplete past months, then generates and sends graph."""
        status_msg = AsyncMock()
        status_msg.delete = AsyncMock()
        status_msg.edit = AsyncMock()
        self.ctx.send.return_value = status_msg

        # 3 months: Sep 2026, Oct 2026, Nov 2026
        # Pre-seed: Sep 2026 is marked complete in DB
        await self.test_db.set_month_complete(2026, 9, True)
        await self.test_db.record_messages_batch(
            {(2026, 9, 101, "Alice"): 100}, {}, {}
        )
        # Nov 2026 is current month (already has 50 messages)
        await self.test_db.record_messages_batch(
            {(2026, 11, 101, "Alice"): 50}, {}, {}
        )
        # Oct 2026 is incomplete in the past. It should be scraped by Option A.
        scrape_calls = []

        async def mock_scrape(guild, year, month, s_msg=None, progress_prefix=""):
            scrape_calls.append((year, month))
            await self.test_db.record_messages_batch(
                {(year, month, 102, "Bob"): 75}, {}, {}
            )
            await self.test_db.set_month_complete(year, month, True)

        self.cog._scrape_month_data = AsyncMock(side_effect=mock_scrape)

        fixed_now = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)
        with patch("cogs.analytics.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

            await self.cog.activity.callback(self.cog, self.ctx, months=3)

        # Oct 2026 (incomplete past month) MUST have been scraped
        self.assertEqual(scrape_calls, [(2026, 10)])

        # Status message was deleted or edited
        status_msg.delete.assert_called_once()

        # Final response should have an embed and a PNG file attachment
        send_calls = self.ctx.send.call_args_list
        final_call_kwargs = send_calls[-1][1]
        self.assertIn("embed", final_call_kwargs)
        self.assertIn("files", final_call_kwargs)

        embed = final_call_kwargs["embed"]
        self.assertIn("Server Activity (Past 3 Months)", embed.title)
        self.assertIn("225", embed.description)  # Total: 100 + 75 + 50 = 225 messages

        files = final_call_kwargs["files"]
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].filename, "monthly_activity.png")


class TestAnalyticsSync(unittest.IsolatedAsyncioTestCase):
    """History scans (scrape, baseline, catch-up) and buffer persistence must never double count."""

    NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.test_dir, "test_sync.db"))
        await self.db.initialize()

        self.bot = MagicMock(spec=commands.Bot)
        self.cog = Analytics(self.bot, db=self.db)

        self.alice = self._user(1001, "alice")
        self.bob = self._user(1002, "bob")
        self.robot = self._user(9999, "robot", bot=True)

        self.guild = MagicMock(spec=discord.Guild)
        self.guild.name = "Test Guild"
        self.guild.me = MagicMock()
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = 42
        self.channel.name = "general"
        self.channel.last_message_id = None
        self.channel.permissions_for.return_value.read_message_history = True
        self.guild.text_channels = [self.channel]
        self.bot.guilds = [self.guild]

        self.messages = []
        self.channel.history = MagicMock(side_effect=self._history)

        self.ctx = MagicMock()
        self.ctx.guild = self.guild
        self.ctx.author = self.alice
        self.ctx.send = AsyncMock()

    async def asyncTearDown(self):
        await self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    @staticmethod
    def _user(uid, name, bot=False):
        user = MagicMock(spec=discord.Member)
        user.id = uid
        user.display_name = name
        user.mention = f"<@{uid}>"
        user.bot = bot
        user.__str__.return_value = name
        return user

    def _msg(self, author, created_at, content="hello"):
        msg = MagicMock(spec=discord.Message)
        msg.id = discord.utils.time_snowflake(created_at) + len(self.messages) + 1
        msg.author = author
        msg.content = content
        msg.created_at = created_at
        msg.channel = self.channel
        self.messages.append(msg)
        return msg

    @staticmethod
    def _as_snowflake(bound):
        if bound is None:
            return None
        if isinstance(bound, datetime):
            return discord.utils.time_snowflake(bound)
        return bound.id

    def _history(self, limit=None, after=None, before=None, oldest_first=None):
        """Mimics TextChannel.history: exclusive snowflake bounds, oldest first."""
        low = self._as_snowflake(after)
        high = self._as_snowflake(before)
        selected = [
            m for m in sorted(self.messages, key=lambda m: m.id)
            if (low is None or m.id > low) and (high is None or m.id < high)
        ]

        async def iterate():
            for m in selected:
                yield m

        return iterate()

    def _frozen_now(self):
        patcher = patch("cogs.analytics.datetime")
        mock_dt = patcher.start()
        self.addCleanup(patcher.stop)
        mock_dt.now.return_value = self.NOW
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

    # ---- month bounds -------------------------------------------------------

    def test_month_bounds_are_utc_with_exclusive_end(self):
        self.assertEqual(
            Analytics._get_month_bounds(2026, 2),
            (datetime(2026, 2, 1, tzinfo=timezone.utc), datetime(2026, 3, 1, tzinfo=timezone.utc)),
        )
        self.assertEqual(
            Analytics._get_month_bounds(2026, 12),
            (datetime(2026, 12, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc)),
        )

    # ---- monthly scrape -----------------------------------------------------

    async def test_scrape_replaces_live_counts_and_leaves_other_tables_alone(self):
        self._msg(self.alice, datetime(2020, 9, 30, 23, 59, 59, tzinfo=timezone.utc))
        self._msg(self.alice, datetime(2020, 10, 1, 0, 0, 0, 1000, tzinfo=timezone.utc))
        self._msg(self.alice, datetime(2020, 10, 20, 10, 0, 0, tzinfo=timezone.utc))
        self._msg(self.robot, datetime(2020, 10, 21, 10, 0, 0, tzinfo=timezone.utc))
        self._msg(self.bob, datetime(2020, 10, 31, 23, 59, 59, 500000, tzinfo=timezone.utc))
        self._msg(self.bob, datetime(2020, 11, 1, 0, 0, 0, tzinfo=timezone.utc))

        # State left behind by live tracking of the same month.
        await self.db.record_messages_batch(
            {(2020, 10, 1001, "alice"): 2},
            {"2020-10-20 10": 1},
            {42: (777, "2020-12-01T00:00:00+00:00")},
        )

        for _ in range(2):
            await self.cog._scrape_month_data(self.guild, 2020, 10)
            self.assertEqual(
                await self.db.get_top_users(2020, 10), [("alice", 2), ("bob", 1)]
            )

        self.assertTrue(await self.db.is_month_complete(2020, 10))
        self.assertEqual(await self.db.get_top_users(2020, 9), [])
        self.assertEqual(await self.db.get_top_users(2020, 11), [])
        self.assertEqual(
            await self.db.get_activity_30_days("2020-01-01 00"), [("2020-10-20 10", 1)]
        )
        self.assertEqual(await self.db.get_all_channel_syncs(), {42: 777})

    # ---- 30-day baseline ----------------------------------------------------

    async def test_baseline_rebuilds_window_without_double_counting(self):
        self._frozen_now()
        self._msg(self.alice, datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc), "too old 🔥")
        self._msg(self.alice, datetime(2026, 10, 20, 9, 0, tzinfo=timezone.utc), "🎉")
        self._msg(self.bob, datetime(2026, 11, 2, 8, 30, tzinfo=timezone.utc), "yo")
        self._msg(self.alice, datetime(2026, 11, 2, 8, 45, tzinfo=timezone.utc), "plain")
        self._msg(self.robot, datetime(2026, 11, 3, 8, 30, tzinfo=timezone.utc), "🤖")
        latest = self._msg(self.alice, datetime(2026, 11, 9, 10, 15, tzinfo=timezone.utc), "hi 🔥🔥")

        # Stale rows inside the window, plus an older month that must survive.
        await self.db.record_messages_batch(
            {(2026, 11, 1001, "alice"): 40, (2026, 10, 1001, "alice"): 50},
            {"2026-11-09 10": 40},
            {},
        )
        await self.db.record_emoji_batch({("2026-11-09", 1001, "alice", "🔥"): 40})
        # The newest message is also still pending in the in-memory buffer.
        await self.cog.on_message(latest)

        for _ in range(2):
            await self.cog.initial_30d_backfill(self.guild)

            self.assertEqual(
                await self.db.get_activity_30_days("2026-01-01 00"),
                [("2026-10-20 09", 1), ("2026-11-02 08", 2), ("2026-11-09 10", 1)],
            )
            self.assertEqual(
                await self.db.get_top_users(2026, 11), [("alice", 2), ("bob", 1)]
            )
            self.assertEqual(await self.db.get_top_users(2026, 10), [("alice", 50)])
            self.assertEqual(
                await self.db.get_user_top_emojis(1001, "2026-01-01"),
                ([("🔥", 2), ("🎉", 1)], 2, 3),
            )
            self.assertEqual(await self.db.get_all_channel_syncs(), {42: latest.id})

    # ---- catch-up -----------------------------------------------------------

    async def test_catch_up_counts_each_missed_message_once(self):
        self._frozen_now()
        seen = self._msg(self.alice, datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc))
        self._msg(self.alice, datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc), "🔥")
        self._msg(self.robot, datetime(2026, 11, 9, 10, 30, tzinfo=timezone.utc))
        pending = self._msg(self.bob, datetime(2026, 11, 9, 11, 0, tzinfo=timezone.utc))
        await self.db.record_messages_batch(
            {(2026, 11, 1001, "alice"): 1}, {"2026-11-08 10": 1}, {42: (seen.id, "x")}
        )
        # Received over the gateway but not flushed yet when the sync starts.
        await self.cog.on_message(pending)

        for _ in range(2):
            await self.cog.catch_up_sync(self.guild)
            self.assertEqual(
                await self.db.get_top_users(2026, 11), [("alice", 2), ("bob", 1)]
            )
            self.assertEqual(
                await self.db.get_activity_30_days("2026-01-01 00"),
                [("2026-11-08 10", 1), ("2026-11-09 10", 1), ("2026-11-09 11", 1)],
            )
            self.assertEqual(await self.db.get_all_channel_syncs(), {42: pending.id})

        self.assertEqual(
            await self.db.get_user_top_emojis(1001, "2026-01-01"), ([("🔥", 1)], 1, 1)
        )

    async def test_catch_up_seeds_watermark_for_unseen_channel_without_counting(self):
        self._frozen_now()
        self._msg(self.alice, datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc))
        self.channel.last_message_id = 555

        await self.cog.catch_up_sync(self.guild)

        self.assertEqual(await self.db.get_top_users(2026, 11), [])
        self.assertEqual(await self.db.get_all_channel_syncs(), {42: 555})

    async def test_flush_waits_for_catch_up_after_a_new_gateway_session(self):
        """A message received right after connecting must not move the watermark past the downtime gap."""
        self._frozen_now()
        await self.db.set_initial_sync_done(True)
        seen = self._msg(self.alice, datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc))
        self._msg(self.alice, datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc))  # sent while offline
        live = self._msg(self.bob, datetime(2026, 11, 9, 11, 0, tzinfo=timezone.utc))
        await self.db.record_messages_batch(
            {(2026, 11, 1001, "alice"): 1}, {}, {42: (seen.id, "x")}
        )

        await self.cog.on_connect()
        await self.cog.on_message(live)
        await self.cog.flush_buffer()  # periodic flush firing before on_ready
        self.assertEqual(await self.db.get_all_channel_syncs(), {42: seen.id})

        await self.cog.on_ready()
        self.assertEqual(await self.db.get_top_users(2026, 11), [("alice", 2), ("bob", 1)])
        self.assertEqual(await self.db.get_all_channel_syncs(), {42: live.id})

        # Normal flushing resumes once the catch-up has run.
        later = self._msg(self.bob, datetime(2026, 11, 10, 11, 59, tzinfo=timezone.utc))
        await self.cog.on_message(later)
        await self.cog.flush_buffer()
        self.assertEqual(await self.db.get_top_users(2026, 11), [("alice", 2), ("bob", 2)])
        self.assertEqual(await self.db.get_all_channel_syncs(), {42: later.id})

    # ---- on_ready -----------------------------------------------------------

    async def test_on_ready_marks_baseline_done_only_after_success(self):
        self.cog.initial_30d_backfill = AsyncMock(side_effect=RuntimeError("boom"))
        await self.cog.on_ready()
        self.assertFalse(await self.db.is_initial_sync_done())

        self.cog.initial_30d_backfill = AsyncMock()
        await self.cog.on_ready()
        self.assertTrue(await self.db.is_initial_sync_done())

    async def test_on_ready_uses_catch_up_once_baseline_exists(self):
        await self.db.set_initial_sync_done(True)
        self.cog.initial_30d_backfill = AsyncMock()
        self.cog.catch_up_sync = AsyncMock()
        await self.cog.on_ready()
        self.cog.initial_30d_backfill.assert_not_awaited()
        self.cog.catch_up_sync.assert_awaited_once_with(self.guild)

    # ---- live buffering -----------------------------------------------------

    async def test_on_message_buffers_and_flush_persists_once(self):
        msg = self._msg(self.alice, datetime(2026, 11, 9, 10, 15, tzinfo=timezone.utc), "🔥 🔥 🎉")
        await self.cog.on_message(msg)
        await self.cog.on_message(self._msg(self.robot, datetime(2026, 11, 9, 10, 16, tzinfo=timezone.utc)))

        await self.cog.flush_buffer()
        await self.cog.flush_buffer()

        self.assertEqual(await self.db.get_top_users(2026, 11), [("alice", 1)])
        self.assertEqual(await self.db.get_activity_30_days("2026-01-01 00"), [("2026-11-09 10", 1)])
        self.assertEqual(
            await self.db.get_user_top_emojis(1001, "2026-01-01"),
            ([("🔥", 2), ("🎉", 1)], 2, 3),
        )
        self.assertEqual(await self.db.get_all_channel_syncs(), {42: msg.id})

    async def test_on_message_ignores_log_channels_and_non_text_channels(self):
        log_channel = MagicMock(spec=discord.TextChannel)
        log_channel.name = "Mod-Logs"
        in_logs = self._msg(self.alice, datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc))
        in_logs.channel = log_channel
        in_dm = self._msg(self.alice, datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc))
        in_dm.channel = MagicMock(spec=discord.DMChannel)

        await self.cog.on_message(in_logs)
        await self.cog.on_message(in_dm)
        await self.cog.flush_buffer()

        self.assertEqual(await self.db.get_top_users(2026, 11), [])

    async def test_only_dash_logs_channels_are_excluded(self):
        cases = (("blog", True), ("catalogs", True), ("dev-log", True), ("bot-logs", False), ("BOT-LOGS", False))
        for channel_id, (name, counted) in enumerate(cases, start=100):
            channel = MagicMock(spec=discord.TextChannel)
            channel.id = channel_id
            channel.name = name
            msg = self._msg(self.alice, datetime(2026, 11, 9, 10, 0, tzinfo=timezone.utc))
            msg.channel = channel
            await self.cog.on_message(msg)
            await self.cog.flush_buffer()
            with self.subTest(channel=name):
                self.assertEqual(channel.id in await self.db.get_all_channel_syncs(), counted)

    # ---- !rescrape ----------------------------------------------------------

    async def test_rescrape_replaces_a_finished_month(self):
        self._frozen_now()
        self._msg(self.alice, datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc))
        self._msg(self.bob, datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc))
        self._msg(self.bob, datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc))
        # Inflated counts stored for a month already flagged as archived.
        await self.db.record_messages_batch({(2026, 10, 1001, "alice"): 2, (2026, 10, 1002, "bob"): 4}, {}, {})
        await self.db.set_month_complete(2026, 10, True)
        status_msg = AsyncMock()
        self.ctx.send.return_value = status_msg

        await self.cog.rescrape.callback(self.cog, self.ctx, 2026, 10)

        self.assertEqual(await self.db.get_top_users(2026, 10), [("bob", 2), ("alice", 1)])
        self.assertTrue(await self.db.is_month_complete(2026, 10))
        final = status_msg.edit.call_args.kwargs["content"]
        self.assertIn("2026-10", final)
        self.assertIn("3 messages", final)

    async def test_rescrape_rejects_unfinished_and_invalid_months(self):
        self._frozen_now()
        self.cog._scrape_month_data = AsyncMock()
        for year, month in ((2026, 11), (2026, 12), (2027, 1), (2026, 0), (2026, 13), (2014, 5)):
            with self.subTest(year=year, month=month):
                self.ctx.send.reset_mock()
                await self.cog.rescrape.callback(self.cog, self.ctx, year, month)
                self.ctx.send.assert_awaited_once()
                self.assertTrue(self.ctx.send.call_args.args[0].startswith("⚠️"))
        self.cog._scrape_month_data.assert_not_awaited()

    async def test_failed_flush_keeps_data_for_the_next_attempt(self):
        msg = self._msg(self.alice, datetime(2026, 11, 9, 10, 15, tzinfo=timezone.utc), "🔥")
        await self.cog.on_message(msg)

        real_record = self.db.record_messages_batch
        self.db.record_messages_batch = AsyncMock(side_effect=sqlite3.OperationalError("database is locked"))
        with self.assertRaises(sqlite3.OperationalError):
            await self.cog.flush_buffer()
        self.assertEqual(await self.db.get_top_users(2026, 11), [])

        self.db.record_messages_batch = real_record
        await self.cog.flush_buffer()
        await self.cog.flush_buffer()

        self.assertEqual(await self.db.get_top_users(2026, 11), [("alice", 1)])
        self.assertEqual(
            await self.db.get_user_top_emojis(1001, "2026-01-01"), ([("🔥", 1)], 1, 1)
        )
        self.assertEqual(await self.db.get_all_channel_syncs(), {42: msg.id})

    # ---- commands read the database once the baseline exists ---------------

    async def test_myemojis_reads_database_without_scanning_history(self):
        self._frozen_now()
        await self.db.set_initial_sync_done(True)
        await self.db.record_emoji_batch(
            {("2026-11-09", 1001, "alice", "🔥"): 3, ("2026-11-09", 1002, "bob", "🚀"): 9}
        )
        with patch("cogs.analytics.discord.utils.utcnow", return_value=self.NOW):
            await self.cog.myemojis.callback(self.cog, self.ctx)

        self.channel.history.assert_not_called()
        embed = self.ctx.send.call_args_list[-1].kwargs["embed"]
        self.assertEqual(embed.description, "**1.** 🔥 — **3** uses")

    async def test_myemojis_without_data_does_not_scan_history_after_baseline(self):
        await self.db.set_initial_sync_done(True)
        self._msg(self.alice, datetime.now(timezone.utc) - timedelta(days=1), "🔥")

        await self.cog.myemojis.callback(self.cog, self.ctx)

        self.channel.history.assert_not_called()
        self.assertIn("No emojis found in your messages", self.ctx.send.call_args_list[-1].args[0])

    async def test_emojileaderboard_without_data_does_not_scan_history_after_baseline(self):
        await self.db.set_initial_sync_done(True)
        self._msg(self.alice, datetime.now(timezone.utc) - timedelta(days=1), "🔥")

        await self.cog.emojileaderboard.callback(self.cog, self.ctx)

        self.channel.history.assert_not_called()
        self.assertIn("No emojis found in server messages", self.ctx.send.call_args_list[-1].args[0])

    async def test_activity30_without_data_does_not_scan_history_after_baseline(self):
        await self.db.set_initial_sync_done(True)
        self._msg(self.alice, datetime.now(timezone.utc) - timedelta(days=1))

        await self.cog.activity30.callback(self.cog, self.ctx)

        self.channel.history.assert_not_called()
        self.assertEqual(len(self.ctx.send.call_args_list[-1].kwargs["files"]), 2)

    async def test_activity30_bootstrap_scan_only_fills_hourly_table(self):
        self._frozen_now()
        self._msg(self.alice, datetime(2026, 11, 9, 10, 15, tzinfo=timezone.utc))
        await self.db.record_messages_batch({(2026, 11, 1001, "alice"): 1}, {}, {42: (7, "x")})

        await self.cog.activity30.callback(self.cog, self.ctx)

        self.assertEqual(await self.db.get_activity_30_days("2026-01-01 00"), [("2026-11-09 10", 1)])
        self.assertEqual(await self.db.get_top_users(2026, 11), [("alice", 1)])
        self.assertEqual(await self.db.get_all_channel_syncs(), {42: 7})

    # ---- !topusers ----------------------------------------------------------

    async def test_topusers_rejects_years_outside_supported_range(self):
        self._frozen_now()
        self.cog._scrape_month_data = AsyncMock()
        for year in (0, -1, 2014, 2027, 99999):
            with self.subTest(year=year):
                self.ctx.send.reset_mock()
                await self.cog.topusers.callback(self.cog, self.ctx, year)
                self.ctx.send.assert_awaited_once()
                self.assertIn("Year must be between 2015 and 2026", self.ctx.send.call_args.args[0])
        self.cog._scrape_month_data.assert_not_awaited()

    async def test_topusers_scrapes_only_finished_unarchived_months(self):
        self._frozen_now()
        await self.db.set_month_complete(2026, 1, True)
        for month in range(2, 10):
            await self.db.set_month_complete(2026, month, True)
        await self.db.record_messages_batch(
            {(2026, 11, 1001, "alice"): 5, (2026, 3, 1002, "bob"): 9}, {}, {}
        )
        self.cog._scrape_month_data = AsyncMock()

        await self.cog.topusers.callback(self.cog, self.ctx, 2026)

        scraped = [call.args[1:3] for call in self.cog._scrape_month_data.await_args_list]
        self.assertEqual(scraped, [(2026, 10)])
        embed = self.ctx.send.call_args_list[-1].kwargs["embed"]
        self.assertEqual(embed.description, "**1.** bob — 9 messages\n**2.** alice — 5 messages")


if __name__ == "__main__":
    unittest.main()
