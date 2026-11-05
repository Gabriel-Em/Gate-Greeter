import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import discord
from discord.ext import commands
from cogs.analytics import Analytics, extract_emojis, EMOJI_SELF_LIMIT, EMOJI_LEADERBOARD_LIMIT


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
        self.bot = MagicMock(spec=commands.Bot)
        self.cog = Analytics(self.bot)

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


if __name__ == "__main__":
    unittest.main()
