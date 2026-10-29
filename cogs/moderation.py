import os
import asyncio
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
)

# ---------------------------------------------------------
# MODULE-LEVEL CONSTANTS
# ---------------------------------------------------------
PRISON_FILE = "prison_data.json"
UNC_FILE = "unc_data.json"
TRUST_FILE = "trust_data.json"


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

        # Load Channel IDs from Environment
        self.general_channel_id = get_env_int("GENERAL_CHANNEL_ID")
        self.quarantine_logs_channel_id = get_env_int("QUARANTINE_LOGS_CHANNEL_ID")
        self.mods_chat_id = get_env_int("MODS_CHAT_ID")

        # Load Role IDs from Environment
        self.inmate_role_id = get_env_int("INMATE_ROLE_ID")
        self.quarantine_role_id = get_env_int("QUARANTINE_ROLE_ID")
        self.trusted_role_id = get_env_int("TRUSTED_ROLE_ID")

        self.unc_role_ids = get_env_int_list("UNC_ROLE_IDS")
        self.staff_role_ids = get_env_int_list("STAFF_ROLE_IDS")
        self.category_role_ids = get_env_int_list("CATEGORY_ROLE_IDS")

        # Load Thresholds & Timers from Environment
        self.quarantine_days = get_env_int("QUARANTINE_ACCOUNT_AGE_DAYS", 7)
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

    @commands.command(name="release")
    @commands.has_permissions(manage_roles=True)
    async def release(self, ctx, member: discord.Member):
        if os.getenv("ENABLE_CMD_PRISON", "True").lower() not in ("true", "1", "t"):
            return

        user_id_str = str(member.id)

        async with self.prison_lock:
            data = load_json_data(PRISON_FILE)
            if user_id_str not in data:
                return await ctx.send(f"❌ {member.mention} is not in prison.")

            saved_role_ids = data.pop(user_id_str)
            save_json_data(PRISON_FILE, data)

        roles_to_restore = [ctx.guild.default_role]
        for role in member.roles:
            if role.id in self.category_role_ids:
                roles_to_restore.append(role)

        for role_id in saved_role_ids:
            role = ctx.guild.get_role(role_id)
            if role and role not in roles_to_restore:
                roles_to_restore.append(role)

        await member.edit(roles=roles_to_restore, reason="Released from Rada's Prison Island")
        await ctx.send(f"✅ {member.mention} has been released from **Rada’s Prison Island**.")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ {member.name} has been released from Rada’s Prison Island.")

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

        async with self.prison_lock:
            data = load_json_data(PRISON_FILE)
            is_inmate = uid in data

            if needs_quarantine and is_inmate:
                del data[uid]
                save_json_data(PRISON_FILE, data)
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Cleared prison data for {member.name} to enforce strict quarantine.")

        # 1. Quarantine Priority (Overrides Prison)
        if needs_quarantine:
            new_roles = [role for role in member.roles if role.id in self.category_role_ids]
            q_role = member.guild.get_role(self.quarantine_role_id)
            if q_role:
                new_roles.append(q_role)

            try:
                await member.edit(roles=new_roles, reason=f"Account auto-quarantined. Age: {account_age.days} days.")
                log_channel = member.guild.get_channel(self.quarantine_logs_channel_id)
                if log_channel:
                    await log_channel.send(
                        f"🔒 **Auto-Quarantine:** {member.mention} has been quarantined upon joining. "
                        f"Account age: **{account_age.days} days**."
                    )
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Auto-quarantined and role-wiped new account {member.name} (Age: {account_age.days} days).")
            except discord.Forbidden:
                print(f"⚠️ Could not wipe and assign Quarantine role to {member.name}: Bot lacks permissions.")

        # 2. Standard Prison Lockdown
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
    # STRICT ROLE ENFORCEMENT (Onboarding Bypass Prevention)
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles == after.roles:
            return

        is_quarantined = any(r.id == self.quarantine_role_id for r in after.roles)
        is_inmate = any(r.id == self.inmate_role_id for r in after.roles)

        if not is_quarantined and not is_inmate:
            return

        # 1. Enforce Quarantine (ONLY Quarantine + Category Roles)
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

        # 2. Enforce Prison (ONLY Inmate + Category Roles)
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