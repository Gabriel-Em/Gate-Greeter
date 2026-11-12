import os
import sys
import asyncio
from dotenv import load_dotenv
import discord
from discord.ext import commands

from utils import get_env_int, is_env_enabled
from database import db

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.auto_moderation_execution = True
intents.auto_moderation_configuration = True

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
    await db.initialize()
    await db.auto_migrate_legacy_json()
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

    if isinstance(error, commands.BotMissingPermissions):
        await ctx.send("🚫 The bot lacks the required administrator permissions to perform this action.")
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
            "name": "!activity [months]",
            "desc": "Generates a monthly activity graph for the past number of months (default 12).",
            "active": analytics_enabled and is_env_enabled("ENABLE_CMD_ACTIVITY"),
        },
        {
            "name": "!pronounstats <year>",
            "desc": f"Graphs demographics for active users ({active_msg_threshold}+ messages/month) by pronoun roles.",
            "active": analytics_enabled and is_env_enabled("ENABLE_CMD_PRONOUNSTATS"),
        },
        {
            "name": "!myemojis",
            "desc": "Shows your personal top 10 most used emojis from the past 30 days.",
            "active": analytics_enabled and is_env_enabled("ENABLE_CMD_MYEMOJIS"),
        },
        {
            "name": "!emojileaderboard",
            "desc": "Server-wide leaderboard of top 15 most used emojis and who used each the most.",
            "active": analytics_enabled and is_env_enabled("ENABLE_CMD_EMOJILEADERBOARD"),
        },
        {
            "name": "!prison @user",
            "desc": "Strips a user's roles (except categories), saves them, and sends them to Prison Island.",
            "active": prison_active,
        },
        {
            "name": "!quarantine @user",
            "desc": "Strips a user's roles (except categories), saves them, and drags them to Quarantine (Staff only).",
            "active": moderation_enabled,
        },
        {
            "name": "!release @user (or !unquarantine @user)",
            "desc": "Releases a quarantined user or prison inmate into the wild and restores their previous roles.",
            "active": moderation_enabled,
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
        {
            "name": "!emojify_channels <revert>",
            "desc": "Edits channel titles with emoji prefixes, or pass 'revert' to restore original names (Admin only).",
            "active": True,
        },
        {
            "name": "!channel_separator <bar|dots>",
            "desc": "Sets ｜ or ┊ between the emoji and channel name (Admin only).",
            "active": True,
        },
        {
            "name": "!revert_channels",
            "desc": "Restores server channel titles back to their original names without emojis (Admin only).",
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


# ---------------------------------------------------------
# Channel Emoji Mapping (Temporary Feature)
# ---------------------------------------------------------
CHANNEL_EMOJI_MAP = {
    # Top Section & Info
    "newcomers": "👋newcomers",
    "welcome": "🤝welcome",
    "rules": "📜rules",
    "announcements": "📢announcements",
    "custom-roles": "🎭custom-roles",
    "partnerships": "🔗partnerships",
    "glossary": "📖glossary",
    "random-questions": "❓random-questions",

    # Community & Discussion
    "veterans-lounge": "☕veterans-lounge",
    "Veterans' discussion": "🎖️ Veterans' discussion",
    "general": "💬general",
    "serious-discussion": "🧐serious-discussion",
    "Lounge 1": "🛋️ Lounge 1",
    "Lounge 2": "🛋️ Lounge 2",
    "history-culture": "🏛️history-culture",
    "philosophy": "🦉philosophy",
    "religion": "🕊️religion",
    "politics": "⚖️politics",
    "veganism": "🌱veganism",
    "science": "🔬science",
    "technology": "💻technology",
    "Discussion 1": "🎙️ Discussion 1",
    "Discussion 2": "🎙️ Discussion 2",

    # Varied Ventures & Media
    "hobbies": "🎨hobbies",
    "food": "🥗food",
    "finance-and-investing": "📈finance-and-investing",
    "health-and-lifestyle": "🧘health-and-lifestyle",
    "animals-nature": "🐾animals-nature",
    "career-advice": "💼career-advice",
    "venting": "😤venting",
    "memes": "😂memes",
    "music-art": "🎵music-art",
    "screens-stories": "📸screens-stories",
    "gaming": "🎮gaming",
    "best-of-server-moments": "⭐best-of-server-moments",
    "Gaming": "🎧 Gaming",

    # Moderation, Bots & Staff
    "prison": "🔒prison",
    "quarantine": "☣️quarantine",
    "bot-commands": "🤖bot-commands",
    "cosmic-videos": "🌌cosmic-videos",
    "cosmic-twitter": "🐦cosmic-twitter",
    "to-do": "📋to-do",
    "mini-mod-chat": "🛡️mini-mod-chat",
    "mods-chat": "🛠️mods-chat",
    "staff-bot-commands": "⚙️staff-bot-commands",
    "Staff Senate": "🏛️ Staff Senate",

    # Server Logs
    "join-leave-logs": "🚪join-leave-logs",
    "warn-logs": "⚠️warn-logs",
    "role-logs": "🏷️role-logs",
    "kick-logs": "🥾kick-logs",
    "message-delete-logs": "🗑️message-delete-logs",
    "ban-logs": "🔨ban-logs",
    "message-edit-logs": "✏️message-edit-logs",
    "bulk-delete-logs": "🧹bulk-delete-logs",
    "admin-mod-logs": "🛡️admin-mod-logs",
    "member-update-logs": "🔄member-update-logs",
    "voice-logs": "🎤voice-logs",
    "quarantine-logs": "☣️quarantine-logs",
}


CHANNEL_SEPARATORS = {"bar": "｜", "dots": "┊"}


def _match_channel_emoji(channel_name: str):
    """
    Matches a channel name to an entry in CHANNEL_EMOJI_MAP.
    Accounts for raw channel names, previous prefix runs (with space or hyphen),
    and target names that are already applied.
    Returns: (matched_key, target_name, is_already_set)
    """
    norm_cname = channel_name.strip().replace("’", "'")
    for key, target_name in CHANNEL_EMOJI_MAP.items():
        norm_key = key.strip().replace("’", "'")
        norm_target = target_name.strip().replace("’", "'")
        prefix = target_name[:-len(key)]
        norm_prefix = prefix.strip()

        # Check if already set to target (or hyphenated for text channels)
        if norm_cname == norm_target or norm_cname == norm_target.replace(" ", "-"):
            return key, target_name, True

        # Check base key
        if norm_cname == norm_key:
            return key, target_name, False

        # Check previous prefix variants (e.g., emoji-channel or emoji channel)
        if norm_cname == f"{norm_prefix}-{norm_key}" or norm_cname == f"{norm_prefix} {norm_key}":
            return key, target_name, False

        # Check separator variants (e.g., emoji｜channel or emoji┊channel)
        for sep in CHANNEL_SEPARATORS.values():
            if norm_cname in (f"{norm_prefix}{sep}{norm_key}", f"{norm_prefix} {sep} {norm_key}"):
                return key, target_name, False

    return None, None, False


@bot.command(
    name="emojify_channels",
    aliases=["update_channel_emojis", "set_channel_emojis", "channelemojis", "emojify"],
)
@commands.guild_only()
@commands.has_permissions(administrator=True)
@commands.bot_has_permissions(administrator=True)
async def emojify_channels(ctx, mode: str = None):
    """
    Edits server channel titles to add emoji prefixes based on CHANNEL_EMOJI_MAP.
    Pass 'revert' as an argument to restore original channel names.
    Requires administrator permissions for both the command invoker and the bot.
    """
    if mode and mode.lower() in ["revert", "undo", "reset", "clean", "remove"]:
        await revert_channels(ctx)
        return

    if not ctx.guild:
        await ctx.send("🚫 This command can only be used in a server.")
        return

    if not ctx.author.guild_permissions.administrator:
        await ctx.send("🚫 You must have Administrator permissions to run this command.")
        return

    if not ctx.guild.me.guild_permissions.administrator:
        await ctx.send("🚫 The bot must have Administrator permissions to run this command.")
        return

    status_msg = await ctx.send(
        f"⏳ Processing channel emoji updates ({len(CHANNEL_EMOJI_MAP)} mapped entries)... Please wait."
    )

    updated = []
    skipped = []
    failed = []
    matched_keys = set()

    for channel in ctx.guild.channels:
        if isinstance(channel, discord.CategoryChannel):
            continue

        matched_key, target_name, is_already_set = _match_channel_emoji(channel.name)
        if not matched_key:
            continue

        matched_keys.add(matched_key)

        if is_already_set:
            skipped.append(f"`{channel.name}` (already set)")
            continue

        try:
            old_name = channel.name
            await channel.edit(name=target_name, reason="Temporary channel emoji update")
            updated.append(f"`{old_name}` ➔ `{target_name}`")
            await asyncio.sleep(1.0)
        except discord.Forbidden:
            failed.append((channel.name, "Forbidden / Missing Permissions"))
        except discord.HTTPException as e:
            failed.append((channel.name, f"HTTP {e.status}: {e.text}"))
        except Exception as e:
            failed.append((channel.name, str(e)))

    not_found = sorted(list(set(CHANNEL_EMOJI_MAP.keys()) - matched_keys))

    embed = discord.Embed(
        title="🏷️ Channel Emojis Update Complete",
        color=discord.Color.green() if not failed else discord.Color.orange(),
    )
    embed.add_field(name="✅ Updated", value=str(len(updated)), inline=True)
    embed.add_field(name="⏭️ Skipped / Already Set", value=str(len(skipped)), inline=True)
    embed.add_field(name="❌ Failed", value=str(len(failed)), inline=True)

    if updated:
        up_str = "\n".join(updated[:15])
        if len(updated) > 15:
            up_str += f"\n... and {len(updated) - 15} more"
        embed.add_field(name=f"Updated Channels ({len(updated)})", value=up_str[:1024], inline=False)

    if not_found:
        nf_str = ", ".join(f"`{k}`" for k in not_found)
        if len(nf_str) > 1000:
            nf_str = nf_str[:997] + "..."
        embed.add_field(name=f"⚠️ Not Found in Server ({len(not_found)})", value=nf_str[:1024], inline=False)

    if failed:
        fail_str = "\n".join(f"• `{name}`: {err}" for name, err in failed[:10])
        embed.add_field(name="Errors", value=fail_str[:1024], inline=False)

    try:
        await status_msg.edit(content=None, embed=embed)
    except Exception:
        await ctx.send(embed=embed)


@bot.command(name="channel_separator", aliases=["chsep"])
@commands.guild_only()
@commands.has_permissions(administrator=True)
@commands.bot_has_permissions(administrator=True)
async def channel_separator(ctx, style: str = None):
    """
    Puts a separator between the emoji and the channel name.
    Usage: !channel_separator bar (｜) | dots (┊)
    """
    sep = CHANNEL_SEPARATORS.get((style or "").lower())
    if not sep:
        await ctx.send("Usage: `!channel_separator bar` (｜) or `!channel_separator dots` (┊)")
        return

    changed, skipped, failed = [], 0, []
    for channel in ctx.guild.channels:
        if isinstance(channel, discord.CategoryChannel):
            continue
        key, target_name, _ = _match_channel_emoji(channel.name)
        if not key:
            continue
        prefix = target_name[:-len(key)].strip()
        new_name = f"{prefix}{sep}{key}"
        if channel.name == new_name:
            skipped += 1
            continue
        try:
            old = channel.name
            await channel.edit(name=new_name, reason="Channel separator update")
            changed.append(f"`{old}` ➔ `{new_name}`")
            await asyncio.sleep(1.0)
        except Exception as e:
            failed.append(f"`{channel.name}`: {e}")

    msg = f"✅ Updated {len(changed)}, skipped {skipped}, failed {len(failed)}."
    if failed:
        msg += "\n" + "\n".join(failed[:10])
    await ctx.send(msg[:2000])


@bot.command(
    name="revert_channels",
    aliases=["unemojify_channels", "revert_channel_emojis", "unemojify", "clear_channel_emojis"],
)
@commands.guild_only()
@commands.has_permissions(administrator=True)
@commands.bot_has_permissions(administrator=True)
async def revert_channels(ctx):
    """
    Reverts server channel titles back to their original names without emoji prefixes.
    Requires administrator permissions for both the command invoker and the bot.
    """
    if not ctx.guild:
        await ctx.send("🚫 This command can only be used in a server.")
        return

    if not ctx.author.guild_permissions.administrator:
        await ctx.send("🚫 You must have Administrator permissions to run this command.")
        return

    if not ctx.guild.me.guild_permissions.administrator:
        await ctx.send("🚫 The bot must have Administrator permissions to run this command.")
        return

    status_msg = await ctx.send(
        f"⏳ Processing channel revert ({len(CHANNEL_EMOJI_MAP)} mapped entries)... Please wait."
    )

    reverted = []
    skipped = []
    failed = []
    matched_keys = set()

    for channel in ctx.guild.channels:
        if isinstance(channel, discord.CategoryChannel):
            continue

        matched_key, _, _ = _match_channel_emoji(channel.name)
        if not matched_key:
            continue

        matched_keys.add(matched_key)
        original_name = matched_key

        clean_cname = channel.name.strip().replace("’", "'")
        clean_oname = original_name.strip().replace("’", "'")
        if clean_cname == clean_oname:
            skipped.append(f"`{channel.name}` (already clean)")
            continue

        try:
            old_name = channel.name
            await channel.edit(name=original_name, reason="Reverting channel emojis to original titles")
            reverted.append(f"`{old_name}` ➔ `{original_name}`")
            await asyncio.sleep(1.0)
        except discord.Forbidden:
            failed.append((channel.name, "Forbidden / Missing Permissions"))
        except discord.HTTPException as e:
            failed.append((channel.name, f"HTTP {e.status}: {e.text}"))
        except Exception as e:
            failed.append((channel.name, str(e)))

    not_found = sorted(list(set(CHANNEL_EMOJI_MAP.keys()) - matched_keys))

    embed = discord.Embed(
        title="🔄 Channel Revert Complete",
        color=discord.Color.green() if not failed else discord.Color.orange(),
    )
    embed.add_field(name="✅ Reverted", value=str(len(reverted)), inline=True)
    embed.add_field(name="⏭️ Already Clean / Skipped", value=str(len(skipped)), inline=True)
    embed.add_field(name="❌ Failed", value=str(len(failed)), inline=True)

    if reverted:
        rev_str = "\n".join(reverted[:15])
        if len(reverted) > 15:
            rev_str += f"\n... and {len(reverted) - 15} more"
        embed.add_field(name=f"Reverted Channels ({len(reverted)})", value=rev_str[:1024], inline=False)

    if not_found:
        nf_str = ", ".join(f"`{k}`" for k in not_found)
        if len(nf_str) > 1000:
            nf_str = nf_str[:997] + "..."
        embed.add_field(name=f"⚠️ Not Found in Server ({len(not_found)})", value=nf_str[:1024], inline=False)

    if failed:
        fail_str = "\n".join(f"• `{name}`: {err}" for name, err in failed[:10])
        embed.add_field(name="Errors", value=fail_str[:1024], inline=False)

    try:
        await status_msg.edit(content=None, embed=embed)
    except Exception:
        await ctx.send(embed=embed)


if __name__ == "__main__":
    bot.run(os.environ.get("BOT_TOKEN"))
