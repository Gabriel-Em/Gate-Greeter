import discord
from discord.ext import commands
import re
import os
import random
from datetime import datetime

class AutoResponder(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.greeted_file = "greeted_users.txt"
        self.greeted_users = set()

        if os.path.exists(self.greeted_file):
            with open(self.greeted_file, "r") as f:
                for line in f:
                    if line.strip():
                        self.greeted_users.add(int(line.strip()))
        else:
            with open(self.greeted_file, "w") as f:
                pass

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        # 1. VO2 Max Check
        if os.getenv("ENABLE_RESP_VO2MAX", "True").lower() in ("true", "1", "t"):
            vo2_pattern = r"what('s|\s+is|s)?\s+vo2\s*max"
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
        if os.getenv("ENABLE_RESP_ALEX", "True").lower() in ("true", "1", "t"):
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

        # 3. One-Time Greeting
        if os.getenv("ENABLE_RESP_GREETING", "True").lower() in ("true", "1", "t"):
            greet_pattern = r'^(hi|hey|hello|bonjour)[\W]*$'
            if re.match(greet_pattern, message.content, re.IGNORECASE):
                user_id = message.author.id

                if user_id not in self.greeted_users:
                    responses = [
                        "You should do cardio and VO2 max.",
                        "Hi, what's your favorite type of bread?"
                    ]
                    await message.reply(random.choice(responses))
                    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Greeted {message.author} in #{message.channel}")
                    
                    self.greeted_users.add(user_id)
                    with open(self.greeted_file, "a") as f:
                        f.write(f"{user_id}\n")

async def setup(bot):
    await bot.add_cog(AutoResponder(bot))