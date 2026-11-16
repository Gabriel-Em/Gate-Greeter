import os
import random
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import discord
from discord.ext import commands
from cogs.moderation import Moderation, QuarantineReleaseButton
from database import Database


class TestQuarantineAndModeration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_db_file = os.path.join(self.test_dir, "test_moderation.db")
        self.test_db = Database(self.test_db_file)

        # Mock Bot
        self.bot = MagicMock(spec=commands.Bot)
        self.bot.add_dynamic_items = MagicMock()
        self.bot.remove_dynamic_items = MagicMock()
        self.bot.get_guild.return_value = None

        # Instantiate Cog
        self.cog = Moderation(self.bot, db=self.test_db)
        self.cog.quarantine_role_id = 999
        self.cog.inmate_role_id = 888
        self.cog.quarantine_channel_id = 111
        self.cog.quarantine_logs_channel_id = 222
        self.cog.category_role_ids = [500]
        self.cog.staff_role_ids = [700]
        self.cog.quarantine_days = 7
        self.cog.quarantine_new_member_days = 7

        # Setup mock guild & roles
        self.guild = MagicMock(spec=discord.Guild)
        self.guild.name = "Test Guild"
        self.guild.id = 1000
        self.guild.fetch_member = AsyncMock(side_effect=lambda uid: self.guild.get_member(uid))
        self.bot.get_guild.return_value = self.guild

        self.default_role = MagicMock(spec=discord.Role, id=1000, name="@everyone", managed=False)
        self.guild.default_role = self.default_role

        self.q_role = MagicMock(spec=discord.Role, id=999, name="Quarantine", managed=False)
        self.category_role = MagicMock(spec=discord.Role, id=500, name="Category", managed=False)
        # Managed by Discord (e.g. Server Booster): bots can neither add nor remove it.
        self.booster_role = MagicMock(spec=discord.Role, id=650, name="Server Booster", managed=True)
        self.user_role1 = MagicMock(spec=discord.Role, id=601, name="Role1", managed=False)
        self.user_role2 = MagicMock(spec=discord.Role, id=602, name="Role2", managed=False)
        self.staff_role = MagicMock(spec=discord.Role, id=700, name="Moderator", managed=False)
        self.bot_top_role = MagicMock(spec=discord.Role, id=9999, name="Bot Top", managed=True)

        # Top role comparison setup
        self.bot_top_role.__gt__ = lambda self, other: True
        self.user_role1.__lt__ = lambda self, other: True
        self.user_role2.__lt__ = lambda self, other: True
        self.q_role.__lt__ = lambda self, other: True

        self.bot_member = MagicMock(spec=discord.Member)
        self.bot_member.top_role = self.bot_top_role
        self.guild.me = self.bot_member

        def get_role_mock(rid):
            mapping = {
                1000: self.default_role,
                999: self.q_role,
                500: self.category_role,
                601: self.user_role1,
                602: self.user_role2,
                700: self.staff_role,
                650: self.booster_role,
            }
            return mapping.get(rid)

        self.guild.get_role.side_effect = get_role_mock

        # Setup mock channels
        self.quarantine_channel = MagicMock(spec=discord.TextChannel, id=111, name="quarantine")
        self.quarantine_channel.send = AsyncMock()

        self.quarantine_logs_channel = MagicMock(spec=discord.TextChannel, id=222, name="quarantine-logs")
        self.quarantine_logs_channel.send = AsyncMock()

        def get_channel_mock(cid):
            mapping = {
                111: self.quarantine_channel,
                222: self.quarantine_logs_channel,
            }
            return mapping.get(cid)

        self.guild.get_channel.side_effect = get_channel_mock

    async def asyncTearDown(self):
        await self.test_db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _create_member(self, user_id=12345, name="Newbie", created_days_ago=10, joined_days_ago=2, roles=None):
        member = MagicMock(spec=discord.Member)
        member.id = user_id
        member.name = name
        member.display_name = name
        member.mention = f"<@{user_id}>"
        member.bot = False
        member.guild = self.guild
        member.roles = list(roles) if roles else [self.default_role]
        member.created_at = datetime.now(timezone.utc) - timedelta(days=created_days_ago)
        member.joined_at = datetime.now(timezone.utc) - timedelta(days=joined_days_ago)
        member.edit = AsyncMock()
        member.send = AsyncMock()
        return member

    async def test_auto_quarantine_account_too_new_on_join(self):
        """Users with accounts created <= 7 days ago are auto-quarantined on join."""
        member = self._create_member(created_days_ago=3, joined_days_ago=0)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            self.guild.get_member.return_value = member
            await self.cog.on_member_join(member)

        # Member roles edited to Quarantine + Category
        member.edit.assert_awaited_once()
        edited_roles = member.edit.call_args.kwargs["roles"]
        self.assertIn(self.q_role, edited_roles)

        # Message sent in quarantine channel mentioning member
        self.quarantine_channel.send.assert_awaited_once()
        sent_content = self.quarantine_channel.send.call_args.args[0]
        self.assertIn(member.mention, sent_content)
        self.assertIn("too new", sent_content)

        # Release button attached
        view = self.quarantine_channel.send.call_args.kwargs.get("view")
        self.assertIsNotNone(view)
        button = view.children[0]
        self.assertEqual(button.custom_id, f"quarantine:release:{member.id}")

        # Quarantine log sent
        self.quarantine_logs_channel.send.assert_awaited_once()

    async def test_automod_new_member_monitoring(self):
        """New members (joined < 7 days ago) triggering AutoMod are dragged to quarantine."""
        member = self._create_member(
            created_days_ago=30,  # Account is older, but server membership is new
            joined_days_ago=3,   # Joined 3 days ago (< 7)
            roles=[self.default_role, self.category_role, self.user_role1, self.user_role2]
        )
        self.guild.get_member.return_value = member

        # Mock AutoMod Action
        execution = MagicMock(spec=discord.AutoModAction)
        execution.guild = self.guild
        execution.user_id = member.id
        execution.member = member
        execution.rule_id = 9999
        execution.matched_keyword = "banned_word"
        execution.matched_content = "some banned_word here"
        execution.channel_id = 333
        mock_rule = MagicMock()
        mock_rule.name = "Banned Words Filter"
        execution.fetch_rule = AsyncMock(return_value=mock_rule)

        await self.cog.on_automod_action(execution)

        # Member roles edited to quarantine
        member.edit.assert_awaited_once()
        edited_roles = member.edit.call_args.kwargs["roles"]
        self.assertIn(self.q_role, edited_roles)
        self.assertNotIn(self.user_role1, edited_roles)

        # Prior roles saved in database
        q_record = await self.cog.db.get_quarantine_record(member.id)
        self.assertIsNotNone(q_record)
        saved_roles = q_record["roles"]
        self.assertIn(self.user_role1.id, saved_roles)
        self.assertIn(self.user_role2.id, saved_roles)

        # User notified in quarantine channel with their mention and AutoMod rule
        self.quarantine_channel.send.assert_awaited_once()
        sent_content = self.quarantine_channel.send.call_args.args[0]
        self.assertIn(member.mention, sent_content)
        self.assertIn("flagged by AutoMod", sent_content)
        self.assertIn("Banned Words Filter", sent_content)

    async def test_automod_older_member_ignored(self):
        """Members who joined 7+ days ago are NOT quarantined by AutoMod monitoring."""
        member = self._create_member(
            created_days_ago=60,
            joined_days_ago=14,  # Joined 14 days ago (>= 7)
            roles=[self.default_role, self.user_role1]
        )
        self.guild.get_member.return_value = member

        execution = MagicMock(spec=discord.AutoModAction)
        execution.guild = self.guild
        execution.user_id = member.id
        execution.member = member
        execution.fetch_rule = AsyncMock()

        await self.cog.on_automod_action(execution)

        # Member should NOT be edited
        member.edit.assert_not_awaited()
        self.quarantine_channel.send.assert_not_awaited()

    async def test_automod_rejoining_member_quarantined(self):
        """Account created 5 years ago that rejoined the server minutes ago is quarantined upon AutoMod violation."""
        member = self._create_member(
            created_days_ago=1825,  # 5-year-old Discord account
            joined_days_ago=0.005,  # Rejoined ~7 minutes ago (< 7 days)
            roles=[self.default_role, self.category_role, self.user_role1],
        )
        self.guild.get_member.return_value = member

        execution = MagicMock(spec=discord.AutoModAction)
        execution.guild_id = self.guild.id
        execution.guild = self.guild
        execution.user_id = member.id
        execution.member = member
        execution.rule_id = 1111
        execution.matched_keyword = "banned_slur"
        execution.matched_content = "some banned_slur here"
        execution.channel_id = 333
        mock_rule = MagicMock()
        mock_rule.name = "Slurs Filter"
        execution.fetch_rule = AsyncMock(return_value=mock_rule)

        await self.cog.on_automod_action(execution)

        # Successfully quarantined
        member.edit.assert_awaited_once()
        edited_roles = member.edit.call_args.kwargs["roles"]
        self.assertIn(self.q_role, edited_roles)
        self.assertNotIn(self.user_role1, edited_roles)

        # Added to ledger with type automod
        q_record = await self.cog.db.get_quarantine_record(member.id)
        self.assertIsNotNone(q_record)
        self.assertEqual(q_record["type"], "automod")

    async def test_automod_already_quarantined_user_in_ledger_ignored(self):
        """User already in quarantine ledger who triggers AutoMod in quarantine does not trigger a re-quarantine."""
        member = self._create_member(
            created_days_ago=30,
            joined_days_ago=2,
            roles=[self.default_role, self.q_role],
        )
        self.guild.get_member.return_value = member

        # User is already recorded in quarantine data
        await self.cog.db.set_quarantine_record(
            member.id,
            roles=[self.user_role1.id],
            reason="Previous quarantine",
            q_type="automod",
        )

        execution = MagicMock(spec=discord.AutoModAction)
        execution.guild_id = self.guild.id
        execution.guild = self.guild
        execution.user_id = member.id
        execution.member = member
        execution.channel_id = self.quarantine_channel.id

        await self.cog.on_automod_action(execution)

        # Should NOT edit roles or send another message to quarantine channel
        member.edit.assert_not_awaited()
        self.quarantine_channel.send.assert_not_awaited()

    async def test_automod_in_quarantine_channel_ignored(self):
        """AutoMod violations occurring inside the quarantine channel itself are ignored."""
        member = self._create_member(
            created_days_ago=30,
            joined_days_ago=1,
            roles=[self.default_role, self.q_role],
        )
        self.guild.get_member.return_value = member

        execution = MagicMock(spec=discord.AutoModAction)
        execution.guild_id = self.guild.id
        execution.guild = self.guild
        execution.user_id = member.id
        execution.member = member
        execution.channel_id = self.quarantine_channel.id

        await self.cog.on_automod_action(execution)

        member.edit.assert_not_awaited()
        self.quarantine_channel.send.assert_not_awaited()

    async def test_release_quarantined_member_restores_roles(self):
        """Releasing an AutoMod quarantined user restores their saved roles, clears quarantine data, and sends DM."""
        member = self._create_member(
            roles=[self.default_role, self.category_role, self.q_role]
        )
        staff = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])

        # Save prior roles in quarantine ledger with type automod
        await self.cog.db.set_quarantine_record(
            member.id,
            roles=[self.user_role1.id, self.user_role2.id],
            reason="Test",
            q_type="automod",
        )

        success, msg = await self.cog.release_quarantined_member(member, released_by=staff)
        self.assertTrue(success)

        # Check roles restored
        member.edit.assert_awaited_once()
        restored = member.edit.call_args.kwargs["roles"]
        self.assertIn(self.user_role1, restored)
        self.assertIn(self.user_role2, restored)
        self.assertNotIn(self.q_role, restored)

        # DM sent with restoration message
        member.send.assert_awaited_once()
        dm_content = member.send.call_args.args[0]
        self.assertIn("previous roles and server access have been restored", dm_content)
        self.assertIn("StaffMod", dm_content)

        # Removed from database
        self.assertFalse(await self.cog.db.has_quarantine_record(member.id))

        # Announced in quarantine channel
        self.quarantine_channel.send.assert_awaited()
        announcement = self.quarantine_channel.send.call_args.args[0]
        self.assertIn(member.mention, announcement)
        self.assertIn("released into the wild", announcement)
        self.assertNotIn(staff.mention, announcement)
        self.assertNotIn(staff.name, announcement)

        # Quarantine log sent without tagging releaser
        self.quarantine_logs_channel.send.assert_awaited()
        log_content = self.quarantine_logs_channel.send.call_args.args[0]
        self.assertIn(f"by **{staff.display_name}**", log_content)
        self.assertNotIn(staff.mention, log_content)

    async def test_release_quarantined_account_age_does_not_restore_partial_roles(self):
        """Releasing an account_age quarantined user does NOT restore partial onboarding roles and directs to Channels & Roles via DM."""
        member = self._create_member(
            roles=[self.default_role, self.category_role, self.q_role]
        )
        staff = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])

        # Save partial onboarding roles clicked on join
        await self.cog.db.set_quarantine_record(
            member.id,
            roles=[self.user_role1.id, self.user_role2.id],
            reason="Account too new",
            q_type="account_age",
        )

        success, msg = await self.cog.release_quarantined_member(member, released_by=staff)
        self.assertTrue(success)

        # Check partial roles were NOT restored
        member.edit.assert_awaited_once()
        restored = member.edit.call_args.kwargs["roles"]
        self.assertNotIn(self.user_role1, restored)
        self.assertNotIn(self.user_role2, restored)
        self.assertNotIn(self.q_role, restored)
        self.assertIn(self.category_role, restored)

        # DM sent with Channels & Roles onboarding instructions
        member.send.assert_awaited_once()
        dm_content = member.send.call_args.args[0]
        self.assertIn("Channels & Roles", dm_content)
        self.assertIn("Welcome to the server", dm_content)

    async def test_release_quarantine_dm_failure_handled_gracefully(self):
        """If user has DMs closed, release still succeeds without crashing."""
        member = self._create_member(
            roles=[self.default_role, self.category_role, self.q_role]
        )
        member.send.side_effect = discord.Forbidden(MagicMock(), "Cannot send messages to this user")
        staff = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])

        await self.cog.db.set_quarantine_record(
            member.id,
            roles=[self.user_role1.id],
            reason="Test",
            q_type="automod",
        )

        success, msg = await self.cog.release_quarantined_member(member, released_by=staff)
        self.assertTrue(success)
        member.edit.assert_awaited_once()

    async def test_release_command_handles_quarantine(self):
        """!release command works for quarantined members."""
        member = self._create_member(roles=[self.default_role, self.q_role])
        ctx = MagicMock()
        ctx.author = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])
        ctx.channel = self.quarantine_channel
        ctx.send = AsyncMock()
        ctx.guild = self.guild

        await self.cog.release.callback(self.cog, ctx, member)

        member.edit.assert_awaited_once()
        restored = member.edit.call_args.kwargs["roles"]
        self.assertNotIn(self.q_role, restored)

    async def test_release_button_click_by_staff(self):
        """Clicking the dynamic release button releases member if staff."""
        member = self._create_member(roles=[self.default_role, self.q_role])
        self.guild.get_member.return_value = member

        button = QuarantineReleaseButton(member.id)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.client = self.bot
        self.bot.get_cog.return_value = self.cog
        interaction.guild = self.guild
        interaction.user = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])
        interaction.user.guild_permissions.manage_roles = True
        interaction.response = MagicMock()
        interaction.response.defer = AsyncMock()
        interaction.response.send_message = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()
        interaction.channel = MagicMock(spec=discord.TextChannel)
        interaction.channel.id = 999
        interaction.channel.send = AsyncMock()
        interaction.message = MagicMock()
        interaction.message.edit = AsyncMock()
        interaction.message.reply = AsyncMock()
        interaction.message.channel = interaction.channel

        await button.callback(interaction)

        interaction.response.defer.assert_awaited_once_with(ephemeral=True)
        member.edit.assert_awaited_once()
        interaction.message.reply.assert_awaited_once()
        reply_content = interaction.message.reply.call_args.args[0]
        self.assertIn(member.mention, reply_content)
        self.assertNotIn(interaction.user.mention, reply_content)
        self.assertNotIn(interaction.user.name, reply_content)
        interaction.followup.send.assert_awaited_once_with(f"✅ Released {member.name} into the wild.", ephemeral=True)
        self.assertTrue(button.item.disabled)

    async def test_manual_quarantine_command(self):
        """Staff manually quarantining a user saves roles, notifies user once without reason field, and logs with untagged staff name."""
        member = self._create_member(
            user_id=12345,
            name="TargetUser",
            created_days_ago=300,
            roles=[self.default_role, self.category_role, self.user_role1],
        )
        staff = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])
        ctx = MagicMock()
        ctx.author = staff
        ctx.guild = self.guild
        ctx.channel = self.quarantine_channel
        ctx.send = AsyncMock()

        await self.cog.manual_quarantine.callback(self.cog, ctx, member, reason="Testing manual quarantine")

        # Roles stripped and saved
        member.edit.assert_awaited_once()
        q_record = await self.cog.db.get_quarantine_record(member.id)
        self.assertIsNotNone(q_record)
        self.assertEqual(q_record["type"], "manual")

        # Exactly 1 message in quarantine channel: mentions user, no reason field, no duplicate ctx.send
        self.quarantine_channel.send.assert_awaited_once()
        q_msg = self.quarantine_channel.send.call_args.args[0]
        self.assertIn(member.mention, q_msg)
        self.assertIn("you have been placed in quarantine.", q_msg)
        self.assertNotIn("Reason:", q_msg)
        self.assertNotIn("too new", q_msg)
        ctx.send.assert_not_awaited()

        # Log in quarantine logs channel states manual quarantine with bold name, no staff tag, no reason field
        self.quarantine_logs_channel.send.assert_awaited_once()
        log_msg = self.quarantine_logs_channel.send.call_args.args[0]
        self.assertIn("Manual Quarantine:", log_msg)
        self.assertIn(f"by **{staff.display_name}**", log_msg)
        self.assertNotIn(staff.mention, log_msg)
        self.assertNotIn("Reason:", log_msg)
        self.assertNotIn("Auto-Quarantine:", log_msg)

    async def test_manual_quarantine_from_other_channel_sends_single_confirmation(self):
        """Staff quarantining from another channel (e.g. mods chat) sends card to quarantine and untagged confirmation to mods chat."""
        member = self._create_member(
            user_id=12345,
            name="TargetUser",
            created_days_ago=300,
            roles=[self.default_role, self.category_role, self.user_role1],
        )
        staff = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])
        other_channel = MagicMock(spec=discord.TextChannel, id=888, name="mods-chat")
        other_channel.send = AsyncMock()

        ctx = MagicMock()
        ctx.author = staff
        ctx.guild = self.guild
        ctx.channel = other_channel
        ctx.send = other_channel.send

        await self.cog.manual_quarantine.callback(self.cog, ctx, member, reason="Testing")

        # 1 card in quarantine channel
        self.quarantine_channel.send.assert_awaited_once()
        # 1 untagged confirmation in other channel
        other_channel.send.assert_awaited_once_with(f"🔒 **{member.display_name}** has been dragged to quarantine.")

    async def test_release_button_click_by_non_staff_denied(self):
        """Non-staff clicking the release button gets rejected."""
        member = self._create_member(roles=[self.default_role, self.q_role])
        self.guild.get_member.return_value = member

        button = QuarantineReleaseButton(member.id)
        interaction = MagicMock(spec=discord.Interaction)
        interaction.client = self.bot
        self.bot.get_cog.return_value = self.cog
        interaction.guild = self.guild
        non_staff = self._create_member(user_id=444, name="RegularUser", roles=[self.default_role])
        non_staff.guild_permissions.manage_roles = False
        non_staff.guild_permissions.administrator = False
        interaction.user = non_staff
        interaction.response = MagicMock()
        interaction.response.send_message = AsyncMock()

        await button.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        err_msg = interaction.response.send_message.call_args.args[0]
        self.assertIn("don't have permission", err_msg)
        member.edit.assert_not_awaited()


    async def test_manual_quarantine_role_removal_in_ui(self):
        """When staff manually removes Quarantine role in Discord UI, saved roles are restored and DM is sent."""
        member_before = self._create_member(roles=[self.default_role, self.category_role, self.q_role])
        member_after = self._create_member(roles=[self.default_role, self.category_role])
        member_after.add_roles = AsyncMock()

        # Save prior roles with type automod
        await self.cog.db.set_quarantine_record(
            member_after.id,
            roles=[self.user_role1.id],
            reason="Test UI removal",
            q_type="automod",
        )

        await self.cog.on_member_update(member_before, member_after)

        member_after.add_roles.assert_awaited_once_with(self.user_role1, reason="Quarantine role manually removed: Restored prior roles.")
        member_after.send.assert_awaited_once()
        self.assertFalse(await self.cog.db.has_quarantine_record(member_after.id))

    async def test_quarantine_evasion_on_rejoin(self):
        """User in quarantine who leaves and rejoins gets dragged back to quarantine."""
        member = self._create_member(created_days_ago=50, joined_days_ago=0)

        await self.cog.db.set_quarantine_record(
            member.id,
            roles=[self.user_role1.id],
            reason="Prior quarantine",
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            self.guild.get_member.return_value = member
            await self.cog.on_member_join(member)

        # Should be re-quarantined
        member.edit.assert_awaited_once()
        edited_roles = member.edit.call_args.kwargs["roles"]
        self.assertIn(self.q_role, edited_roles)
        # Check evasion message sent
        sent_content = self.quarantine_channel.send.call_args.args[0]
        self.assertIn("Quarantine Evasion Attempted", sent_content)

    async def test_quarantine_evasion_on_rejoin_with_account_still_too_new(self):
        """A quarantined new account that rejoins is dragged back instead of being skipped as 'already quarantined'."""
        member = self._create_member(created_days_ago=2, joined_days_ago=0)
        await self.cog.db.set_quarantine_record(
            member.id, roles=[], reason="Account too new", q_type="account_age"
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            self.guild.get_member.return_value = member
            await self.cog.on_member_join(member)

        member.edit.assert_awaited_once()
        self.assertIn(self.q_role, member.edit.call_args.kwargs["roles"])
        self.assertIn("Quarantine Evasion Attempted", self.quarantine_channel.send.call_args.args[0])
        self.assertTrue(await self.cog.db.has_quarantine_record(member.id))

    async def test_quarantine_evasion_on_rejoin_keeps_evasion_wording_for_automod_record(self):
        """Rejoining while quarantined by AutoMod is announced as evasion and keeps the saved roles."""
        member = self._create_member(created_days_ago=50, joined_days_ago=0)
        await self.cog.db.set_quarantine_record(
            member.id, roles=[self.user_role1.id], reason="AutoMod", q_type="automod"
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            self.guild.get_member.return_value = member
            await self.cog.on_member_join(member)

        user_msg = self.quarantine_channel.send.call_args.args[0]
        self.assertIn("Quarantine Evasion Attempted", user_msg)
        self.assertNotIn("flagged by AutoMod", user_msg)
        self.assertIn("Quarantine Evasion Detected", self.quarantine_logs_channel.send.call_args.args[0])
        record = await self.cog.db.get_quarantine_record(member.id)
        self.assertEqual((record["type"], record["roles"]), ("automod", [self.user_role1.id]))

    def _forbidden(self):
        return discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "Missing Permissions")

    async def test_failed_quarantine_leaves_no_record_behind(self):
        """If Discord rejects the role change, the member is not recorded as quarantined and can be retried."""
        member = self._create_member(roles=[self.default_role, self.user_role1])
        member.edit.side_effect = self._forbidden()

        self.assertFalse(await self.cog.quarantine_member(member, reason="x", quarantine_type="manual"))
        self.assertFalse(await self.cog.db.has_quarantine_record(member.id))
        self.quarantine_channel.send.assert_not_awaited()

        member.edit.side_effect = None
        self.assertTrue(await self.cog.quarantine_member(member, reason="x", quarantine_type="manual"))
        record = await self.cog.db.get_quarantine_record(member.id)
        self.assertEqual(record["roles"], [self.user_role1.id])

    async def test_failed_quarantine_of_inmate_restores_prison_record(self):
        member = self._create_member(roles=[self.default_role])
        await self.cog.db.set_prison_record(member.id, [self.user_role1.id, self.user_role2.id])
        member.edit.side_effect = self._forbidden()

        self.assertFalse(await self.cog.quarantine_member(member, reason="x", quarantine_type="manual"))

        self.assertFalse(await self.cog.db.has_quarantine_record(member.id))
        self.assertEqual(
            await self.cog.db.get_prison_record(member.id), [self.user_role1.id, self.user_role2.id]
        )

    async def test_failed_evasion_requarantine_keeps_existing_record(self):
        member = self._create_member(created_days_ago=50, joined_days_ago=0)
        await self.cog.db.set_quarantine_record(
            member.id, roles=[self.user_role1.id], reason="AutoMod", q_type="automod"
        )
        member.edit.side_effect = self._forbidden()

        self.assertFalse(await self.cog.quarantine_member(member, reason="x", is_evasion=True))

        record = await self.cog.db.get_quarantine_record(member.id)
        self.assertEqual((record["type"], record["roles"]), ("automod", [self.user_role1.id]))

    async def test_failed_quarantine_release_keeps_saved_roles(self):
        """A release Discord rejects must not throw away the roles saved for restoration."""
        member = self._create_member(roles=[self.default_role, self.q_role])
        staff = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])
        await self.cog.db.set_quarantine_record(
            member.id,
            roles=[self.user_role1.id],
            reason="Spam",
            q_type="manual",
            quarantined_at="2026-01-02T03:04:05+00:00",
        )
        member.edit.side_effect = self._forbidden()

        success, _ = await self.cog.release_quarantined_member(member, released_by=staff)

        self.assertFalse(success)
        member.send.assert_not_awaited()
        self.assertEqual(
            await self.cog.db.get_quarantine_record(member.id),
            {
                "roles": [self.user_role1.id],
                "quarantined_at": "2026-01-02T03:04:05+00:00",
                "reason": "Spam",
                "type": "manual",
            },
        )

    async def test_failed_prison_release_keeps_saved_roles(self):
        inmate_role, _, _ = self._setup_prison_quarantine_roles()
        member = self._create_member(roles=[self.default_role, inmate_role])
        await self.cog.db.set_prison_record(member.id, [self.user_role1.id])
        member.edit.side_effect = self._forbidden()
        ctx = self._make_staff_ctx(self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role]))

        with patch.dict(os.environ, {"ENABLE_MODERATION": "True", "ENABLE_CMD_PRISON": "True"}):
            await self.cog.release.callback(self.cog, ctx, member)

        self.assertEqual(await self.cog.db.get_prison_record(member.id), [self.user_role1.id])
        self.assertIn("Could not release", ctx.send.call_args.args[0])

    async def test_failed_imprisonment_rolls_back_records(self):
        """If Discord rejects the role change, no prison record remains and a prior quarantine record is kept."""
        self._setup_prison_quarantine_roles()
        member = self._create_member(roles=[self.default_role, self.q_role])
        await self.cog.db.set_quarantine_record(
            member.id, roles=[self.user_role1.id], reason="Spam", q_type="manual"
        )
        member.edit.side_effect = self._forbidden()
        ctx = self._make_staff_ctx(self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role]))

        with patch.dict(os.environ, {"ENABLE_MODERATION": "True", "ENABLE_CMD_PRISON": "True"}):
            await self.cog.prison.callback(self.cog, ctx, member)

        self.assertFalse(await self.cog.db.has_prison_record(member.id))
        record = await self.cog.db.get_quarantine_record(member.id)
        self.assertEqual((record["type"], record["roles"]), ("manual", [self.user_role1.id]))
        self.assertIn("Could not imprison", ctx.send.call_args.args[0])

    async def test_prison_release_restores_saved_roles(self):
        inmate_role, _, _ = self._setup_prison_quarantine_roles()
        member = self._create_member(roles=[self.default_role, self.category_role, inmate_role])
        await self.cog.db.set_prison_record(member.id, [self.user_role1.id, self.user_role2.id])
        ctx = self._make_staff_ctx(self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role]))

        with patch.dict(os.environ, {"ENABLE_MODERATION": "True", "ENABLE_CMD_PRISON": "True"}):
            await self.cog.release.callback(self.cog, ctx, member)

        self.assertEqual(
            {r.id for r in member.edit.call_args.kwargs["roles"]},
            {self.default_role.id, self.category_role.id, self.user_role1.id, self.user_role2.id},
        )
        self.assertFalse(await self.cog.db.has_prison_record(member.id))

    async def test_release_of_member_who_is_not_restricted_changes_nothing(self):
        member = self._create_member(roles=[self.default_role, self.user_role1])
        ctx = self._make_staff_ctx(self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role]))

        with patch.dict(os.environ, {"ENABLE_MODERATION": "True", "ENABLE_CMD_PRISON": "True"}):
            await self.cog.release.callback(self.cog, ctx, member)

        member.edit.assert_not_awaited()
        self.assertIn("neither in prison nor in quarantine", ctx.send.call_args.args[0])

    async def test_staff_cannot_be_quarantined_or_imprisoned(self):
        member = self._create_member(roles=[self.default_role, self.staff_role])
        ctx = self._make_staff_ctx(self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role]))

        with patch.dict(os.environ, {"ENABLE_MODERATION": "True", "ENABLE_CMD_PRISON": "True"}):
            await self.cog.prison.callback(self.cog, ctx, member)
            await self.cog.manual_quarantine.callback(self.cog, ctx, member, reason="x")

        member.edit.assert_not_awaited()
        self.assertFalse(await self.cog.db.has_prison_record(member.id))
        self.assertFalse(await self.cog.db.has_quarantine_record(member.id))

    # ---- managed roles (Server Booster, integrations) ----------------------

    async def test_quarantine_keeps_managed_roles_and_does_not_save_them(self):
        member = self._create_member(
            created_days_ago=300,
            roles=[self.default_role, self.category_role, self.booster_role, self.user_role1],
        )

        self.assertTrue(await self.cog.quarantine_member(member, reason="x", quarantine_type="manual"))

        self.assertEqual(
            {r.id for r in member.edit.call_args.kwargs["roles"]},
            {self.category_role.id, self.booster_role.id, self.q_role.id},
        )
        self.assertEqual((await self.cog.db.get_quarantine_record(member.id))["roles"], [self.user_role1.id])

    async def test_prison_keeps_managed_roles_and_does_not_save_them(self):
        inmate_role, _, _ = self._setup_prison_quarantine_roles()
        member = self._create_member(
            roles=[self.default_role, self.category_role, self.booster_role, self.user_role1]
        )
        ctx = self._make_staff_ctx(self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role]))

        with patch.dict(os.environ, {"ENABLE_MODERATION": "True", "ENABLE_CMD_PRISON": "True"}):
            await self.cog.prison.callback(self.cog, ctx, member)

        self.assertEqual(
            {r.id for r in member.edit.call_args.kwargs["roles"]},
            {self.category_role.id, self.booster_role.id, inmate_role.id},
        )
        self.assertEqual(await self.cog.db.get_prison_record(member.id), [self.user_role1.id])

    async def test_releases_keep_managed_roles(self):
        inmate_role, _, _ = self._setup_prison_quarantine_roles()
        staff = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])

        quarantined = self._create_member(user_id=1, roles=[self.default_role, self.booster_role, self.q_role])
        await self.cog.db.set_quarantine_record(1, roles=[self.user_role1.id], reason="x", q_type="manual")
        success, _ = await self.cog.release_quarantined_member(quarantined, released_by=staff)
        self.assertTrue(success)
        self.assertEqual(
            {r.id for r in quarantined.edit.call_args.kwargs["roles"]},
            {self.default_role.id, self.booster_role.id, self.user_role1.id},
        )

        inmate = self._create_member(user_id=2, roles=[self.default_role, self.booster_role, inmate_role])
        await self.cog.db.set_prison_record(2, [self.user_role2.id])
        with patch.dict(os.environ, {"ENABLE_MODERATION": "True", "ENABLE_CMD_PRISON": "True"}):
            await self.cog.release.callback(self.cog, self._make_staff_ctx(staff), inmate)
        self.assertEqual(
            {r.id for r in inmate.edit.call_args.kwargs["roles"]},
            {self.default_role.id, self.booster_role.id, self.user_role2.id},
        )

    async def test_enforcement_tolerates_managed_roles_but_still_strips_others(self):
        inmate_role, _, _ = self._setup_prison_quarantine_roles()
        for restriction in (self.q_role, inmate_role):
            with self.subTest(restriction=restriction.id):
                allowed = [self.default_role, self.category_role, self.booster_role, restriction]
                before = self._create_member(roles=[self.default_role, restriction])
                settled = self._create_member(roles=allowed)
                await self.cog.on_member_update(before, settled)
                settled.edit.assert_not_awaited()

                sneaky = self._create_member(roles=[*allowed, self.user_role1])
                await self.cog.on_member_update(settled, sneaky)
                sneaky.edit.assert_awaited_once()
                self.assertEqual(
                    {r.id for r in sneaky.edit.call_args.kwargs["roles"]},
                    {self.category_role.id, self.booster_role.id, restriction.id},
                )

    async def test_fleeing_inmate_is_locked_down_again_on_rejoin(self):
        inmate_role, _, _ = self._setup_prison_quarantine_roles()
        member = self._create_member(
            created_days_ago=300, joined_days_ago=0, roles=[self.default_role, self.booster_role, self.user_role1]
        )
        await self.cog.db.set_prison_record(member.id, [self.user_role2.id])

        with patch("asyncio.sleep", new_callable=AsyncMock):
            self.guild.get_member.return_value = member
            await self.cog.on_member_join(member)

        self.assertEqual(
            {r.id for r in member.edit.call_args.kwargs["roles"]}, {self.booster_role.id, inmate_role.id}
        )
        self.assertEqual(await self.cog.db.get_prison_record(member.id), [self.user_role2.id])

    async def test_quarantine_timestamp_is_stored_in_utc(self):
        member = self._create_member(created_days_ago=300, roles=[self.default_role])
        before = datetime.now(timezone.utc)
        await self.cog.quarantine_member(member, reason="x", quarantine_type="manual")
        after = datetime.now(timezone.utc)

        stored = datetime.fromisoformat((await self.cog.db.get_quarantine_record(member.id))["quarantined_at"])
        self.assertEqual(stored.utcoffset(), timedelta(0))
        self.assertTrue(before <= stored <= after)

    async def test_on_ready_audit_runs_successfully(self):
        """on_ready performs comprehensive permission, channel, and role audits without crashing."""
        self.bot.guilds = [self.guild]
        # Ensure guild.me attributes are present
        bot_member = MagicMock()
        bot_member.__str__.return_value = "Gate Greeter#0001"
        bot_member.top_role = MagicMock(name="BotRole", position=10)
        bot_member.top_role.name = "BotRole"
        bot_member.guild_permissions = MagicMock(
            administrator=True,
            manage_guild=True,
            manage_roles=True,
            view_audit_log=True,
            kick_members=True
        )
        self.guild.me = bot_member

        # Run on_ready
        await self.cog.on_ready()

    async def test_member_greeting_on_join(self):
        """Standard new members joining are greeted in newcomers channel with guild name and clickable rules channel."""
        newcomers_channel = MagicMock(spec=discord.TextChannel, id=444, name="newcomers")
        newcomers_channel.send = AsyncMock()
        self.cog.newcomers_channel_id = 444
        self.cog.rules_channel_id = 796842030332248105

        orig_get_channel = self.guild.get_channel.side_effect
        self.guild.get_channel.side_effect = lambda cid: newcomers_channel if cid == 444 else orig_get_channel(cid)
        self.guild.name = "CosmicSkeptic's community"

        member = self._create_member(user_id=12345, created_days_ago=15, joined_days_ago=0)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            self.guild.get_member.return_value = member
            await self.cog.on_member_join(member)

        newcomers_channel.send.assert_awaited_once()
        sent_content = newcomers_channel.send.call_args.args[0]
        self.assertIn("Hey <@12345>", sent_content)
        self.assertIn("welcome to **CosmicSkeptic's community**!", sent_content)
        self.assertIn("please follow the instructions from <#796842030332248105>.", sent_content)

    async def test_member_greeting_disabled_by_env(self):
        """When ENABLE_GREETING is False, standard new members are not greeted on join."""
        newcomers_channel = MagicMock(spec=discord.TextChannel, id=444, name="newcomers")
        newcomers_channel.send = AsyncMock()
        self.cog.newcomers_channel_id = 444

        orig_get_channel = self.guild.get_channel.side_effect
        self.guild.get_channel.side_effect = lambda cid: newcomers_channel if cid == 444 else orig_get_channel(cid)

        member = self._create_member(user_id=12345, created_days_ago=15, joined_days_ago=0)

        with patch("asyncio.sleep", new_callable=AsyncMock), patch("cogs.moderation.is_env_enabled", return_value=False):
            self.guild.get_member.return_value = member
            await self.cog.on_member_join(member)

        newcomers_channel.send.assert_not_called()

    async def test_member_greeting_not_sent_for_quarantined_user(self):
        """Auto-quarantined users receive quarantine notice, not the public newcomers greeting."""
        newcomers_channel = MagicMock(spec=discord.TextChannel, id=444, name="newcomers")
        newcomers_channel.send = AsyncMock()
        self.cog.newcomers_channel_id = 444

        orig_get_channel = self.guild.get_channel.side_effect
        self.guild.get_channel.side_effect = lambda cid: newcomers_channel if cid == 444 else orig_get_channel(cid)

        # Account age <= 7 days triggers auto-quarantine
        member = self._create_member(user_id=12345, created_days_ago=3, joined_days_ago=0)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            self.guild.get_member.return_value = member
            await self.cog.on_member_join(member)

        # Quarantined, so newcomers channel greeting was not sent
        newcomers_channel.send.assert_not_called()
        # Quarantine channel notice was sent
        self.quarantine_channel.send.assert_awaited_once()

    async def test_member_greeting_skipped_when_newcomers_channel_not_found(self):
        """When newcomers channel is not found in the guild, a warning is logged and greeting is aborted."""
        self.cog.newcomers_channel_id = 999999
        self.cog.rules_channel_id = 796842030332248105
        self.guild.name = "CosmicSkeptic's community"

        member = self._create_member(user_id=12345, created_days_ago=15, joined_days_ago=0)

        with patch("asyncio.sleep", new_callable=AsyncMock), patch("builtins.print") as mock_print:
            self.guild.get_member.return_value = member
            await self.cog.on_member_join(member)

        mock_print.assert_any_call("⚠️ Newcomers channel 999999 not found in guild CosmicSkeptic's community. Welcome greeting not sent.")

    def _setup_prison_quarantine_roles(self):
        """Configures inmate, trusted, and 5 user roles on the mock guild for transition tests."""
        self.q_role.managed = False
        inmate_role = MagicMock(spec=discord.Role, id=888, name="Inmate", managed=False)
        trusted_role = MagicMock(spec=discord.Role, id=605, name="Trusted Media", managed=False)
        extra_roles = [
            MagicMock(spec=discord.Role, id=603, name="Role3", managed=False),
            MagicMock(spec=discord.Role, id=604, name="Role4", managed=False),
            trusted_role,
        ]
        for r in [inmate_role, *extra_roles]:
            r.__lt__ = lambda self, other: True

        self.cog.trusted_role_id = trusted_role.id
        five_roles = [self.user_role1, self.user_role2, *extra_roles]
        all_roles = {
            1000: self.default_role,
            999: self.q_role,
            888: inmate_role,
            500: self.category_role,
            700: self.staff_role,
            650: self.booster_role,
            **{r.id: r for r in five_roles},
        }
        self.guild.get_role.side_effect = all_roles.get
        return inmate_role, trusted_role, five_roles

    async def _attach_live_role_edit(self, member):
        """Simulates Discord updating member.roles and dispatching on_member_update on member.edit."""
        async def live_edit(*, roles, reason=None):
            before = self._create_member(
                user_id=member.id,
                name=member.name,
                roles=list(member.roles),
            )
            new_roles = [self.default_role]
            for r in roles:
                if r not in new_roles:
                    new_roles.append(r)
            member.roles = new_roles
            after = self._create_member(
                user_id=member.id,
                name=member.name,
                roles=list(member.roles),
            )
            await self.cog.on_member_update(before, after)

        member.edit = AsyncMock(side_effect=live_edit)

    def _make_staff_ctx(self, staff):
        ctx = MagicMock()
        ctx.author = staff
        ctx.guild = self.guild
        ctx.channel = self.quarantine_channel
        ctx.send = AsyncMock()
        return ctx

    async def test_prison_quarantine_cycle_ending_in_quarantine_restores_all_roles(self):
        """prison -> quarantine -> prison -> quarantine -> prison -> quarantine -> release restores all 5 roles cleanly."""
        inmate_role, _, five_roles = self._setup_prison_quarantine_roles()
        member = self._create_member(
            user_id=12345,
            name="CycleUser",
            created_days_ago=300,
            roles=[self.default_role, self.category_role, *five_roles],
        )
        await self._attach_live_role_edit(member)
        staff = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])
        ctx = self._make_staff_ctx(staff)

        with patch.dict(os.environ, {"ENABLE_MODERATION": "True", "ENABLE_CMD_PRISON": "True"}):
            # 1. prison -> 2. quarantine -> 3. prison -> 4. quarantine -> 5. prison -> 6. quarantine
            await self.cog.prison.callback(self.cog, ctx, member)
            self.assertIn(inmate_role, member.roles)

            await self.cog.manual_quarantine.callback(self.cog, ctx, member, reason="Q1")
            self.assertIn(self.q_role, member.roles)
            self.assertIsNone(await self.cog.db.get_prison_record(member.id))

            await self.cog.prison.callback(self.cog, ctx, member)
            self.assertIn(inmate_role, member.roles)
            self.assertIsNone(await self.cog.db.get_quarantine_record(member.id))

            await self.cog.manual_quarantine.callback(self.cog, ctx, member, reason="Q2")
            self.assertIn(self.q_role, member.roles)
            self.assertIsNone(await self.cog.db.get_prison_record(member.id))

            await self.cog.prison.callback(self.cog, ctx, member)
            self.assertIn(inmate_role, member.roles)
            self.assertIsNone(await self.cog.db.get_quarantine_record(member.id))

            await self.cog.manual_quarantine.callback(self.cog, ctx, member, reason="Q3")
            self.assertIn(self.q_role, member.roles)

            # Intermediate Quarantine -> Prison transitions must NOT send bogus quarantine release DMs/logs
            member.send.assert_not_awaited()
            for call in self.quarantine_logs_channel.send.call_args_list:
                self.assertNotIn("Quarantine Release", call.args[0])

            # 7. Release from quarantine into the wild
            await self.cog.release.callback(self.cog, ctx, member)

        self.assertEqual(
            {r.id for r in member.roles},
            {self.default_role.id, self.category_role.id, *(r.id for r in five_roles)},
        )
        self.assertFalse(await self.cog.db.has_prison_record(member.id))
        self.assertFalse(await self.cog.db.has_quarantine_record(member.id))

    async def test_prison_quarantine_cycle_ending_in_prison_restores_all_roles(self):
        """prison -> quarantine -> prison -> release restores all 5 roles directly without bouncing back to quarantine."""
        inmate_role, _, five_roles = self._setup_prison_quarantine_roles()
        member = self._create_member(
            user_id=12345,
            name="CycleUser",
            created_days_ago=300,
            roles=[self.default_role, self.category_role, *five_roles],
        )
        await self._attach_live_role_edit(member)
        staff = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])
        ctx = self._make_staff_ctx(staff)

        with patch.dict(os.environ, {"ENABLE_MODERATION": "True", "ENABLE_CMD_PRISON": "True"}):
            await self.cog.prison.callback(self.cog, ctx, member)
            await self.cog.manual_quarantine.callback(self.cog, ctx, member, reason="Q1")
            await self.cog.prison.callback(self.cog, ctx, member)
            self.assertIn(inmate_role, member.roles)

            await self.cog.release.callback(self.cog, ctx, member)

        self.assertNotIn(self.q_role, member.roles)
        self.assertNotIn(inmate_role, member.roles)
        self.assertEqual(
            {r.id for r in member.roles},
            {self.default_role.id, self.category_role.id, *(r.id for r in five_roles)},
        )
        self.assertFalse(await self.cog.db.has_prison_record(member.id))
        self.assertFalse(await self.cog.db.has_quarantine_record(member.id))

    async def test_prison_from_empty_quarantine_and_quarantine_from_empty_prison_preserve_empty_list(self):
        """Zero prior roles stays [] across prison <-> quarantine without saving restriction roles."""
        inmate_role, _, _ = self._setup_prison_quarantine_roles()
        member = self._create_member(
            user_id=22222,
            name="NoRolesUser",
            created_days_ago=300,
            roles=[self.default_role, self.category_role],
        )
        await self._attach_live_role_edit(member)
        staff = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])
        ctx = self._make_staff_ctx(staff)

        with patch.dict(os.environ, {"ENABLE_MODERATION": "True", "ENABLE_CMD_PRISON": "True"}):
            await self.cog.prison.callback(self.cog, ctx, member)
            await self.cog.manual_quarantine.callback(self.cog, ctx, member, reason="Q1")
            q_rec = await self.cog.db.get_quarantine_record(member.id)
            self.assertEqual(q_rec["roles"], [])

            await self.cog.prison.callback(self.cog, ctx, member)
            p_rec = await self.cog.db.get_prison_record(member.id)
            self.assertEqual(p_rec, [])

            await self.cog.release.callback(self.cog, ctx, member)

        self.assertEqual({r.id for r in member.roles}, {self.default_role.id, self.category_role.id})
        self.assertNotIn(inmate_role, member.roles)
        self.assertNotIn(self.q_role, member.roles)

    async def test_account_age_quarantine_to_prison_does_not_preserve_onboarding_roles(self):
        """Moving an account_age quarantined user to prison does not leak incomplete onboarding roles on release."""
        self._setup_prison_quarantine_roles()
        member = self._create_member(
            user_id=33333,
            name="FreshAcct",
            created_days_ago=1,
            roles=[self.default_role, self.q_role],
        )
        await self._attach_live_role_edit(member)
        await self.cog.db.set_quarantine_record(
            member.id,
            roles=[self.user_role1.id],
            reason="Account too new",
            q_type="account_age",
        )
        staff = self._create_member(user_id=777, name="StaffMod", roles=[self.staff_role])
        ctx = self._make_staff_ctx(staff)

        with patch.dict(os.environ, {"ENABLE_MODERATION": "True", "ENABLE_CMD_PRISON": "True"}):
            await self.cog.prison.callback(self.cog, ctx, member)
            self.assertEqual(await self.cog.db.get_prison_record(member.id), [])
            await self.cog.release.callback(self.cog, ctx, member)

        self.assertEqual({r.id for r in member.roles}, {self.default_role.id})

    async def test_manual_trusted_role_grant_frees_trust_db_row(self):
        """Manually granting TRUSTED_ROLE_ID via Discord UI deletes any existing trust_records row."""
        _, trusted_role, _ = self._setup_prison_quarantine_roles()
        await self.cog.db.set_trust_points(12345, 17.0)
        self.cog.trust_cooldowns[12345] = datetime.now(timezone.utc)

        before = self._create_member(user_id=12345, roles=[self.default_role, self.user_role1])
        after = self._create_member(user_id=12345, roles=[self.default_role, self.user_role1, trusted_role])

        await self.cog.on_member_update(before, after)

        self.assertEqual(await self.cog.db.get_trust_points(12345), 0.0)
        cur = self.cog.db._get_connection().execute("SELECT 1 FROM trust_records WHERE user_id = ?;", (12345,))
        self.assertIsNone(cur.fetchone())
        self.assertNotIn(12345, self.cog.trust_cooldowns)

    async def test_trusted_role_added_while_quarantined_does_not_delete_trust_row(self):
        """If TRUSTED_ROLE_ID is added to a quarantined user, enforcement strips it and preserves trust_records."""
        _, trusted_role, _ = self._setup_prison_quarantine_roles()
        await self.cog.db.set_trust_points(12345, 14.0)

        before = self._create_member(user_id=12345, roles=[self.default_role, self.q_role])
        after = self._create_member(user_id=12345, roles=[self.default_role, self.q_role, trusted_role])

        await self.cog.on_member_update(before, after)

        after.edit.assert_awaited_once()
        cur = self.cog.db._get_connection().execute("SELECT points FROM trust_records WHERE user_id = ?;", (12345,))
        row = cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["points"], 14.0)


def utc_clock(at):
    """Stand-in for datetime.now that only answers when asked for UTC."""
    def now(tz=None):
        if tz is not timezone.utc:
            raise AssertionError("moderation code must read the clock as datetime.now(timezone.utc)")
        return at
    return now


class TestUncVoiceTracking(unittest.IsolatedAsyncioTestCase):
    ROLE_IDS = [11, 12, 13, 14, 15]

    async def asyncSetUp(self):
        random.seed(20260101)
        self.test_dir = tempfile.mkdtemp()
        self.db_file = os.path.join(self.test_dir, "test_unc_voice.db")
        self.db = Database(self.db_file)

        bot = MagicMock(spec=commands.Bot)
        bot.add_dynamic_items = MagicMock()
        bot.remove_dynamic_items = MagicMock()
        bot.get_guild.return_value = None
        self.cog = Moderation(bot, db=self.db)
        self.cog.unc_role_ids = self.ROLE_IDS
        self.cog.unc_misclick_max_seconds = 3.0
        self.cog.unc_solo_max_seconds = 15.0
        self.cog.unc_solo_dodge_min_seconds = 10.0
        self.cog.unc_cooldown_seconds = 3600
        self.cog.unc_abuse_threshold = 5
        self.cog.unc_abuse_window_seconds = 604800
        self.cog.unc_abuse_lockout_seconds = 21600
        self.cog.general_channel_id = 500

        self.role = MagicMock()
        self.role.id = self.ROLE_IDS[0]
        self.role.name = "Unc"
        self.general = MagicMock()
        self.general.send = AsyncMock()
        self.guild = MagicMock()
        self.guild.get_role.return_value = self.role
        self.guild.get_channel.return_value = self.general

        self.channel = self._channel(1, "lobby")

    async def asyncTearDown(self):
        await self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _channel(self, cid, name):
        ch = MagicMock()
        ch.id = cid
        ch.name = name
        ch.members = []
        return ch

    def _member(self, uid):
        m = MagicMock()
        m.id = uid
        m.bot = False
        m.guild = self.guild
        m.roles = []
        m.mention = f"<@{uid}>"
        m.name = f"user{uid}"
        m.add_roles = AsyncMock()
        m.remove_roles = AsyncMock()
        return m

    def _state(self, channel):
        s = MagicMock()
        s.channel = channel
        return s

    async def _join(self, member, channel, at):
        channel.members.append(member)
        with patch("cogs.moderation.datetime") as dt:
            dt.now.side_effect = utc_clock(at)
            await self.cog.on_voice_state_update(member, self._state(None), self._state(channel))

    async def _leave(self, member, channel, at):
        channel.members.remove(member)
        with patch("cogs.moderation.datetime") as dt:
            dt.now.side_effect = utc_clock(at)
            await self.cog.on_voice_state_update(member, self._state(channel), self._state(None))

    async def _reset_unc_enforcement(self):
        conn = self.db._get_connection()
        with conn:
            conn.execute("DELETE FROM unc_state;")
            conn.execute("DELETE FROM unc_short_stays;")

    async def _stay(self, member, channel, start, seconds=1.0):
        await self._join(member, channel, start)
        await self._leave(member, channel, start + timedelta(seconds=seconds))

    async def test_alone_for_10_seconds_is_awarded(self):
        m = self._member(1)
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        await self._join(m, self.channel, t0)
        await self._leave(m, self.channel, t0 + timedelta(seconds=10))
        self.assertEqual(await self.db.get_unc_count(1), 1)
        self.general.send.assert_awaited_once()
        msg = self.general.send.call_args.args[0]
        self.assertIn(m.mention, msg)
        self.assertIn("`lobby`", msg)
        self.assertIn("**Unc**", msg)
        self.assertNotIn("Total misclicks", msg)

    async def test_alone_beyond_limit_is_not_awarded(self):
        m = self._member(1)
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        await self._join(m, self.channel, t0)
        await self._leave(m, self.channel, t0 + timedelta(seconds=15.5))
        self.general.send.assert_not_awaited()
        self.assertEqual(await self.db.get_unc_count(1), 0)

    async def test_not_alone_for_10_seconds_is_not_awarded(self):
        m = self._member(1)
        other = self._member(2)
        self.channel.members.append(other)
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        await self._join(m, self.channel, t0)
        await self._leave(m, self.channel, t0 + timedelta(seconds=10))
        self.general.send.assert_not_awaited()

    async def test_someone_joining_then_leaving_voids_solo_status(self):
        m = self._member(1)
        other = self._member(2)
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        await self._join(m, self.channel, t0)
        await self._join(other, self.channel, t0 + timedelta(seconds=2))
        await self._leave(other, self.channel, t0 + timedelta(seconds=4))
        await self._leave(m, self.channel, t0 + timedelta(seconds=10))
        for call in self.general.send.call_args_list:
            self.assertNotIn(m.mention, call.args[0])

    async def test_classic_misclick_under_3s_with_others_present(self):
        m = self._member(1)
        self.channel.members.append(self._member(2))
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        await self._join(m, self.channel, t0)
        await self._leave(m, self.channel, t0 + timedelta(seconds=2))
        self.general.send.assert_awaited_once()

    async def test_solo_limit_boundary_exactly_at_limit_not_awarded(self):
        m = self._member(1)
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        await self._join(m, self.channel, t0)
        await self._leave(m, self.channel, t0 + timedelta(seconds=15))
        self.general.send.assert_not_awaited()

    async def test_solo_award_respects_cooldown(self):
        m = self._member(1)
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        await self._join(m, self.channel, t0)
        await self._leave(m, self.channel, t0 + timedelta(seconds=10))
        await self._join(m, self.channel, t0 + timedelta(seconds=20))
        await self._leave(m, self.channel, t0 + timedelta(seconds=30))
        self.assertEqual(self.general.send.await_count, 1)

    async def _set_count(self, uid, n):
        for _ in range(n):
            await self.db.increment_unc_count(uid)

    async def _catch(self, m, seconds, others=False):
        if others:
            self.channel.members.append(self._member(99))
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        await self._join(m, self.channel, t0)
        await self._leave(m, self.channel, t0 + timedelta(seconds=seconds))
        return self.general.send.call_args.args[0]

    async def test_first_regular_catch_uses_first_message_without_total(self):
        msg = await self._catch(self._member(1), 2, others=True)
        self.assertIn("just earned the **Unc** role for missclicking into the `lobby`", msg)
        self.assertNotIn("Total misclicks", msg)

    async def test_first_solo_catch_uses_first_message(self):
        msg = await self._catch(self._member(1), 10)
        self.assertIn("just earned the **Unc** role for missclicking into the `lobby`", msg)
        self.assertNotIn("Total misclicks", msg)

    async def test_non_first_regular_catch_contains_channel(self):
        await self._set_count(1, 1)
        msg = await self._catch(self._member(1), 2, others=True)
        self.assertNotIn("Total misclicks", msg)
        self.assertIn("`lobby`", msg)

    async def test_non_first_solo_catch_contains_seconds(self):
        await self._set_count(1, 2)
        msg = await self._catch(self._member(1), 10)
        self.assertNotIn("Total misclicks", msg)
        self.assertIn("10", msg)

    async def test_milestone_message_has_no_total_suffix(self):
        await self._set_count(1, 4)
        msg = await self._catch(self._member(1), 10)
        self.assertIn("LEVEL UP", msg)
        self.assertNotIn("Total misclicks", msg)

    async def test_regular_messages_vary_across_catches(self):
        await self._set_count(1, 20)
        seen = set()
        for i in range(60):
            await self._reset_unc_enforcement()
            self.channel.members.clear()
            m = self._member(1)
            seen.add(await self._catch(m, 2, others=True))
        self.assertGreaterEqual(len(seen), 5)

    async def test_solo_stay_under_or_equal_10s_never_uses_dodge_or_trick_messages(self):
        await self._set_count(1, 20)
        dodge_indicators = ["dodge", "hide", "hoping", "trick", "pretending", "Nice try"]
        for _ in range(40):
            await self._reset_unc_enforcement()
            self.channel.members.clear()
            m = self._member(1)
            msg = await self._catch(m, 5)
            for indicator in dodge_indicators:
                self.assertNotIn(indicator, msg)

    async def test_solo_stay_boundary_exactly_10s_does_not_use_dodge_message(self):
        await self._set_count(1, 20)
        dodge_indicators = ["dodge", "hide", "hoping", "trick", "pretending", "Nice try"]
        for _ in range(40):
            await self._reset_unc_enforcement()
            self.channel.members.clear()
            m = self._member(1)
            msg = await self._catch(m, 10)
            for indicator in dodge_indicators:
                self.assertNotIn(indicator, msg)

    async def test_solo_stay_over_10s_uses_dodge_messages(self):
        await self._set_count(1, 20)
        dodge_indicators = ["dodge", "hide", "hoping the bot wouldn't notice", "trick", "pretending", "Nice try"]
        for _ in range(40):
            await self._reset_unc_enforcement()
            self.channel.members.clear()
            m = self._member(1)
            msg = await self._catch(m, 12)
            self.assertTrue(any(indicator in msg for indicator in dodge_indicators), f"Expected dodge indicator in message: {msg}")

    async def test_unc_solo_dodge_min_seconds_from_env(self):
        with patch.dict(os.environ, {"UNC_SOLO_DODGE_MIN_SECONDS": "5.0"}):
            cog = Moderation(MagicMock(), db=self.db)
            self.assertEqual(cog.unc_solo_dodge_min_seconds, 5.0)

    async def test_failed_role_update_does_not_break_tracking_of_the_next_channel(self):
        """Moving between channels leaves one and joins the other; an API error on the award must not lose the join."""
        m = self._member(1)
        m.add_roles.side_effect = discord.HTTPException(MagicMock(status=500, reason="x"), "boom")
        other = self._channel(2, "afk")
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

        await self._join(m, self.channel, t0)
        self.channel.members.remove(m)
        other.members.append(m)
        with patch("cogs.moderation.datetime") as dt:
            dt.now.side_effect = utc_clock(t0 + timedelta(seconds=1))
            await self.cog.on_voice_state_update(m, self._state(self.channel), self._state(other))
        await self._leave(m, other, t0 + timedelta(seconds=2))

        stays = await self.db.get_unc_short_stays(1, 0.0)
        self.assertEqual([stay["channel_id"] for stay in stays], [1, 2])

    T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    EXPECTED_ROASTS = {
        "📉 <@1> tried to farm misclicks in lobby but just exposed themselves as a tryhard. Unc privileges revoked.",
        "🚨 <@1> just got caught gaming the misclick system in lobby. Try harder, boomer.",
        "🕵️ Nice try, <@1>, but spamming lobby won't get you a higher Unc level any faster. Sit down.",
        "🤡 <@1> really thought they could speedrun Unc levels in lobby by joining and leaving repeatedly. We see you.",
        "⚙️ <@1> thinks they found a loophole by spam-clicking lobby. The bot is onto you, granddad.",
    }

    CONFIG_ATTRS = (
        "unc_role_ids", "unc_misclick_max_seconds", "unc_solo_max_seconds",
        "unc_cooldown_seconds", "unc_abuse_threshold", "unc_abuse_window_seconds",
        "unc_abuse_lockout_seconds", "general_channel_id",
    )

    # Leave time (relative to the first stay) of the 5th stay in the standard probe below.
    FLAG_LEAVE = timedelta(hours=8, seconds=1)
    LOCKOUT = 21600

    async def _probe(self, m, start, hours=(0, 2, 4, 6, 8)):
        """One-second stays at the given hour offsets; the default 5 spaced stays end in a flag."""
        for h in hours:
            await self._stay(m, self.channel, start + timedelta(hours=h))

    def _restart_cog(self):
        restarted = Moderation(self.cog.bot, db=self.db)
        for attr in self.CONFIG_ATTRS:
            setattr(restarted, attr, getattr(self.cog, attr))
        self.cog = restarted

    def _flag_lock_end(self, start=None):
        return ((start or self.T0) + self.FLAG_LEAVE).timestamp() + self.LOCKOUT

    # ---- classifier verdicts ------------------------------------------------

    async def test_classifier_returns_award_silent_roast_in_order(self):
        verdicts = []
        for hours in (0, 2, 4, 6, 8):
            joined = self.T0 + timedelta(hours=hours)
            verdicts.append(
                await self.cog._classify_unc_attempt(1, 1, joined, joined + timedelta(seconds=1))
            )
        self.assertEqual(verdicts, ["AWARD", "AWARD", "AWARD", "AWARD", "ROAST"])
        joined = self.T0 + timedelta(hours=9)
        self.assertEqual(
            await self.cog._classify_unc_attempt(1, 1, joined, joined + timedelta(seconds=1)),
            "SILENT",
        )

    async def test_classifier_returns_silent_inside_cooldown(self):
        first = self.T0
        second = self.T0 + timedelta(minutes=10)
        self.assertEqual(
            await self.cog._classify_unc_attempt(1, 1, first, first + timedelta(seconds=1)), "AWARD"
        )
        self.assertEqual(
            await self.cog._classify_unc_attempt(1, 1, second, second + timedelta(seconds=1)), "SILENT"
        )

    # ---- recording ----------------------------------------------------------

    async def test_short_stay_is_stored_as_utc_epoch_seconds(self):
        # 2026-01-01 12:00:00 UTC is 1767268800 seconds after the Unix epoch.
        await self._stay(self._member(1), self.channel, self.T0, seconds=1.5)
        self.assertEqual(
            await self.db.get_unc_short_stays(1, 0.0),
            [{"channel_id": 1, "joined_at": 1767268800.0, "left_at": 1767268801.5}],
        )
        self.assertEqual((await self.db.get_unc_state(1))["last_counted"], 1767268801.5)

    async def test_every_short_stay_is_recorded_even_when_silenced(self):
        m = self._member(1)
        for minutes in (0, 10, 20, 30):
            await self._stay(m, self.channel, self.T0 + timedelta(minutes=minutes))
        self.assertEqual(len(await self.db.get_unc_short_stays(1, 0.0)), 4)
        self.assertEqual(await self.db.get_unc_count(1), 1)

    async def test_stays_during_lockout_are_recorded(self):
        m = self._member(1)
        await self._probe(m, self.T0)
        await self._stay(m, self.channel, self.T0 + timedelta(hours=9))
        self.assertEqual(len(await self.db.get_unc_short_stays(1, 0.0)), 6)

    async def test_long_stays_with_company_are_never_recorded_or_flagged(self):
        m = self._member(1)
        self.channel.members.append(self._member(99))
        for h in (0, 2, 4, 6, 8, 10):
            await self._stay(m, self.channel, self.T0 + timedelta(hours=h), seconds=5.0)
        self.assertEqual(await self.db.get_unc_short_stays(1, 0.0), [])
        self.assertIsNone((await self.db.get_unc_state(1))["locked_until"])
        self.general.send.assert_not_awaited()

    # ---- cooldown -----------------------------------------------------------

    async def test_stay_just_inside_cooldown_is_ignored_and_exactly_at_cooldown_counts(self):
        m = self._member(1)
        await self._stay(m, self.channel, self.T0)
        await self._stay(m, self.channel, self.T0 + timedelta(seconds=1 + 3599 - 1))
        self.assertEqual(await self.db.get_unc_count(1), 1)
        await self._stay(m, self.channel, self.T0 + timedelta(seconds=1 + 3600 - 1))
        self.assertEqual(await self.db.get_unc_count(1), 2)

    async def test_rapid_burst_triggers_roast_on_fifth_attempt(self):
        m = self._member(1)
        for seconds in (0, 3, 6, 9):
            await self._stay(m, self.channel, self.T0 + timedelta(seconds=seconds))
        self.assertEqual(self.general.send.await_count, 1)
        self.assertEqual(await self.db.get_unc_count(1), 1)
        self.assertIsNone((await self.db.get_unc_state(1))["locked_until"])

        # 5th attempt within seconds: triggers ROAST immediately!
        await self._stay(m, self.channel, self.T0 + timedelta(seconds=12))
        self.assertEqual(self.general.send.await_count, 2)
        self.assertEqual(await self.db.get_unc_count(1), 1)
        self.assertIsNotNone((await self.db.get_unc_state(1))["locked_until"])
        self.assertIn(self.general.send.call_args.args[0], self.EXPECTED_ROASTS)

        # 6th attempt within seconds: shadowban traps them (SILENT)
        await self._stay(m, self.channel, self.T0 + timedelta(seconds=15))
        self.assertEqual(self.general.send.await_count, 2)

    # ---- threshold ----------------------------------------------------------

    async def test_fourth_spaced_attempt_is_not_flagged(self):
        m = self._member(1)
        await self._probe(m, self.T0, hours=(0, 2, 4, 6))
        self.assertEqual(await self.db.get_unc_count(1), 4)
        self.assertIsNone((await self.db.get_unc_state(1))["locked_until"])
        self.assertEqual(self.general.send.await_count, 4)

    async def test_fifth_spaced_attempt_is_flagged_and_roasted(self):
        m = self._member(1)
        await self._probe(m, self.T0)
        self.assertEqual(self.general.send.await_count, 5)
        self.assertIn(self.general.send.call_args.args[0], self.EXPECTED_ROASTS)
        self.assertEqual(await self.db.get_unc_count(1), 4)
        self.assertEqual((await self.db.get_unc_state(1))["locked_until"], self._flag_lock_end())

    async def test_flagged_attempt_does_not_touch_roles(self):
        m = self._member(1)
        await self._probe(m, self.T0)
        self.assertEqual(m.add_roles.await_count, 4)
        m.remove_roles.assert_not_awaited()

    async def test_flag_never_clears_the_users_history(self):
        m = self._member(1)
        await self._probe(m, self.T0)
        self.assertEqual(len(await self.db.get_unc_short_stays(1, 0.0)), 5)

    async def test_oldest_attempt_exactly_at_window_edge_still_counts_toward_flag(self):
        m = self._member(1)
        await self._probe(m, self.T0, hours=(0, 48, 96, 120))
        await self._stay(m, self.channel, self.T0 + timedelta(seconds=604800))
        self.assertIsNotNone((await self.db.get_unc_state(1))["locked_until"])

    async def test_oldest_attempt_just_past_window_edge_does_not_count(self):
        m = self._member(1)
        await self._probe(m, self.T0, hours=(0, 48, 96, 120))
        await self._stay(m, self.channel, self.T0 + timedelta(seconds=604801))
        self.assertIsNone((await self.db.get_unc_state(1))["locked_until"])
        self.assertEqual(await self.db.get_unc_count(1), 5)

    async def test_attempts_spread_beyond_window_are_all_awarded(self):
        m = self._member(1)
        await self._probe(m, self.T0, hours=(0, 200, 400, 600, 800))
        self.assertEqual(await self.db.get_unc_count(1), 5)
        self.assertEqual(self.general.send.await_count, 5)
        self.assertIsNone((await self.db.get_unc_state(1))["locked_until"])

    # ---- the rolling trap ---------------------------------------------------

    async def test_attempt_during_lockout_pushes_lockout_six_hours_from_that_attempt(self):
        m = self._member(1)
        await self._probe(m, self.T0)
        sends = self.general.send.await_count
        attempt = self.T0 + timedelta(hours=10)
        await self._stay(m, self.channel, attempt)
        state = await self.db.get_unc_state(1)
        self.assertEqual(state["locked_until"], (attempt + timedelta(seconds=1)).timestamp() + 21600)
        self.assertGreater(state["locked_until"], self._flag_lock_end())
        self.assertEqual(self.general.send.await_count, sends)
        self.assertEqual(await self.db.get_unc_count(1), 4)

    async def test_repeated_attempts_keep_rolling_the_lockout_forward(self):
        m = self._member(1)
        await self._probe(m, self.T0)
        last_leave = None
        for hours in (10, 15, 20, 25):
            attempt = self.T0 + timedelta(hours=hours)
            await self._stay(m, self.channel, attempt)
            last_leave = attempt + timedelta(seconds=1)
            state = await self.db.get_unc_state(1)
            self.assertEqual(state["locked_until"], last_leave.timestamp() + 21600)
        self.assertEqual(await self.db.get_unc_count(1), 4)

    async def test_attempt_just_before_lockout_end_is_trapped(self):
        m = self._member(1)
        await self._probe(m, self.T0)
        unlock = self.T0 + self.FLAG_LEAVE + timedelta(seconds=self.LOCKOUT)
        await self._join(m, self.channel, unlock - timedelta(seconds=2))
        await self._leave(m, self.channel, unlock - timedelta(seconds=1))
        self.assertEqual(await self.db.get_unc_count(1), 4)
        self.assertEqual(
            (await self.db.get_unc_state(1))["locked_until"],
            (unlock - timedelta(seconds=1)).timestamp() + 21600,
        )

    async def test_user_gets_roast_then_silent_and_extension_then_roast_after_lockout_expires(self):
        m = self._member(1)
        # 5 spaced attempts trigger the initial ROAST
        await self._probe(m, self.T0)
        self.assertEqual(self.general.send.await_count, 5)  # 4 awards + 1 roast
        self.assertIn(self.general.send.call_args.args[0], self.EXPECTED_ROASTS)
        flag_locked_until = (self.T0 + self.FLAG_LEAVE).timestamp() + self.LOCKOUT
        self.assertEqual((await self.db.get_unc_state(1))["locked_until"], flag_locked_until)

        # Try again 2 hours later (during active lockout):
        t_2h = self.T0 + self.FLAG_LEAVE + timedelta(hours=2)
        await self._stay(m, self.channel, t_2h)
        # Result is SILENT: sends unchanged, lockout extended 6 hours from this attempt!
        self.assertEqual(self.general.send.await_count, 5)
        extended_locked_until = (t_2h + timedelta(seconds=1)).timestamp() + self.LOCKOUT
        self.assertEqual((await self.db.get_unc_state(1))["locked_until"], extended_locked_until)

        # Wait for the lockout to completely expire (e.g. 6 hours + 1 minute after t_2h):
        t_after_lockout = t_2h + timedelta(seconds=self.LOCKOUT + 60)
        await self._stay(m, self.channel, t_after_lockout)
        # Because history is never cleared, user still has 5+ attempts in last 7 days -> ROAST again!
        self.assertEqual(self.general.send.await_count, 6)
        self.assertIn(self.general.send.call_args.args[0], self.EXPECTED_ROASTS)
        new_locked_until = (t_after_lockout + timedelta(seconds=1)).timestamp() + self.LOCKOUT
        self.assertEqual((await self.db.get_unc_state(1))["locked_until"], new_locked_until)

    async def test_attempt_at_lockout_end_triggers_roast_if_threshold_still_met(self):
        m = self._member(1)
        await self._probe(m, self.T0)
        unlock = self.T0 + self.FLAG_LEAVE + timedelta(seconds=self.LOCKOUT)
        await self._join(m, self.channel, unlock - timedelta(seconds=1))
        await self._leave(m, self.channel, unlock)
        # Lockout ended, but threshold within rolling window is still met -> ROAST
        self.assertEqual(self.general.send.await_count, 6)
        self.assertEqual(await self.db.get_unc_count(1), 4)
        self.assertEqual((await self.db.get_unc_state(1))["locked_until"], unlock.timestamp() + self.LOCKOUT)

    async def test_after_stays_age_out_past_seven_days_user_is_awarded_again(self):
        m = self._member(1)
        await self._probe(m, self.T0)
        # Stays aged out of the 7-day window
        t_fresh = self.T0 + timedelta(days=8)
        await self._stay(m, self.channel, t_fresh)
        # Attempt count in 7-day window is 1 -> AWARD
        self.assertEqual(await self.db.get_unc_count(1), 5)
        self.assertEqual(self.general.send.await_count, 6)

    # ---- persistence --------------------------------------------------------

    async def test_cooldown_survives_cog_restart(self):
        m = self._member(1)
        await self._stay(m, self.channel, self.T0)
        self._restart_cog()
        await self._stay(m, self.channel, self.T0 + timedelta(minutes=30))
        self.assertEqual(await self.db.get_unc_count(1), 1)
        self.assertEqual(self.general.send.await_count, 1)

    async def test_pattern_history_survives_cog_restart(self):
        m = self._member(1)
        await self._probe(m, self.T0, hours=(0, 2, 4, 6))
        self._restart_cog()
        await self._stay(m, self.channel, self.T0 + timedelta(hours=8))
        self.assertIsNotNone((await self.db.get_unc_state(1))["locked_until"])

    async def test_lockout_survives_cog_restart(self):
        m = self._member(1)
        await self._probe(m, self.T0)
        self._restart_cog()
        attempt = self.T0 + timedelta(hours=10)
        await self._stay(m, self.channel, attempt)
        self.assertEqual(await self.db.get_unc_count(1), 4)
        self.assertEqual(
            (await self.db.get_unc_state(1))["locked_until"],
            (attempt + timedelta(seconds=1)).timestamp() + 21600,
        )

    # ---- isolation, messages, failure paths, config -------------------------

    async def test_users_are_tracked_independently(self):
        spammer = self._member(1)
        innocent = self._member(2)
        await self._probe(spammer, self.T0)
        await self._stay(innocent, self.channel, self.T0 + timedelta(hours=9))
        self.assertEqual(await self.db.get_unc_count(2), 1)
        self.assertIsNone((await self.db.get_unc_state(2))["locked_until"])
        self.assertIn("<@2>", self.general.send.call_args.args[0])
        self.assertIsNotNone((await self.db.get_unc_state(1))["locked_until"])

    async def test_roast_messages_match_expected_texts_and_all_appear(self):
        seen = set()
        for _ in range(80):
            await self._reset_unc_enforcement()
            await self._probe(self._member(1), self.T0)
            seen.add(self.general.send.call_args.args[0])
        self.assertEqual(seen, self.EXPECTED_ROASTS)

    async def test_flag_survives_announcement_failure(self):
        m = self._member(1)
        await self._probe(m, self.T0, hours=(0, 2, 4, 6))
        self.general.send.side_effect = discord.HTTPException(MagicMock(status=500, reason="x"), "boom")
        await self._stay(m, self.channel, self.T0 + timedelta(hours=8))
        self.assertEqual((await self.db.get_unc_state(1))["locked_until"], self._flag_lock_end())

    async def test_flag_without_general_channel_still_locks_out(self):
        m = self._member(1)
        await self._probe(m, self.T0, hours=(0, 2, 4, 6))
        self.guild.get_channel.return_value = None
        await self._stay(m, self.channel, self.T0 + timedelta(hours=8))
        self.assertEqual((await self.db.get_unc_state(1))["locked_until"], self._flag_lock_end())
        self.assertEqual(self.general.send.await_count, 4)

    async def test_abuse_settings_default_values(self):
        with patch.dict(os.environ, {}, clear=False):
            for key in ("UNC_ABUSE_THRESHOLD", "UNC_ABUSE_WINDOW_SECONDS", "UNC_ABUSE_LOCKOUT_SECONDS"):
                os.environ.pop(key, None)
            cog = Moderation(MagicMock(), db=self.db)
        self.assertEqual(cog.unc_abuse_threshold, 5)
        self.assertEqual(cog.unc_abuse_window_seconds, 604800)
        self.assertEqual(cog.unc_abuse_lockout_seconds, 21600)

    async def test_abuse_settings_from_env(self):
        env = {
            "UNC_ABUSE_THRESHOLD": "7",
            "UNC_ABUSE_WINDOW_SECONDS": "7200",
            "UNC_ABUSE_LOCKOUT_SECONDS": "600",
        }
        with patch.dict(os.environ, env):
            cog = Moderation(MagicMock(), db=self.db)
        self.assertEqual(cog.unc_abuse_threshold, 7)
        self.assertEqual(cog.unc_abuse_window_seconds, 7200)
        self.assertEqual(cog.unc_abuse_lockout_seconds, 600)


class TestTrustAndAntiNuke(unittest.IsolatedAsyncioTestCase):
    T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.test_dir, "test_trust.db"))
        bot = MagicMock(spec=commands.Bot)
        self.cog = Moderation(bot, db=self.db)
        self.cog.trusted_role_id = 605
        self.cog.inmate_role_id = 888
        self.cog.quarantine_role_id = 999
        self.cog.trust_threshold = 3
        self.cog.trust_cooldown_seconds = 60
        self.cog.anti_nuke_limit = 2
        self.cog.anti_nuke_window = 600
        self.cog.mods_chat_id = 333

        self.trusted_role = MagicMock(spec=discord.Role, id=605, managed=False)
        self.q_role = MagicMock(spec=discord.Role, id=999, managed=False)
        self.mods_chat = MagicMock(spec=discord.TextChannel, id=333)
        self.mods_chat.send = AsyncMock()

        self.guild = MagicMock(spec=discord.Guild)
        self.guild.name = "Test Guild"
        self.guild.get_role.side_effect = {605: self.trusted_role, 999: self.q_role}.get
        self.guild.get_channel.side_effect = {333: self.mods_chat}.get
        self.members = {}
        self.guild.get_member.side_effect = lambda uid: self.members.get(uid)
        self.audit_entries = []  # newest first, like Discord returns them
        self.guild.audit_logs = MagicMock(side_effect=self._audit_logs)

    async def asyncTearDown(self):
        await self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _member(self, uid, roles=(), bot=False):
        member = MagicMock(spec=discord.Member)
        member.id = uid
        member.name = f"user{uid}"
        member.mention = f"<@{uid}>"
        member.bot = bot
        member.guild = self.guild
        member.roles = list(roles)
        member.add_roles = AsyncMock()
        member.edit = AsyncMock()
        member.kick = AsyncMock()
        member.send = AsyncMock()
        self.members[uid] = member
        return member

    # ---- trust --------------------------------------------------------------

    async def _say(self, author, seconds, guild="default"):
        message = MagicMock(spec=discord.Message)
        message.author = author
        message.guild = self.guild if guild == "default" else guild
        with patch("cogs.moderation.datetime") as dt:
            dt.now.side_effect = utc_clock(self.T0 + timedelta(seconds=seconds))
            await self.cog.on_message(message)

    async def test_trusted_role_awarded_exactly_at_threshold(self):
        member = self._member(1)
        await self._say(member, 0)
        await self._say(member, 60)
        self.assertEqual(await self.db.get_trust_points(1), 2.0)
        member.add_roles.assert_not_awaited()

        await self._say(member, 120)

        member.add_roles.assert_awaited_once()
        self.assertIs(member.add_roles.call_args.args[0], self.trusted_role)
        member.send.assert_awaited_once()
        self.assertEqual(await self.db.get_trust_points(1), 0.0)

    async def test_messages_inside_cooldown_do_not_count(self):
        member = self._member(1)
        await self._say(member, 0)
        await self._say(member, 1)
        await self._say(member, 59)
        self.assertEqual(await self.db.get_trust_points(1), 1.0)
        await self._say(member, 60)
        self.assertEqual(await self.db.get_trust_points(1), 2.0)

    async def test_cooldown_is_tracked_per_user(self):
        await self._say(self._member(1), 0)
        await self._say(self._member(2), 1)
        self.assertEqual(await self.db.get_trust_points(1), 1.0)
        self.assertEqual(await self.db.get_trust_points(2), 1.0)

    async def test_restricted_trusted_bot_and_dm_messages_earn_nothing(self):
        await self._say(self._member(1, roles=[self.q_role]), 0)
        await self._say(self._member(2, roles=[MagicMock(spec=discord.Role, id=888)]), 0)
        await self._say(self._member(3, roles=[self.trusted_role]), 0)
        await self._say(self._member(4, bot=True), 0)
        await self._say(self._member(5), 0, guild=None)
        for uid in (1, 2, 3, 4, 5):
            self.assertEqual(await self.db.get_trust_points(uid), 0.0)

    async def test_author_without_member_data_is_ignored(self):
        user = MagicMock(spec=discord.User)
        user.id = 6
        user.bot = False
        await self._say(user, 0)
        self.assertEqual(await self.db.get_trust_points(6), 0.0)

    async def test_failed_role_grant_is_retried_on_a_later_message(self):
        member = self._member(1)
        member.add_roles.side_effect = discord.Forbidden(MagicMock(status=403, reason="x"), "nope")
        for seconds in (0, 60, 120):
            await self._say(member, seconds)
        self.assertEqual(await self.db.get_trust_points(1), 2.0)

        member.add_roles.side_effect = None
        await self._say(member, 180)
        self.assertEqual(member.add_roles.await_count, 2)
        self.assertEqual(await self.db.get_trust_points(1), 0.0)

    async def test_closed_dms_do_not_block_the_role_grant(self):
        member = self._member(1)
        member.send.side_effect = discord.Forbidden(MagicMock(status=403, reason="x"), "closed")
        for seconds in (0, 60, 120):
            await self._say(member, seconds)
        member.add_roles.assert_awaited_once()
        self.assertEqual(await self.db.get_trust_points(1), 0.0)

    # ---- anti-nuke ----------------------------------------------------------

    def _audit_logs(self, limit=None, action=None):
        entries = self.audit_entries[:limit]

        async def iterate():
            for entry in entries:
                yield entry

        return iterate()

    def _channel_deleted_by(self, channel_id, deleter):
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        channel.guild = self.guild
        entry = MagicMock()
        entry.target.id = channel_id
        entry.user = deleter
        self.audit_entries.insert(0, entry)
        return channel

    async def _handle_deletion(self, channel, seconds=0):
        with patch("asyncio.sleep", new_callable=AsyncMock), patch("cogs.moderation.datetime") as dt:
            dt.now.side_effect = utc_clock(self.T0 + timedelta(seconds=seconds))
            await self.cog.on_guild_channel_delete(channel)

    async def test_single_deletion_is_tolerated(self):
        nuker = self._member(50)
        await self._handle_deletion(self._channel_deleted_by(1, nuker))
        nuker.edit.assert_not_awaited()
        self.mods_chat.send.assert_not_awaited()

    async def test_rapid_deletions_strip_roles_even_when_audit_log_has_newer_entries(self):
        """In a burst, every handler sees later deletions on top of the audit log and must still find its own."""
        nuker = self._member(50)
        first = self._channel_deleted_by(1, nuker)
        second = self._channel_deleted_by(2, nuker)
        third = self._channel_deleted_by(3, nuker)

        await self._handle_deletion(first)
        nuker.edit.assert_not_awaited()
        await self._handle_deletion(second)

        nuker.edit.assert_awaited_once()
        self.assertEqual(nuker.edit.call_args.kwargs["roles"], [])
        self.mods_chat.send.assert_awaited_once()
        self.assertIn("ANTI-NUKE", self.mods_chat.send.call_args.args[0])
        self.assertIn("<@50>", self.mods_chat.send.call_args.args[0])

        # The tally restarts after the lockdown; a single further deletion does not re-trigger.
        await self._handle_deletion(third)
        nuker.edit.assert_awaited_once()

    async def test_lockdown_removes_everything_except_managed_roles(self):
        admin_role = MagicMock(spec=discord.Role, id=70, managed=False)
        booster_role = MagicMock(spec=discord.Role, id=71, managed=True)
        nuker = self._member(50, roles=[admin_role, booster_role])
        await self._handle_deletion(self._channel_deleted_by(1, nuker))
        await self._handle_deletion(self._channel_deleted_by(2, nuker))
        self.assertEqual(nuker.edit.call_args.kwargs["roles"], [booster_role])

    async def test_rogue_bot_is_kicked(self):
        rogue = self._member(60, bot=True)
        await self._handle_deletion(self._channel_deleted_by(1, rogue))
        await self._handle_deletion(self._channel_deleted_by(2, rogue))
        rogue.kick.assert_awaited_once()
        rogue.edit.assert_not_awaited()
        self.assertIn("kicked", self.mods_chat.send.call_args.args[0])

    async def test_deletions_by_different_users_are_counted_separately(self):
        a, b = self._member(50), self._member(51)
        await self._handle_deletion(self._channel_deleted_by(1, a))
        await self._handle_deletion(self._channel_deleted_by(2, b))
        a.edit.assert_not_awaited()
        b.edit.assert_not_awaited()

    async def test_window_boundary(self):
        slow = self._member(50)
        await self._handle_deletion(self._channel_deleted_by(1, slow), seconds=0)
        await self._handle_deletion(self._channel_deleted_by(2, slow), seconds=601)
        slow.edit.assert_not_awaited()

        fast = self._member(51)
        await self._handle_deletion(self._channel_deleted_by(3, fast), seconds=1000)
        await self._handle_deletion(self._channel_deleted_by(4, fast), seconds=1600)
        fast.edit.assert_awaited_once()

    async def test_deletion_missing_from_audit_log_is_ignored(self):
        nuker = self._member(50)
        self._channel_deleted_by(1, nuker)
        unknown = MagicMock(spec=discord.TextChannel)
        unknown.id = 999
        unknown.guild = self.guild
        await self._handle_deletion(unknown)
        await self._handle_deletion(unknown)
        nuker.edit.assert_not_awaited()

    async def test_unreadable_audit_log_does_not_raise(self):
        self.guild.audit_logs = MagicMock(
            side_effect=discord.Forbidden(MagicMock(status=403, reason="x"), "Missing Permissions")
        )
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 1
        channel.guild = self.guild
        await self._handle_deletion(channel)
        self.mods_chat.send.assert_not_awaited()

    async def test_failed_lockdown_alerts_moderators(self):
        nuker = self._member(50)
        nuker.edit.side_effect = discord.Forbidden(MagicMock(status=403, reason="x"), "too low")
        await self._handle_deletion(self._channel_deleted_by(1, nuker))
        await self._handle_deletion(self._channel_deleted_by(2, nuker))
        self.assertIn("URGENT", self.mods_chat.send.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
