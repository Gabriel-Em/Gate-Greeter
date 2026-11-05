import os
import asyncio
import re
from datetime import datetime
import discord
from discord.ext import commands

from utils import (
    ensure_json_file,
    load_json_data,
    save_json_data,
    get_env_int,
    get_env_float,
    get_env_int_list,
    is_env_enabled,
)

# ---------------------------------------------------------
# MODULE-LEVEL CONSTANTS
# ---------------------------------------------------------
PRISON_FILE = "prison_data.json"
UNC_FILE = "unc_data.json"
TRUST_FILE = "trust_data.json"
QUARANTINE_FILE = "quarantine_data.json"


# ---------------------------------------------------------
# PERSISTENT INTERACTIVE COMPONENTS
# ---------------------------------------------------------
class QuarantineReleaseButton(discord.ui.DynamicItem[discord.ui.Button], template=r"quarantine:release:(?P<user_id>[0-9]+)"):
    def __init__(self, user_id: int = 0):
        super().__init__(
            discord.ui.Button(
                label="Release into the Wild",
                style=discord.ButtonStyle.success,
                custom_id=f"quarantine:release:{user_id}",
                emoji="🌿",
            )
        )
        self.user_id = user_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        user_id = int(match.group("user_id"))
        return cls(user_id)

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Moderation")
        if not cog:
            return await interaction.response.send_message("⚠️ Moderation module is not loaded.", ephemeral=True)

        is_staff = (
            any(role.id in cog.staff_role_ids for role in interaction.user.roles)
            or interaction.user.guild_permissions.manage_roles
            or interaction.user.guild_permissions.administrator
        )
        if not is_staff:
            return await interaction.response.send_message("🚫 You don't have permission to release quarantined users.", ephemeral=True)

        guild = interaction.guild
        if not guild:
            return await interaction.response.send_message("⚠️ Server not found.", ephemeral=True)

        member = guild.get_member(self.user_id)
        if not member:
            try:
                member = await guild.fetch_member(self.user_id)
            except (discord.NotFound, discord.HTTPException):
                return await interaction.response.send_message("⚠️ Member is no longer in this server.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        success, msg = await cog.release_quarantined_member(
            member,
            released_by=interaction.user,
            channel=interaction.channel,
            reply_to=interaction.message,
        )

        if success:
            # Disable button and update label on message
            self.item.disabled = True
            self.item.label = f"Released by {interaction.user.display_name}"
            self.item.style = discord.ButtonStyle.secondary
            try:
                target_view = self.view or discord.ui.View.from_message(interaction.message)
                for child in target_view.children:
                    if isinstance(child, discord.ui.Button) and child.custom_id == self.custom_id:
                        child.disabled = True
                        child.label = f"Released by {interaction.user.display_name}"
                        child.style = discord.ButtonStyle.secondary
                await interaction.message.edit(view=target_view)
            except Exception:
                pass
            await interaction.followup.send(f"✅ Released {member.name} into the wild.", ephemeral=True)
        else:
            await interaction.followup.send(f"⚠️ {msg}", ephemeral=True)


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Runtime Memory Trackers
        self.voice_join_times = {}
        self.unc_cooldowns = {}
        self.trust_cooldowns = {}
        self.channel_deletions = {}

        # Async Locks for File I/O Safety
        self.prison_lock = asyncio.Lock()
        self.unc_lock = asyncio.Lock()
        self.trust_lock = asyncio.Lock()
        self.quarantine_lock = asyncio.Lock()

        # Load Channel IDs from Environment
        self.general_channel_id = get_env_int("GENERAL_CHANNEL_ID")
        self.quarantine_channel_id = get_env_int("QUARANTINE_CHANNEL_ID")
        self.quarantine_logs_channel_id = get_env_int("QUARANTINE_LOGS_CHANNEL_ID")
        self.mods_chat_id = get_env_int("MODS_CHAT_ID")

        # Load Role IDs from Environment
        self.inmate_role_id = get_env_int("INMATE_ROLE_ID")
        self.quarantine_role_id = get_env_int("QUARANTINE_ROLE_ID")
        self.trusted_role_id = get_env_int("TRUSTED_ROLE_ID")

        self.unc_role_ids = get_env_int_list("UNC_ROLE_IDS")
        self.staff_role_ids = get_env_int_list("STAFF_ROLE_IDS")
        self.category_role_ids = get_env_int_list("CATEGORY_ROLE_IDS")
        self.pronoun_role_ids = get_env_int_list("PRONOUN_ROLE_IDS")

        # Load Thresholds & Timers from Environment
        self.quarantine_days = get_env_int("QUARANTINE_ACCOUNT_AGE_DAYS", 7)
        self.quarantine_new_member_days = get_env_int("QUARANTINE_NEW_MEMBER_DAYS", 7)
        self.trust_threshold = get_env_int("TRUST_THRESHOLD", 30)
        self.trust_cooldown_seconds = get_env_int("TRUST_COOLDOWN_SECONDS", 60)
        self.unc_misclick_max_seconds = get_env_float("UNC_MISCLICK_MAX_SECONDS", 3.0)
        self.unc_cooldown_seconds = get_env_int("UNC_COOLDOWN_SECONDS", 3600)
        self.anti_nuke_limit = get_env_int("ANTI_NUKE_CHANNEL_LIMIT", 2)
        self.anti_nuke_window = get_env_int("ANTI_NUKE_WINDOW_SECONDS", 600)

        # Initialize JSON Ledgers
        ensure_json_file(PRISON_FILE)
        ensure_json_file(UNC_FILE)
        ensure_json_file(TRUST_FILE)
        ensure_json_file(QUARANTINE_FILE)

    async def cog_load(self):
        self.bot.add_dynamic_items(QuarantineReleaseButton)

    async def cog_unload(self):
        self.bot.remove_dynamic_items(QuarantineReleaseButton)

    @commands.Cog.listener()
    async def on_ready(self):
        unc_tier_names = ["Unc", "Grand Unc", "Great-Grand Unc", "Ancestral Unc", "The First Unc"]

        for guild in self.bot.guilds:
            me = guild.me
            perms = me.guild_permissions if me else None
            top_role = me.top_role if me else None
            is_admin = perms.administrator if perms else False

            print("\n" + "=" * 70)
            print(f"[Audit] Comprehensive Setup Audit for '{guild.name}' (ID: {guild.id})")
            print("=" * 70)
            if me:
                print(f"Bot Identity: {me} | Top Role: @{top_role.name} (Position: {top_role.position})")

            # 1. Guild-Level Bot Permissions
            print("\n[Guild Permissions]")
            if perms:
                print(f"  - Manage Server (manage_guild):    {'✅ Granted' if perms.manage_guild else '❌ MISSING (Required for AutoMod execution events!)'}")
                print(f"  - Manage Roles (manage_roles):      {'✅ Granted' if perms.manage_roles else '❌ MISSING (Required to assign/remove roles!)'}")
                print(f"  - View Audit Log (view_audit_log):  {'✅ Granted' if perms.view_audit_log else '❌ MISSING (Required for anti-nuke & evasion tracking!)'}")
                print(f"  - Kick Members (kick_members):      {'✅ Granted' if perms.kick_members else '⚠️ Not granted (Anti-nuke cannot kick rogue bots)'}")
                print(f"  - Administrator:                    {'✅ True (Overrides channel restrictions)' if is_admin else 'False (Standard permissions apply)'}")
            else:
                print("  ❌ Could not resolve bot member object in guild.")

            # 2. Configured Channels & Access
            print("\n[Channels & Access]")
            channels_to_check = [
                ("Quarantine Channel", self.quarantine_channel_id),
                ("Quarantine Logs", self.quarantine_logs_channel_id),
                ("General Chat", self.general_channel_id),
                ("Mods Chat", self.mods_chat_id),
            ]
            for label, cid in channels_to_check:
                if not cid:
                    print(f"  - {label}: ⚠️ Not configured in .env")
                    continue
                ch = guild.get_channel(cid)
                if not ch:
                    print(f"  - {label} (ID: {cid}): ❌ NOT FOUND in guild")
                    continue

                if me and hasattr(ch, "permissions_for"):
                    cp = ch.permissions_for(me)
                    can_view = cp.view_channel or is_admin
                    can_send = cp.send_messages or is_admin
                    if can_view and can_send:
                        status = "✅ Accessible (View: ✅, Send: ✅)"
                    else:
                        issues = []
                        if not can_view:
                            issues.append("View: ❌")
                        if not can_send:
                            issues.append("Send: ❌")
                        status = f"⚠️ Limited ({', '.join(issues)})"
                else:
                    status = "✅ Found"
                print(f"  - {label} (ID: {cid}): #{ch.name} -> {status}")

            # 3. Actionable Managed Roles & Hierarchy
            print("\n[Managed Roles & Hierarchy]")
            single_roles = [
                ("Quarantine Role", self.quarantine_role_id),
                ("Inmate Role", self.inmate_role_id),
                ("Trusted Role", self.trusted_role_id),
            ]
            for label, rid in single_roles:
                if not rid:
                    print(f"  - {label}: ⚠️ Not configured in .env")
                    continue
                role = guild.get_role(rid)
                if not role:
                    print(f"  - {label} (ID: {rid}): ❌ NOT FOUND in guild")
                    continue

                if me and top_role:
                    if is_admin or top_role > role:
                        hier_status = "✅ Manageable (Hierarchy OK)"
                    else:
                        hier_status = f"❌ Hierarchy error: Role is equal or higher than bot's top role (@{top_role.name}); bot CANNOT assign it!"
                else:
                    hier_status = "✅ Found"
                print(f"  - {label} (ID: {rid}): @{role.name} -> {hier_status}")

            # UNC Role Tiers
            print(f"  - UNC Roles ({len(self.unc_role_ids)} configured):")
            if not self.unc_role_ids:
                print("      ⚠️ None configured in .env (UNC_ROLE_IDS)")
            else:
                for idx, rid in enumerate(self.unc_role_ids):
                    expected_tier = unc_tier_names[idx] if idx < len(unc_tier_names) else f"Tier {idx+1}"
                    role = guild.get_role(rid)
                    if not role:
                        print(f"      Tier {idx+1} ({expected_tier}, ID: {rid}): ❌ NOT FOUND")
                        continue
                    if me and top_role and not is_admin and top_role <= role:
                        hier = "❌ Bot role is below this tier; cannot assign!"
                    else:
                        hier = "✅ OK"
                    print(f"      Tier {idx+1} ({expected_tier}, ID: {rid}): @{role.name} -> {hier}")

            # 4. Reference Role Sets
            print("\n[Reference Roles]")
            ref_role_sets = [
                ("Staff Roles", self.staff_role_ids),
                ("Category Divider Roles", self.category_role_ids),
                ("Pronoun Roles", self.pronoun_role_ids),
            ]
            for label, rids in ref_role_sets:
                if not rids:
                    print(f"  - {label}: ⚠️ None configured in .env")
                    continue
                found_roles = [guild.get_role(rid) for rid in rids if guild.get_role(rid)]
                missing_count = len(rids) - len(found_roles)
                status = f"✅ {len(found_roles)}/{len(rids)} found"
                if missing_count > 0:
                    status += f" (⚠️ {missing_count} ID(s) not found in guild)"
                print(f"  - {label}: {status}")
                if found_roles:
                    names_str = ", ".join(f"@{r.name}" for r in found_roles[:6])
                    if len(found_roles) > 6:
                        names_str += f" (+{len(found_roles) - 6} more)"
                    print(f"      Names: {names_str}")

            print("=" * 70 + "\n")

    def get_quarantine_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        if self.quarantine_channel_id:
            ch = guild.get_channel(self.quarantine_channel_id)
            if ch:
                return ch
        if self.quarantine_logs_channel_id:
            return guild.get_channel(self.quarantine_logs_channel_id)
        return None

    def get_quarantine_logs_channel(self, guild: discord.Guild) -> discord.TextChannel | None:
        if self.quarantine_logs_channel_id:
            ch = guild.get_channel(self.quarantine_logs_channel_id)
            if ch:
                return ch
        if self.quarantine_channel_id:
            return guild.get_channel(self.quarantine_channel_id)
        return None

    # ---------------------------------------------------------
    # VOICE MISCLICK TRACKER (The Unc Dynasty)
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot or before.channel == after.channel:
            return

        now = datetime.now()

        # Event: Left a channel
        if before.channel is not None:
            record = self.voice_join_times.get(member.id)
            if record and record["channel_id"] == before.channel.id:
                duration = (now - record["time"]).total_seconds()

                if duration < self.unc_misclick_max_seconds:
                    last_misclick = self.unc_cooldowns.get(member.id)
                    if last_misclick and (now - last_misclick).total_seconds() < self.unc_cooldown_seconds:
                        self.voice_join_times.pop(member.id, None)
                        return

                    self.unc_cooldowns[member.id] = now

                    # Ensure UNC_ROLE_IDS has all 5 tiers configured
                    if len(self.unc_role_ids) < 5:
                        print("⚠️ UNC_ROLE_IDS in .env does not contain all 5 role IDs.")
                        self.voice_join_times.pop(member.id, None)
                        return

                    # 1. Safely load and increment count
                    async with self.unc_lock:
                        unc_data = load_json_data(UNC_FILE)
                        uid = str(member.id)
                        current_count = unc_data.get(uid, 0) + 1
                        unc_data[uid] = current_count
                        save_json_data(UNC_FILE, unc_data)

                    # 2. Determine level and target role ID
                    if current_count < 5:
                        target_role_id = self.unc_role_ids[0]
                    elif current_count < 10:
                        target_role_id = self.unc_role_ids[1]
                    elif current_count < 15:
                        target_role_id = self.unc_role_ids[2]
                    elif current_count < 20:
                        target_role_id = self.unc_role_ids[3]
                    else:
                        target_role_id = self.unc_role_ids[4]

                    target_role = member.guild.get_role(target_role_id)

                    # 3. Apply role and announce
                    if target_role:
                        try:
                            old_roles = [
                                r for r in member.roles
                                if r.id in self.unc_role_ids and r.id != target_role.id
                            ]
                            if old_roles:
                                await member.remove_roles(*old_roles)

                            if target_role not in member.roles:
                                await member.add_roles(target_role)

                            general_channel = member.guild.get_channel(self.general_channel_id)
                            if general_channel:
                                role_name = target_role.name
                                if current_count == 1:
                                    msg = f"👴 {member.mention} just earned the **{role_name}** role for missclicking into the `{before.channel.name}` voice channel!"
                                elif current_count == 5:
                                    msg = f"🌟 **LEVEL UP!** {member.mention} misclicked for the 5th time and evolved into a **{role_name}**!"
                                elif current_count == 10:
                                    msg = f"📜 **LEVEL UP!** {member.mention} hit 10 misclicks and aged into a **{role_name}**!"
                                elif current_count == 15:
                                    msg = f"🏺 **LEVEL UP!** {member.mention} reached 15 misclicks and is now recognized as an **{role_name}**!"
                                elif current_count == 20:
                                    msg = f"👑 **MAX LEVEL!** {member.mention} achieved 20 misclicks and has ascended to **{role_name}**!"
                                else:
                                    msg = f"👴 {member.mention} just misclicked into `{before.channel.name}`! (Total misclicks: {current_count})"

                                await general_channel.send(msg)
                                log_msg = msg.replace(member.mention, member.name)
                                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {log_msg}")

                        except discord.Forbidden:
                            print(f"⚠️ Could not manage unc roles for {member.name}: Bot lacks permissions or hierarchy is incorrect.")

                self.voice_join_times.pop(member.id, None)

        # Event: Joined a channel
        if after.channel is not None:
            self.voice_join_times[member.id] = {
                "time": now,
                "channel_id": after.channel.id,
            }

    # ---------------------------------------------------------
    # PRISON COMMANDS
    # ---------------------------------------------------------
    @commands.command(name="prison")
    @commands.has_permissions(manage_roles=True)
    async def prison(self, ctx, member: discord.Member):
        if os.getenv("ENABLE_CMD_PRISON", "True").lower() not in ("true", "1", "t"):
            return

        if any(role.id in self.staff_role_ids for role in member.roles):
            return await ctx.send(f"🚫 Cannot imprison staff member {member.mention}.")

        user_id_str = str(member.id)

        async with self.prison_lock:
            data = load_json_data(PRISON_FILE)
            if user_id_str in data:
                return await ctx.send(f"⚠️ {member.mention} is already in prison.")

            roles_to_save = [
                role.id for role in member.roles
                if role.id != ctx.guild.id and role.id not in self.category_role_ids and not role.managed
            ]
            data[user_id_str] = roles_to_save
            save_json_data(PRISON_FILE, data)

        new_roles = [role for role in member.roles if role.id in self.category_role_ids]
        inmate_role = ctx.guild.get_role(self.inmate_role_id)
        if inmate_role:
            new_roles.append(inmate_role)

        await member.edit(roles=new_roles, reason="Sent to Rada's Prison Island")
        await ctx.send(f"🔒 {member.mention} has been sent to **Rada’s Prison Island**.")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔒 {member.name} has been sent to Rada’s Prison Island.")

    async def send_release_dm(
        self,
        member: discord.Member,
        released_by_name: str | None,
        q_type: str,
    ) -> bool:
        """Sends a release explanation DM to the member tailored to their quarantine type."""
        by_str = f" by **{released_by_name}**" if released_by_name else ""
        guild_name = member.guild.name if member.guild else "the server"

        if q_type == "automod":
            dm_text = (
                f"🌿 You have been released from quarantine in **{guild_name}**{by_str}!\n\n"
                f"Your previous roles and server access have been restored. Please make sure to follow the server rules."
            )
        elif q_type == "account_age":
            dm_text = (
                f"🌿 You have been released from quarantine in **{guild_name}**{by_str}!\n\n"
                f"Welcome to the server! Head over to **Channels & Roles** at the top of the server to complete your onboarding and pick your roles."
            )
        else:
            dm_text = (
                f"🌿 You have been released from quarantine in **{guild_name}**{by_str}!\n\n"
                f"Your server access has been restored."
            )

        try:
            await member.send(dm_text)
            return True
        except (discord.Forbidden, discord.HTTPException):
            print(f"⚠️ Could not send release DM to {member.name} (DMs may be disabled).")
            return False

    # ---------------------------------------------------------
    # QUARANTINE CORE HELPERS
    # ---------------------------------------------------------
    async def quarantine_member(
        self,
        member: discord.Member,
        reason: str,
        is_automod: bool = False,
        automod_details: dict | None = None,
        is_evasion: bool = False,
        quarantine_type: str | None = None,
        quarantined_by: discord.Member | discord.User | None = None,
    ) -> bool:
        """Quarantines a member, saves their prior roles, and announces it with a release button."""
        if member.bot:
            return False

        if any(role.id in self.staff_role_ids for role in member.roles):
            return False

        q_role = member.guild.get_role(self.quarantine_role_id)
        if not q_role:
            print(f"⚠️ Quarantine role {self.quarantine_role_id} not found in guild {member.guild.name}.")
            return False

        uid = str(member.id)
        now = datetime.now()

        async with self.quarantine_lock:
            q_data = load_json_data(QUARANTINE_FILE)
            already_quarantined = any(r.id == self.quarantine_role_id for r in member.roles)
            if (already_quarantined or uid in q_data) and not is_evasion:
                print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Ignored quarantine request for {member.name}: already in quarantine.")
                return False

            if uid not in q_data:
                roles_to_save = [
                    role.id for role in member.roles
                    if role.id != member.guild.id
                    and role.id not in self.category_role_ids
                    and not role.managed
                    and role.id != self.quarantine_role_id
                ]
                effective_type = quarantine_type or ("automod" if is_automod else ("evasion" if is_evasion else "account_age"))
                q_data[uid] = {
                    "roles": roles_to_save,
                    "quarantined_at": now.isoformat(),
                    "reason": reason,
                    "type": effective_type,
                }
                save_json_data(QUARANTINE_FILE, q_data)
            else:
                effective_type = q_data[uid].get("type", "account_age") if isinstance(q_data[uid], dict) else "account_age"

        # Clear prison data if present to prioritize strict quarantine
        async with self.prison_lock:
            p_data = load_json_data(PRISON_FILE)
            if uid in p_data:
                del p_data[uid]
                save_json_data(PRISON_FILE, p_data)
                print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Cleared prison data for {member.name} to enforce strict quarantine.")

        new_roles = [role for role in member.roles if role.id in self.category_role_ids]
        if q_role not in new_roles:
            new_roles.append(q_role)

        try:
            await member.edit(roles=new_roles, reason=reason)
        except discord.Forbidden:
            print(f"⚠️ Could not assign Quarantine role to {member.name}: Bot lacks permissions.")
            return False

        quarantine_channel = self.get_quarantine_channel(member.guild)
        log_channel = self.get_quarantine_logs_channel(member.guild)
        view = discord.ui.View(timeout=None)
        view.add_item(QuarantineReleaseButton(member.id))

        if is_automod or effective_type == "automod":
            details = automod_details or {}
            rule_name = details.get("rule_name", "AutoMod Rule")
            keyword = details.get("matched_keyword")
            ch_id = details.get("channel_id")
            source_ch = member.guild.get_channel(ch_id) if ch_id else None
            joined_days = (discord.utils.utcnow() - member.joined_at).days if member.joined_at else 0

            user_msg = (
                f"🔒 Welcome {member.mention}. You have been automatically placed in quarantine by Gate Greeter "
                f"because as a new server member (**joined {joined_days} day(s) ago**; monitoring period is **{self.quarantine_new_member_days} days**), "
                f"you were flagged by AutoMod for violating server rules (**{rule_name}**).\n\n"
                f"A staff member will review your case and release you into the wild shortly."
            )

            log_msg = (
                f"🔒 **AutoMod Quarantine:** {member.mention} (`{member.name}`, ID: `{member.id}`) was automatically quarantined.\n"
                f"- **Server Membership:** {joined_days} day(s)\n"
                f"- **Rule Triggered:** {rule_name}"
            )
            if keyword:
                log_msg += f"\n- **Trigger Keyword:** ||{keyword}||"
            if source_ch:
                log_msg += f"\n- **Channel:** {source_ch.mention}"

        elif is_evasion or effective_type == "evasion":
            user_msg = (
                f"🚨 **Quarantine Evasion Attempted!** {member.mention}, you tried to rejoin the server while quarantined, "
                f"but Gate Greeter immediately dragged you back to quarantine.\n\n"
                f"Please wait for staff review."
            )
            log_msg = f"🚨 **Quarantine Evasion Detected:** {member.mention} (`{member.name}`) attempted to evade quarantine by rejoining."

        elif effective_type == "manual":
            by_str = f" by **{quarantined_by.display_name}**" if quarantined_by else ""
            user_msg = (
                f"🔒 {member.mention}, you have been placed in quarantine.\n\n"
                f"Please wait here while staff reviews your case before you can be released into the wild."
            )
            log_msg = f"🔒 **Manual Quarantine:** {member.mention} (`{member.name}`, ID: `{member.id}`) was quarantined{by_str}."

        else:
            account_age = discord.utils.utcnow() - member.created_at
            user_msg = (
                f"🔒 Welcome {member.mention}. You have been automatically placed in quarantine by Gate Greeter "
                f"because your Discord account is too new (**{account_age.days} day(s) old**; minimum required is **{self.quarantine_days} days**).\n\n"
                f"This is an automated security measure to protect our community against raids and spam accounts. "
                f"A staff member will review your account and release you into the wild shortly."
            )
            log_msg = (
                f"🔒 **Auto-Quarantine:** {member.mention} (`{member.name}`) has been quarantined upon joining. "
                f"Account age: **{account_age.days} days**."
            )

        if quarantine_channel:
            try:
                await quarantine_channel.send(user_msg, view=view)
            except discord.Forbidden:
                print(f"⚠️ Lacking permissions to send message in quarantine channel {quarantine_channel.name}.")

        if log_channel:
            try:
                await log_channel.send(log_msg)
            except discord.Forbidden:
                print(f"⚠️ Lacking permissions to send message in quarantine logs channel {log_channel.name}.")

        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Quarantined {member.name}. Reason: {reason}")
        return True

    async def release_quarantined_member(
        self,
        member: discord.Member,
        released_by: discord.Member | discord.User,
        channel: discord.abc.Messageable | None = None,
        reply_to: discord.Message | None = None,
    ) -> tuple[bool, str]:
        """Releases a member from quarantine and restores any roles they previously had."""
        q_role = member.guild.get_role(self.quarantine_role_id)
        is_quarantined = (q_role in member.roles) if q_role else False
        uid = str(member.id)
        now = datetime.now()

        async with self.quarantine_lock:
            q_data = load_json_data(QUARANTINE_FILE)
            in_q_data = uid in q_data
            if not is_quarantined and not in_q_data:
                return False, f"{member.mention} is not currently in quarantine."

            saved_entry = q_data.pop(uid, None)
            save_json_data(QUARANTINE_FILE, q_data)

        if isinstance(saved_entry, dict):
            q_type = saved_entry.get("type", "account_age")
            saved_role_ids = saved_entry.get("roles", [])
        elif isinstance(saved_entry, list):
            q_type = "account_age"
            saved_role_ids = saved_entry
        else:
            q_type = "account_age"
            saved_role_ids = []

        roles_to_restore = [
            role for role in member.roles
            if (role.id in self.category_role_ids or role.id == member.guild.id)
            and role.id != self.quarantine_role_id
        ]

        # Only restore saved roles for members quarantined via AutoMod or manually,
        # NOT for accounts quarantined on join whose saved roles were just incomplete onboarding clicks.
        if q_type != "account_age":
            bot_top_role = member.guild.me.top_role
            for role_id in saved_role_ids:
                role = member.guild.get_role(role_id)
                if role and role not in roles_to_restore and role.id != self.quarantine_role_id:
                    if role < bot_top_role:
                        roles_to_restore.append(role)
                    else:
                        print(f"⚠️ Role {role.name} ({role.id}) is higher than bot's highest role, skipping restoration.")

        try:
            await member.edit(roles=roles_to_restore, reason=f"Released from quarantine into the wild by {released_by.name}")
        except discord.Forbidden:
            return False, f"Could not restore roles for {member.mention}: Bot lacks permissions or hierarchy is too low."

        # Send direct message to the released member
        dm_sent = await self.send_release_dm(member, released_by.display_name, q_type)

        quarantine_channel = self.get_quarantine_channel(member.guild)
        log_channel = self.get_quarantine_logs_channel(member.guild)
        release_text = f"🌿 {member.mention} has been released into the wild!"

        if reply_to:
            try:
                await reply_to.reply(release_text)
            except Exception:
                if quarantine_channel:
                    try:
                        await quarantine_channel.send(release_text)
                    except discord.Forbidden:
                        pass
        elif quarantine_channel:
            try:
                await quarantine_channel.send(release_text)
            except discord.Forbidden:
                pass

        if channel and (not quarantine_channel or channel.id != quarantine_channel.id):
            if not reply_to or channel.id != reply_to.channel.id:
                try:
                    await channel.send(release_text)
                except discord.Forbidden:
                    pass

        if log_channel:
            dm_status = "" if dm_sent else " *(DM could not be delivered: member has DMs closed)*"
            log_text = f"🌿 **Quarantine Release:** {member.mention} (`{member.name}`) has been released into the wild by **{released_by.display_name}**.{dm_status}"
            if not quarantine_channel or log_channel.id != quarantine_channel.id:
                try:
                    await log_channel.send(log_text)
                except discord.Forbidden:
                    pass

        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] 🌿 {member.name} released into the wild by {released_by.name}. DM delivered: {dm_sent}")
        return True, release_text

    # ---------------------------------------------------------
    # COMMANDS: RELEASE & QUARANTINE
    # ---------------------------------------------------------
    @commands.command(name="release", aliases=["unquarantine"])
    @commands.has_permissions(manage_roles=True)
    async def release(self, ctx, member: discord.Member):
        if not is_env_enabled("ENABLE_MODERATION"):
            return

        uid = str(member.id)
        is_quarantined = any(r.id == self.quarantine_role_id for r in member.roles)

        async with self.quarantine_lock:
            q_data = load_json_data(QUARANTINE_FILE)
            in_q_data = uid in q_data

        async with self.prison_lock:
            p_data = load_json_data(PRISON_FILE)
            is_inmate = uid in p_data or any(r.id == self.inmate_role_id for r in member.roles)

        # 1. Quarantine Release
        if is_quarantined or in_q_data:
            success, msg = await self.release_quarantined_member(member, released_by=ctx.author, channel=ctx.channel)
            if not success:
                await ctx.send(f"❌ {msg}")
            return

        # 2. Prison Island Release
        if is_inmate:
            if not is_env_enabled("ENABLE_CMD_PRISON"):
                return await ctx.send("🚫 Prison command is disabled.")

            async with self.prison_lock:
                p_data = load_json_data(PRISON_FILE)
                if uid not in p_data:
                    return await ctx.send(f"❌ {member.mention} has no saved roles in prison.")
                saved_role_ids = p_data.pop(uid)
                save_json_data(PRISON_FILE, p_data)

            roles_to_restore = [ctx.guild.default_role]
            for role in member.roles:
                if role.id in self.category_role_ids and role.id != self.inmate_role_id:
                    roles_to_restore.append(role)

            bot_top_role = ctx.guild.me.top_role
            for role_id in saved_role_ids:
                role = ctx.guild.get_role(role_id)
                if role and role not in roles_to_restore and role.id != self.inmate_role_id:
                    if role < bot_top_role:
                        roles_to_restore.append(role)

            await member.edit(roles=roles_to_restore, reason="Released from Rada's Prison Island")
            await ctx.send(f"✅ {member.mention} has been released from **Rada’s Prison Island**.")
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ {member.name} has been released from Rada’s Prison Island.")
            return

        await ctx.send(f"❌ {member.mention} is neither in prison nor in quarantine.")

    @commands.command(name="quarantine")
    @commands.has_permissions(manage_roles=True)
    async def manual_quarantine(self, ctx, member: discord.Member, *, reason: str = "Manually quarantined by staff."):
        if not is_env_enabled("ENABLE_MODERATION"):
            return

        if any(role.id in self.staff_role_ids for role in member.roles):
            return await ctx.send(f"🚫 Cannot quarantine staff member {member.mention}.")

        success = await self.quarantine_member(
            member,
            reason=reason,
            quarantine_type="manual",
            quarantined_by=ctx.author,
        )
        if success:
            quarantine_channel = self.get_quarantine_channel(ctx.guild)
            if not quarantine_channel or ctx.channel.id != quarantine_channel.id:
                await ctx.send(f"🔒 **{member.display_name}** has been dragged to quarantine.")
        else:
            await ctx.send(f"⚠️ Could not quarantine {member.mention}.")

    # ---------------------------------------------------------
    # ONBOARDING, EVASION PREVENTION & QUARANTINE
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member):
        await asyncio.sleep(3)
        member = member.guild.get_member(member.id)
        if not member:
            return

        account_age = discord.utils.utcnow() - member.created_at
        needs_quarantine = account_age.days <= self.quarantine_days
        uid = str(member.id)

        async with self.quarantine_lock:
            q_data = load_json_data(QUARANTINE_FILE)
            was_quarantined = uid in q_data

        async with self.prison_lock:
            p_data = load_json_data(PRISON_FILE)
            is_inmate = uid in p_data

        # 1. Auto-Quarantine (Account too new)
        if needs_quarantine:
            await self.quarantine_member(
                member=member,
                reason=f"Account auto-quarantined. Age: {account_age.days} days.",
                is_automod=False,
            )

        # 2. Quarantine Evasion Prevention
        elif was_quarantined:
            await self.quarantine_member(
                member=member,
                reason="Quarantine evasion attempted. Re-quarantined upon rejoining.",
                is_automod=False,
                is_evasion=True,
            )

        # 3. Standard Prison Lockdown
        elif is_inmate:
            new_roles = [role for role in member.roles if role.id in self.category_role_ids]
            inmate_role = member.guild.get_role(self.inmate_role_id)
            if inmate_role:
                new_roles.append(inmate_role)

            try:
                await member.edit(roles=new_roles, reason="Fleeing inmate rejoined. Sent back to prison.")
                general_channel = member.guild.get_channel(self.general_channel_id)
                if general_channel:
                    await general_channel.send(
                        f"🚨 **Prison Evasion Attempted!** {member.mention} tried to flee the server and rejoin to clear their roles, "
                        f"but Gate Greeter immediately dragged them back to **Rada’s Prison Island**."
                    )
            except discord.Forbidden:
                print(f"⚠️ Could not lock down {member.name}: Bot lacks permissions.")

    # ---------------------------------------------------------
    # AUTOMOD MONITORING (New Member Auto-Quarantine)
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction):
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ⚡ AutoMod event received! Guild ID: {execution.guild_id}, User ID: {execution.user_id}, Rule ID: {execution.rule_id}, Channel ID: {execution.channel_id}")

        guild = self.bot.get_guild(execution.guild_id) or execution.guild
        if not guild:
            print(f"⚠️ AutoMod: Could not resolve guild for ID {execution.guild_id}.")
            return

        quarantine_channel = self.get_quarantine_channel(guild)
        # 1. Do not re-quarantine if violation occurred inside quarantine channel
        if quarantine_channel and execution.channel_id == quarantine_channel.id:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] AutoMod: Ignored violation inside quarantine channel for user ID {execution.user_id}.")
            return

        user_id = execution.user_id

        # 2. Always fetch fresh member object from API to guarantee accurate joined_at and roles
        # (Discord gateway member cache may retain old joined_at or omit it after rejoining)
        member = None
        try:
            member = await guild.fetch_member(user_id)
        except (discord.NotFound, discord.HTTPException) as e:
            member = execution.member or guild.get_member(user_id)
            if not member:
                print(f"⚠️ AutoMod: Could not find or fetch member with user ID {user_id}: {e}")

        if not member:
            return

        if member.bot:
            return

        # 3. Skip staff members
        if any(role.id in self.staff_role_ids for role in member.roles):
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] AutoMod: Ignored staff member {member.name}.")
            return

        # 4. Skip members already having the quarantine role
        if any(role.id == self.quarantine_role_id for role in member.roles):
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] AutoMod: Ignored {member.name} (already has Quarantine role).")
            return

        # 5. Check if the member joined less than self.quarantine_new_member_days ago (default: 7 days)
        if not member.joined_at:
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] AutoMod: Could not resolve join date for {member.name}.")
            return

        join_duration = discord.utils.utcnow() - member.joined_at
        joined_seconds = join_duration.total_seconds()
        joined_days = max(0.0, joined_seconds) / 86400.0

        if joined_days >= self.quarantine_new_member_days:
            print(
                f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] AutoMod: Ignored {member.name} - "
                f"server membership is {joined_days:.1f} days (threshold: {self.quarantine_new_member_days} days)."
            )
            return

        # Attempt to fetch the AutoMod rule name
        rule_name = "AutoMod Rule"
        try:
            rule = await execution.fetch_rule()
            rule_name = rule.name
        except Exception:
            pass

        automod_details = {
            "rule_name": rule_name,
            "matched_keyword": execution.matched_keyword,
            "matched_content": execution.matched_content,
            "channel_id": execution.channel_id,
        }

        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🚨 AutoMod violation ({rule_name}) "
            f"by new member {member.name} (joined {joined_days:.1f}d ago). Quarantining..."
        )
        await self.quarantine_member(
            member=member,
            reason=f"AutoMod triggered ({rule_name}) by new member ({int(joined_days)}d in server).",
            is_automod=True,
            automod_details=automod_details,
        )

    # ---------------------------------------------------------
    # STRICT ROLE ENFORCEMENT (Onboarding Bypass Prevention)
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles == after.roles:
            return

        had_q = any(r.id == self.quarantine_role_id for r in before.roles)
        has_q = any(r.id == self.quarantine_role_id for r in after.roles)

        # 1. Staff manually unquarantined the user by removing the role in Discord UI
        if had_q and not has_q:
            uid = str(after.id)
            async with self.quarantine_lock:
                q_data = load_json_data(QUARANTINE_FILE)
                if uid in q_data:
                    saved_entry = q_data.pop(uid)
                    save_json_data(QUARANTINE_FILE, q_data)
                    q_type = saved_entry.get("type", "account_age") if isinstance(saved_entry, dict) else "account_age"
                    saved_role_ids = saved_entry.get("roles", []) if isinstance(saved_entry, dict) else saved_entry

                    if q_type != "account_age":
                        roles_to_add = [
                            after.guild.get_role(rid) for rid in saved_role_ids
                            if after.guild.get_role(rid) and after.guild.get_role(rid) not in after.roles
                            and after.guild.get_role(rid) < after.guild.me.top_role
                        ]
                        if roles_to_add:
                            try:
                                await after.add_roles(*roles_to_add, reason="Quarantine role manually removed: Restored prior roles.")
                                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Restored prior roles for {after.name} on manual quarantine release.")
                            except discord.Forbidden:
                                pass

                    dm_sent = await self.send_release_dm(after, None, q_type)

                    log_channel = self.get_quarantine_logs_channel(after.guild)
                    if log_channel:
                        dm_status = "" if dm_sent else " *(DM could not be delivered: member has DMs closed)*"
                        try:
                            await log_channel.send(f"🌿 **Quarantine Release:** {after.mention} (`{after.name}`) had Quarantine role removed in UI.{dm_status}")
                        except discord.Forbidden:
                            pass
            return

        is_quarantined = has_q
        is_inmate = any(r.id == self.inmate_role_id for r in after.roles)

        if not is_quarantined and not is_inmate:
            return

        # 2. Enforce Quarantine (ONLY Quarantine + Category Roles)
        if is_quarantined:
            q_role = after.guild.get_role(self.quarantine_role_id)
            valid_roles = [q_role] if q_role else []
            valid_roles.extend(r for r in after.roles if r.id in self.category_role_ids)

            current_roles = [r for r in after.roles if r.id != after.guild.default_role.id]
            if set(r.id for r in current_roles) != set(r.id for r in valid_roles):
                try:
                    await after.edit(roles=valid_roles, reason="Strict Quarantine Enforcement: Prevented role self-assignment.")
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Stripped unauthorized onboarding roles from quarantined user {after.name}.")
                except discord.Forbidden:
                    pass
            return

        # 3. Enforce Prison (ONLY Inmate + Category Roles)
        if is_inmate:
            inmate_role = after.guild.get_role(self.inmate_role_id)
            valid_roles = [inmate_role] if inmate_role else []
            valid_roles.extend(r for r in after.roles if r.id in self.category_role_ids)

            current_roles = [r for r in after.roles if r.id != after.guild.default_role.id]
            if set(r.id for r in current_roles) != set(r.id for r in valid_roles):
                try:
                    await after.edit(roles=valid_roles, reason="Strict Prison Enforcement: Prevented role self-assignment.")
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Stripped unauthorized onboarding roles from imprisoned user {after.name}.")
                except discord.Forbidden:
                    pass

    # ---------------------------------------------------------
    # TRUST SYSTEM (Anti-Raid Media Permissions)
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        # 1. Ignore users who already have Trusted Media, OR are currently in Prison / Quarantine
        restricted_or_trusted_ids = {
            self.trusted_role_id,
            self.inmate_role_id,
            self.quarantine_role_id,
        }
        if any(role.id in restricted_or_trusted_ids for role in message.author.roles):
            return

        # 2. Check the anti-spam cooldown buffer
        now = datetime.now()
        last_interaction = self.trust_cooldowns.get(message.author.id)

        if last_interaction and (now - last_interaction).total_seconds() < self.trust_cooldown_seconds:
            return

        self.trust_cooldowns[message.author.id] = now

        # 3. Safely lock file operations to prevent race conditions
        async with self.trust_lock:
            trust_data = load_json_data(TRUST_FILE)
            uid = str(message.author.id)
            current_points = trust_data.get(uid, 0) + 1

            # 4. Check if they reached the threshold
            if current_points >= self.trust_threshold:
                trusted_role = message.guild.get_role(self.trusted_role_id)
                if trusted_role:
                    try:
                        await message.author.add_roles(
                            trusted_role,
                            reason="Reached interaction threshold for media permissions.",
                        )

                        trust_data.pop(uid, None)
                        save_json_data(TRUST_FILE, trust_data)

                        try:
                            await message.author.send(
                                f"🎉 **Access Granted!** Thanks for chatting in **{message.guild.name}**. "
                                f"You've interacted enough to unlock media permissions—you can now attach files and embed links!"
                            )
                        except discord.Forbidden:
                            pass

                        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Awarded Trusted Media role to {message.author.name}")
                    except discord.Forbidden:
                        print(f"⚠️ Could not assign Trusted Media role to {message.author.name}: Bot lacks permissions.")
            else:
                trust_data[uid] = current_points
                save_json_data(TRUST_FILE, trust_data)

    # ---------------------------------------------------------
    # ANTI-NUKE SYSTEM (Channel Deletion Limits)
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        guild = channel.guild
        await asyncio.sleep(2)

        deleter = None
        async for entry in guild.audit_logs(limit=1, action=discord.AuditLogAction.channel_delete):
            if entry.target.id == channel.id:
                deleter = entry.user
                break

        if not deleter:
            return

        user_id = deleter.id
        now = datetime.now()

        if user_id not in self.channel_deletions:
            self.channel_deletions[user_id] = []

        self.channel_deletions[user_id].append(now)

        recent_deletions = [
            t for t in self.channel_deletions[user_id]
            if (now - t).total_seconds() <= self.anti_nuke_window
        ]
        self.channel_deletions[user_id] = recent_deletions

        if len(recent_deletions) >= self.anti_nuke_limit:
            member = guild.get_member(user_id)
            if member:
                del self.channel_deletions[user_id]
                mods_chat = guild.get_channel(self.mods_chat_id)

                if member.bot:
                    try:
                        await member.kick(reason=f"Anti-Nuke: Rogue bot deleted {self.anti_nuke_limit} channels within {self.anti_nuke_window // 60} minutes.")
                        if mods_chat:
                            await mods_chat.send(
                                f"🚨 **ANTI-NUKE LOCKDOWN TRIGGERED!** 🚨\n"
                                f"The bot {member.mention} just deleted {self.anti_nuke_limit} channels within a {self.anti_nuke_window // 60}-minute window. "
                                f"Gate Greeter has **kicked** the bot from the server to stop the attack."
                            )
                        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Anti-nuke: Kicked rogue bot {member.name}.")
                    except discord.Forbidden:
                        print(f"⚠️ Anti-nuke failed to kick bot {member.name}: Gate Greeter's role is too low.")
                        if mods_chat:
                            await mods_chat.send(f"⚠️ **URGENT:** The bot {member.mention} is deleting channels, but my role is too low to kick it!")
                else:
                    try:
                        await member.edit(roles=[], reason=f"Anti-Nuke: Deleted {self.anti_nuke_limit} channels within {self.anti_nuke_window // 60} minutes.")
                        if mods_chat:
                            await mods_chat.send(
                                f"🚨 **ANTI-NUKE LOCKDOWN TRIGGERED!** 🚨\n"
                                f"The user {member.mention} just deleted {self.anti_nuke_limit} channels within a {self.anti_nuke_window // 60}-minute window. "
                                f"Gate Greeter has **stripped all of their roles** to prevent further damage."
                            )
                        print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Anti-nuke: Stripped all roles from {member.name}.")
                    except discord.Forbidden:
                        print(f"⚠️ Anti-nuke failed to strip {member.name}: Gate Greeter's role is too low.")
                        if mods_chat:
                            await mods_chat.send(f"⚠️ **URGENT:** The user {member.mention} is deleting channels, but my role is too low to strip their permissions!")


async def setup(bot):
    await bot.add_cog(Moderation(bot))