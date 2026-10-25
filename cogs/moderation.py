import discord
from discord.ext import commands
import os
import json
import asyncio
from datetime import datetime

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Format: {member_id: {"time": datetime_object, "channel_id": int}}
        self.voice_join_times = {}

        # Anti-Spam Buffer: {member_id: datetime_object}
        self.unc_cooldowns = {}

        # Prison System Configuration
        self.prison_file = "prison_data.json"
        self.inmate_role_id = 1351480622497009685
        self.staff_role_ids = [818263287716249601, 1236070091431608320, 1352345807604551761]
        self.category_role_ids = [
            1562090532002332754, 1562089458168242258, 1562090734713176145, 
            1562090652899090464, 1562092542227648542, 1562092666706071644, 
            1562092243928481882, 1562097073266888824
        ]

        # Initialize the prison ledger if it doesn't exist
        if not os.path.exists(self.prison_file):
            with open(self.prison_file, "w") as f:
                json.dump({}, f)

        # Unc System Configuration
        self.unc_file = "unc_data.json"
        
        if not os.path.exists(self.unc_file):
            with open(self.unc_file, "w") as f:
                json.dump({}, f)

    def load_unc_data(self):
        try:
            with open(self.unc_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def save_unc_data(self, data):
        with open(self.unc_file, "w") as f:
            json.dump(data, f, indent=4)

    # ---------------------------------------------------------
    # VOICE MISCLICK TRACKER (The Unc Dynasty)
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        if before.channel == after.channel: return

        now = datetime.now()

        # Event: Left a channel
        if before.channel is not None:
            record = self.voice_join_times.get(member.id)
            if record and record["channel_id"] == before.channel.id:
                duration = (now - record["time"]).total_seconds()
                
                # Check for misclick (< 3s)
                if duration < 3.0:
                    # --- ANTI-SPAM COOLDOWN CHECK ---
                    last_misclick = self.unc_cooldowns.get(member.id)
                    # 3600 seconds = 1 hour cooldown. Adjust this number if you want a longer/shorter buffer.
                    if last_misclick and (now - last_misclick).total_seconds() < 3600:
                        if member.id in self.voice_join_times:
                            del self.voice_join_times[member.id]
                        return # Silently ignore the farming attempt

                    # Record the time of this valid misclick to start their cooldown timer
                    self.unc_cooldowns[member.id] = now
                    # --------------------------------

                    # 1. Load data and increment their count
                    unc_data = self.load_unc_data()
                    uid = str(member.id)
                    current_count = unc_data.get(uid, 0) + 1
                    unc_data[uid] = current_count
                    self.save_unc_data(unc_data)

                    # 2. Determine their level and target role
                    if current_count < 5:
                        target_role_name = "Unc"
                    elif current_count < 10:
                        target_role_name = "Grand Unc"
                    elif current_count < 15:
                        target_role_name = "Great-Grand Unc"
                    elif current_count < 20:
                        target_role_name = "Ancestral Unc"
                    else:
                        target_role_name = "The First Unc"

                    # Fetch the role from the server (case-sensitive to match Discord exactly)
                    target_role = discord.utils.get(member.guild.roles, name=target_role_name)
                    
                    # 3. Apply the role and announce
                    if target_role:
                        try:
                            # Strip any old unc roles from the progression tree to prevent duplicates
                            dynasty_roles = ["Unc", "Grand Unc", "Great-Grand Unc", "Ancestral Unc", "The First Unc"]
                            old_roles = [r for r in member.roles if r.name in dynasty_roles and r.id != target_role.id]
                            
                            if old_roles:
                                await member.remove_roles(*old_roles)
                                
                            if target_role not in member.roles:
                                await member.add_roles(target_role)

                            general_channel = discord.utils.get(member.guild.text_channels, name="general")
                            if general_channel:
                                # Custom messages based on milestones
                                if current_count == 1:
                                    msg = f"👴 {member.mention} just earned the **{target_role_name}** role for missclicking into the `{before.channel.name}` voice channel!"
                                elif current_count == 5:
                                    msg = f"🌟 **LEVEL UP!** {member.mention} misclicked for the 5th time and evolved into a **{target_role_name}**!"
                                elif current_count == 10:
                                    msg = f"📜 **LEVEL UP!** {member.mention} hit 10 misclicks and aged into a **{target_role_name}**!"
                                elif current_count == 15:
                                    msg = f"🏺 **LEVEL UP!** {member.mention} reached 15 misclicks and is now recognized as an **{target_role_name}**!"
                                elif current_count == 20:
                                    msg = f"👑 **MAX LEVEL!** {member.mention} achieved 20 misclicks and has ascended to **{target_role_name}**!"
                                else:
                                    msg = f"👴 {member.mention} just misclicked into `{before.channel.name}`! (Total misclicks: {current_count})"

                                await general_channel.send(msg)
                                log_msg = msg.replace(member.mention, member.name)
                                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {log_msg}")

                        except discord.Forbidden:
                            print(f"⚠️ Could not manage unc roles for {member.name}: The bot lacks permissions or the role hierarchy is incorrect.")
                
                # Clean up memory
                if member.id in self.voice_join_times:
                    del self.voice_join_times[member.id]

        # Event: Joined a channel
        if after.channel is not None:
            self.voice_join_times[member.id] = {
                "time": now,
                "channel_id": after.channel.id
            }

    def load_prison_data(self):
        try:
            with open(self.prison_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            # If the file is corrupted or missing, return an empty ledger
            return {}

    def save_prison_data(self, data):
        with open(self.prison_file, "w") as f:
            json.dump(data, f, indent=4)

    # ---------------------------------------------------------
    # PRISON COMMANDS
    # ---------------------------------------------------------
    @commands.command(name="prison")
    @commands.has_permissions(manage_roles=True)
    async def prison(self, ctx, member: discord.Member):
        if os.getenv("ENABLE_CMD_PRISON", "True").lower() not in ("true", "1", "t"):
            return

        # 1. Check if the target is staff
        if any(role.id in self.staff_role_ids for role in member.roles):
            return await ctx.send(f"🚫 Cannot imprison staff member {member.mention}.")

        data = self.load_prison_data()
        user_id_str = str(member.id)

        if user_id_str in data:
            return await ctx.send(f"⚠️ {member.mention} is already in prison.")

        # 2. Save current roles (ignoring @everyone, categories, and managed/Nitro roles)
        roles_to_save = [
            role.id for role in member.roles 
            if role.id != ctx.guild.id and role.id not in self.category_role_ids and not role.managed
        ]
        
        data[user_id_str] = roles_to_save
        self.save_prison_data(data)

        # 3. Determine their new roles (Keep @everyone + Categories, add Inmate)
        new_roles = []
        for role in member.roles:
            if role.id in self.category_role_ids:
                new_roles.append(role)
                
        inmate_role = ctx.guild.get_role(self.inmate_role_id)
        if inmate_role:
            new_roles.append(inmate_role)

        # Apply the changes all at once
        await member.edit(roles=new_roles, reason="Sent to Rada's Prison Island")
        await ctx.send(f"🔒 {member.mention} has been sent to **Rada’s Prison Island**.")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 🔒 {member.name} has been sent to Rada’s Prison Island.")

    @commands.command(name="release")
    @commands.has_permissions(manage_roles=True)
    async def release(self, ctx, member: discord.Member):
        if os.getenv("ENABLE_CMD_PRISON", "True").lower() not in ("true", "1", "t"):
            return

        data = self.load_prison_data()
        user_id_str = str(member.id)

        if user_id_str not in data:
            return await ctx.send(f"❌ {member.mention} is not in prison.")

        # 1. Retrieve and remove them from the ledger
        saved_role_ids = data.pop(user_id_str)
        self.save_prison_data(data)

        # 2. Reconstruct their roles (Keep @everyone + Categories, restore Saved Roles)
        roles_to_restore = [ctx.guild.default_role]
        
        for role in member.roles:
            if role.id in self.category_role_ids:
                roles_to_restore.append(role)

        for role_id in saved_role_ids:
            role = ctx.guild.get_role(role_id)
            # Only add the role back if it still exists and isn't already in the list
            if role and role not in roles_to_restore:
                roles_to_restore.append(role)

        await member.edit(roles=roles_to_restore, reason="Released from Rada's Prison Island")
        await ctx.send(f"✅ {member.mention} has been released from **Rada’s Prison Island**.")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ✅ {member.name} has been released from Rada’s Prison Island.")

    # ---------------------------------------------------------
    # EVASION PREVENTION (Rejoining Inmates)
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member):
        data = self.load_prison_data()
        
        if str(member.id) in data:
            # Wait 3 seconds to let Discord Onboarding and Circle Bot finish giving them roles
            await asyncio.sleep(3)
            
            # Fetch the freshest version of the member to get the roles they just acquired
            member = member.guild.get_member(member.id)
            if not member: 
                return

            # Re-apply the prison lockdown
            new_roles = []
            for role in member.roles:
                if role.id in self.category_role_ids:
                    new_roles.append(role)
                    
            inmate_role = member.guild.get_role(self.inmate_role_id)
            if inmate_role:
                new_roles.append(inmate_role)
            
            # Strip unauthorized roles
            await member.edit(roles=new_roles, reason="Fleeing inmate rejoined. Sent back to prison.")

            # Announce the evasion attempt in general chat
            general_channel = discord.utils.get(member.guild.text_channels, name="general")
            if general_channel:
                await general_channel.send(
                    f"🚨 **Prison Evasion Attempted!** {member.mention} tried to flee the server and rejoin to clear their roles, "
                    f"but Gate Greeter immediately dragged them back to **Rada’s Prison Island**."
                )
                print(f"Successfully announced prison evasion for {member.name}.")
                

async def setup(bot):
    await bot.add_cog(Moderation(bot))