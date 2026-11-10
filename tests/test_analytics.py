import os
import sys
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


if __name__ == "__main__":
    unittest.main()
