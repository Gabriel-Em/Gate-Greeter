import discord
from discord.ext import commands
from datetime import datetime

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Format: {member_id: {"time": datetime_object, "channel_id": int}}
        self.voice_join_times = {}

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

async def setup(bot):
    await bot.add_cog(Moderation(bot))