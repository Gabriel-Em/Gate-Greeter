import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import discord
from discord.ext import commands
from cogs.moderation import Moderation, QuarantineReleaseButton, QUARANTINE_FILE, PRISON_FILE
from database import Database
from utils import load_json_data, save_json_data


class TestQuarantineAndModeration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Use temporary file paths for tests
        self.test_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "tmp"))
        os.makedirs(self.test_dir, exist_ok=True)
        self.orig_q_file = QUARANTINE_FILE
        self.orig_p_file = PRISON_FILE

        self.test_db_file = os.path.join(self.test_dir, "test_moderation.db")
        if os.path.exists(self.test_db_file):
            os.remove(self.test_db_file)
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

        self.default_role = MagicMock(spec=discord.Role, id=1000, name="@everyone")
        self.guild.default_role = self.default_role

        self.q_role = MagicMock(spec=discord.Role, id=999, name="Quarantine")
        self.category_role = MagicMock(spec=discord.Role, id=500, name="Category")
        self.user_role1 = MagicMock(spec=discord.Role, id=601, name="Role1", managed=False)
        self.user_role2 = MagicMock(spec=discord.Role, id=602, name="Role2", managed=False)
        self.staff_role = MagicMock(spec=discord.Role, id=700, name="Moderator")
        self.bot_top_role = MagicMock(spec=discord.Role, id=9999, name="Bot Top")

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
        for f in [self.test_db_file, self.test_db_file + "-wal", self.test_db_file + "-shm"]:
            if os.path.exists(f):
                os.remove(f)
        if os.path.exists(self.test_dir):
            try:
                os.rmdir(self.test_dir)
            except OSError:
                pass

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


if __name__ == "__main__":
    unittest.main()
