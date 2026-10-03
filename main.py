import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Set up intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents)

async def load_extensions():
    """Loads cogs based on .env configuration."""
    if os.getenv("ENABLE_AUTO_RESPONDER", "True").lower() in ("true", "1", "t"):
        await bot.load_extension("cogs.auto_responder")
        print("Loaded module: Auto Responder")
        
    if os.getenv("ENABLE_ANALYTICS", "True").lower() in ("true", "1", "t"):
        await bot.load_extension("cogs.analytics")
        print("Loaded module: Analytics")
        
    if os.getenv("ENABLE_MODERATION", "True").lower() in ("true", "1", "t"):
        await bot.load_extension("cogs.moderation")
        print("Loaded module: Moderation")

@bot.event
async def setup_hook():
    # setup_hook runs automatically before the bot connects to Discord
    await load_extensions()

@bot.event
async def on_ready():
    print(f'Logged in successfully as {bot.user}')

if __name__ == "__main__":
    bot.run(os.environ.get('BOT_TOKEN'))