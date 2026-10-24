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

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Ignore simple mute/deafen updates within the same channel
        if before.channel == after.channel:
            return

        now = datetime.now()

        # Event: Left a channel
        if before.channel is not None:
            record = self.voice_join_times.get(member.id)
            if record and record["channel_id"] == before.channel.id:
                duration = (now - record["time"]).total_seconds()
                
                # Check for misclick (< 3s)
                if duration < 3.0:
                    unc_role = discord.utils.get(member.guild.roles, name="unc")
                    if unc_role:
                        try:
                            await member.add_roles(unc_role)
                            print(f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] Assigned 'unc' role to {member.name}")
                            
                            general_channel = discord.utils.get(member.guild.text_channels, name="general")
                            if general_channel:
                                await general_channel.send(
                                    f"👴 {member.mention} just earned the **unc** role for misclicking "
                                    f"into the `{before.channel.name}` voice channel!"
                                )
                            else:
                                print("⚠️ Could not find a channel named 'general' to post the announcement.")
                        except discord.Forbidden:
                            print(f"⚠️ Could not assign role to {member.name}: The bot lacks permissions.")
                    else:
                        print("⚠️ Could not find a role named 'unc' in this server.")
                
                # Clean up memory
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
            new_roles = [member.guild.default_role]
            for role in member.roles:
                if role.id in self.category_role_ids:
                    new_roles.append(role)
                    
            inmate_role = member.guild.get_role(self.inmate_role_id)
            if inmate_role:
                new_roles.append(inmate_role)
                
            await member.edit(roles=new_roles, reason="Fleeing inmate rejoined. Sent back to prison.")

async def setup(bot):
    await bot.add_cog(Moderation(bot))