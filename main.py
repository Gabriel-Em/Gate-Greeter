import os
import sys
from dotenv import load_dotenv
import discord
from discord.ext import commands

from utils import get_env_int, is_env_enabled

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

# Disable default help so we can register our own custom embed help command
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)


async def load_extensions():
    """Loads cogs based on .env configuration."""
    if is_env_enabled("ENABLE_AUTO_RESPONDER"):
        await bot.load_extension("cogs.auto_responder")
        print("Loaded module: Auto Responder")

    if is_env_enabled("ENABLE_ANALYTICS"):
        await bot.load_extension("cogs.analytics")
        print("Loaded module: Analytics")

    if is_env_enabled("ENABLE_MODERATION"):
        await bot.load_extension("cogs.moderation")
        print("Loaded module: Moderation")

    if is_env_enabled("ENABLE_BACKUP"):
        await bot.load_extension("cogs.backup")
        print("Loaded module: Backup")


@bot.event
async def setup_hook():
    await load_extensions()


@bot.event
async def on_ready():
    print(f"Logged in successfully as {bot.user}")


# ---------------------------------------------------------
# Global Command Error Handling
# ---------------------------------------------------------
@bot.event
async def on_command_error(ctx, error):
    # 1. User ran a command that doesn't exist (e.g., !top)
    if isinstance(error, commands.CommandNotFound):
        await ctx.send(f"⚠️ Unknown command `{ctx.invoked_with}`. Type `!help` to see available commands.")
        return

    # 2. Target member could not be found (e.g., !prison / !release)
    if isinstance(error, commands.MemberNotFound):
        await ctx.send(f"⚠️ Could not find member `{error.argument}` in this server.")
        return

    # 3. User passed an invalid argument type (e.g., string instead of int)
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"⚠️ Invalid argument provided: {error}. Type `!help` for correct usage.")
        return

    # 4. User missed a required argument
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Missing required argument: `{error.param.name}`. Type `!help` for correct usage.")
        return

    # 5. Permission errors
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 You don't have permission to use this command.")
        return

    # 6. Catch internal code crashes (like UnboundLocalError or Forbidden) and send to Discord
    if isinstance(error, commands.CommandInvokeError):
        original_error = error.original
        print(f"[Unhandled Error] in command {ctx.command}: {original_error}")
        await ctx.send(f"❌ **Internal Code Error:** `{type(original_error).__name__}: {original_error}`")
        return

    # Fallback: Print uncaught errors to console so you can still debug them
    print(f"[Unhandled Error] in command {ctx.command}: {error}")
    await ctx.send(f"❌ **Unexpected Error:** `{error}`")


# ---------------------------------------------------------
# Custom Help Command
# ---------------------------------------------------------
@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="🤖 Server Bot Commands",
        description="Here are the commands available on the server and their current status:",
        color=discord.Color.blue(),
    )

    analytics_enabled = is_env_enabled("ENABLE_ANALYTICS")
    moderation_enabled = is_env_enabled("ENABLE_MODERATION")
    backup_enabled = is_env_enabled("ENABLE_BACKUP")
    prison_active = moderation_enabled and is_env_enabled("ENABLE_CMD_PRISON")
    snapshot_active = backup_enabled and is_env_enabled("ENABLE_CMD_SNAPSHOT")
    active_msg_threshold = get_env_int("PRONOUN_ACTIVE_MSG_THRESHOLD", 50)

    commands_info = [
        {
            "name": "!topusers [year] <month>",
            "desc": "Shows top 10 most active members for a given year or specific month.",
            "active": analytics_enabled and is_env_enabled("ENABLE_CMD_TOPUSERS"),
        },
        {
            "name": "!activity30",
            "desc": "Generates 30-day hourly and daily server-wide message graphs.",
            "active": analytics_enabled and is_env_enabled("ENABLE_CMD_ACTIVITY30"),
        },
        {
            "name": "!archive",
            "desc": "Deep scans and archives message counts for the past 12 months.",
            "active": analytics_enabled and is_env_enabled("ENABLE_CMD_ARCHIVE"),
        },
        {
            "name": "!pronounstats <year>",
            "desc": f"Graphs demographics for active users ({active_msg_threshold}+ messages/month) by pronoun roles.",
            "active": analytics_enabled and is_env_enabled("ENABLE_CMD_PRONOUNSTATS"),
        },
        {
            "name": "!prison @user",
            "desc": "Strips a user's roles (except categories), saves them, and sends them to Prison Island.",
            "active": prison_active,
        },
        {
            "name": "!release @user",
            "desc": "Releases an inmate and restores all their previous roles.",
            "active": prison_active,
        },
        {
            "name": "!snapshot",
            "desc": "Exports a full server structure backup (roles, channels, permissions) in JSON and Markdown (Admin only).",
            "active": snapshot_active,
        },
        {
            "name": "!restart",
            "desc": "Triggers a container reboot and pulls latest code (Admin only).",
            "active": True,
        },
    ]

    for cmd in commands_info:
        status_tag = "🟢 **Active**" if cmd["active"] else "🔴 **Disabled**"
        embed.add_field(
            name=f"`{cmd['name']}` — {status_tag}",
            value=cmd["desc"],
            inline=False,
        )

    embed.set_footer(text="Parameters in [brackets] are required, <brackets> are optional.")
    await ctx.send(embed=embed)


# ---------------------------------------------------------
# Admin Restart Command
# ---------------------------------------------------------
@bot.command(name="restart")
@commands.has_permissions(administrator=True)
async def restart(ctx):
    await ctx.send("🔄 Shutting down... Container will reboot and pull changes in a moment.")
    await bot.close()
    sys.exit(1)


if __name__ == "__main__":
    bot.run(os.environ.get("BOT_TOKEN"))
