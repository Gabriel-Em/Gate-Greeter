import re
from datetime import datetime
import discord
from discord.ext import commands

from utils import is_env_enabled


class AutoResponder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        # 1. VO2 Max Check
        if is_env_enabled("ENABLE_RESP_VO2MAX"):
            vo2_pattern = r"what\b.*?\bvo2(\s*max)?\b"
            if re.search(vo2_pattern, message.content, re.IGNORECASE):
                explanation = (
                    "VO2 max (maximal oxygen consumption) is the maximum amount of oxygen "
                    "your body can absorb and use during intense exercise. It's one of the best "
                    "indicators of your cardiovascular fitness and endurance!"
                )
                await message.reply(explanation)
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Replied about VO2 max to {message.author} in #{message.channel}")
                return

        # 2. Alex O'Connor Check
        if is_env_enabled("ENABLE_RESP_ALEX"):
            alex_pattern = r"(is.*?alex.*?(in|on).*?server|is.*?this.*?alex.*?server|is.*?alex'?s.*?server)"
            if re.search(alex_pattern, message.content, re.IGNORECASE):
                alex_response = (
                    "Alex does have an account here and gave the server his blessing a long time ago, "
                    "but he doesn't actively use Discord. This is entirely a fan-made server "
                    "(he doesn't have an official one!).\n\n"
                    "**Please note:** The views and opinions expressed by members on this server "
                    "do not reflect Alex's personal views."
                )
                await message.reply(alex_response)
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Replied about Alex O'Connor to {message.author} in #{message.channel}")
                return


async def setup(bot):
    await bot.add_cog(AutoResponder(bot))
