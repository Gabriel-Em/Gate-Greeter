import os
import io
import asyncio
from datetime import datetime, timezone
import discord
from discord.ext import commands

from utils import is_env_enabled, save_json_data

# ---------------------------------------------------------
# MODULE-LEVEL CONSTANTS
# ---------------------------------------------------------
BACKUP_DIR = "backups"
SNAPSHOT_JSON_FILE = os.path.join(BACKUP_DIR, "server_snapshot.json")
SNAPSHOT_MD_FILE = os.path.join(BACKUP_DIR, "server_snapshot.md")


class Backup(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.snapshot_lock = asyncio.Lock()
        os.makedirs(BACKUP_DIR, exist_ok=True)

    # ---------------------------------------------------------
    # SERIALIZATION HELPERS (Zero User Data)
    # ---------------------------------------------------------
    @staticmethod
    def _extract_enabled_permissions(perms: discord.Permissions) -> list[str]:
        """Returns a sorted list of enabled permission names from a Permissions object."""
        return sorted([name for name, value in perms if value is True])

    @classmethod
    def _serialize_role_overwrites(cls, channel: discord.abc.GuildChannel) -> list[dict]:
        """
        Extracts channel/category permission overwrites strictly for Roles.
        Member-specific overwrites are intentionally excluded so zero user data is saved.
        """
        overwrites_data = []

        # Sort role overwrites by role hierarchy position (highest first, @everyone last)
        role_items = [
            (target, overwrite)
            for target, overwrite in channel.overwrites.items()
            if isinstance(target, discord.Role)
        ]
        role_items.sort(key=lambda item: item[0].position, reverse=True)

        for role, overwrite in role_items:
            allow_perms, deny_perms = overwrite.pair()
            if allow_perms.value == 0 and deny_perms.value == 0:
                continue

            overwrites_data.append(
                {
                    "role_id": role.id,
                    "role_name": role.name,
                    "is_default": role.is_default(),
                    "allow_value": allow_perms.value,
                    "deny_value": deny_perms.value,
                    "allow_permissions": cls._extract_enabled_permissions(allow_perms),
                    "deny_permissions": cls._extract_enabled_permissions(deny_perms),
                }
            )

        return overwrites_data

    @classmethod
    def _serialize_role(cls, role: discord.Role) -> dict:
        """Serializes a Discord Role with both machine-restore values and human-readable fields."""
        return {
            "id": role.id,
            "name": role.name,
            "position": role.position,
            "color_int": role.color.value,
            "color_hex": f"#{role.color.value:06x}",
            "hoist": role.hoist,
            "mentionable": role.mentionable,
            "managed": role.managed,
            "is_default": role.is_default(),
            "permissions_value": role.permissions.value,
            "permissions_enabled": cls._extract_enabled_permissions(role.permissions),
        }

    @classmethod
    def _serialize_category(cls, category: discord.CategoryChannel) -> dict:
        """Serializes a Discord CategoryChannel."""
        return {
            "id": category.id,
            "name": category.name,
            "position": category.position,
            "nsfw": category.nsfw,
            "overwrites": cls._serialize_role_overwrites(category),
        }

    @classmethod
    def _serialize_channel(cls, channel: discord.abc.GuildChannel) -> dict:
        """Serializes a non-category guild channel (Text, News, Voice, Stage, Forum)."""
        data = {
            "id": channel.id,
            "name": channel.name,
            "type": str(channel.type),
            "position": channel.position,
            "category_id": channel.category.id if channel.category else None,
            "category_name": channel.category.name if channel.category else None,
            "permissions_synced": channel.permissions_synced,
            "overwrites": cls._serialize_role_overwrites(channel),
        }

        if isinstance(channel, discord.TextChannel):
            data.update(
                {
                    "topic": channel.topic,
                    "nsfw": channel.nsfw,
                    "slowmode_delay": channel.slowmode_delay,
                    "default_auto_archive_duration": channel.default_auto_archive_duration,
                    "is_news": channel.is_news(),
                }
            )
        elif isinstance(channel, discord.ForumChannel):
            data.update(
                {
                    "topic": channel.topic,
                    "nsfw": channel.nsfw,
                    "slowmode_delay": channel.slowmode_delay,
                    "default_thread_slowmode_delay": getattr(
                        channel, "default_thread_slowmode_delay", 0
                    ),
                    "default_auto_archive_duration": channel.default_auto_archive_duration,
                    "default_layout": str(channel.default_layout)
                    if getattr(channel, "default_layout", None)
                    else None,
                    "default_sort_order": str(channel.default_sort_order)
                    if getattr(channel, "default_sort_order", None)
                    else None,
                    "available_tags": [
                        {
                            "name": tag.name,
                            "moderated": tag.moderated,
                            "emoji": str(tag.emoji) if tag.emoji else None,
                        }
                        for tag in getattr(channel, "available_tags", [])
                    ],
                }
            )
        elif isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
            data.update(
                {
                    "bitrate": channel.bitrate,
                    "user_limit": channel.user_limit,
                    "rtc_region": str(channel.rtc_region) if channel.rtc_region else None,
                    "video_quality_mode": str(channel.video_quality_mode)
                    if getattr(channel, "video_quality_mode", None)
                    else None,
                    "nsfw": getattr(channel, "nsfw", False),
                    "slowmode_delay": getattr(channel, "slowmode_delay", 0),
                    "topic": getattr(channel, "topic", None),
                }
            )

        return data

    @classmethod
    def _build_snapshot_payload(cls, guild: discord.Guild) -> dict:
        """Constructs the complete machine- and human-readable snapshot dictionary."""
        now_utc = datetime.now(timezone.utc)

        # Sort roles from highest position down to @everyone
        sorted_roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)
        roles_data = [cls._serialize_role(role) for role in sorted_roles]

        # Sort categories by position
        sorted_categories = sorted(guild.categories, key=lambda c: c.position)
        categories_data = [cls._serialize_category(cat) for cat in sorted_categories]

        # Gather non-category channels sorted by category position then channel position
        non_category_channels = [
            ch for ch in guild.channels if not isinstance(ch, discord.CategoryChannel)
        ]
        non_category_channels.sort(
            key=lambda ch: (
                ch.category.position if ch.category else -1,
                0 if isinstance(ch, (discord.TextChannel, discord.ForumChannel)) else 1,
                ch.position,
            )
        )
        channels_data = [cls._serialize_channel(ch) for ch in non_category_channels]

        return {
            "schema_version": 1,
            "snapshot_timestamp_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "snapshot_iso": now_utc.isoformat(),
            "bot_had_administrator": guild.me.guild_permissions.administrator,
            "guild": {
                "id": guild.id,
                "name": guild.name,
                "verification_level": str(guild.verification_level),
                "default_notifications": str(guild.default_notifications),
                "explicit_content_filter": str(guild.explicit_content_filter),
                "afk_timeout": guild.afk_timeout,
                "afk_channel_id": guild.afk_channel.id if guild.afk_channel else None,
                "afk_channel_name": guild.afk_channel.name if guild.afk_channel else None,
                "system_channel_id": guild.system_channel.id if guild.system_channel else None,
                "system_channel_name": guild.system_channel.name if guild.system_channel else None,
                "system_channel_flags": guild.system_channel_flags.value,
                "rules_channel_id": guild.rules_channel.id if guild.rules_channel else None,
                "rules_channel_name": guild.rules_channel.name if guild.rules_channel else None,
            },
            "roles": roles_data,
            "categories": categories_data,
            "channels": channels_data,
        }

    # ---------------------------------------------------------
    # HUMAN-READABLE MARKDOWN GENERATOR
    # ---------------------------------------------------------
    @staticmethod
    def _format_overwrites_markdown(overwrites: list[dict], indent: str = "  ") -> list[str]:
        """Formats role permission overwrites as readable Markdown bullet points."""
        if not overwrites:
            return [f"{indent}- *No custom role overwrites*"]

        lines = []
        for ow in overwrites:
            lines.append(f"{indent}- **Role: `{ow['role_name']}`** (ID: `{ow['role_id']}`)")
            if ow["allow_permissions"]:
                allowed_str = ", ".join(f"`{p}`" for p in ow["allow_permissions"])
                lines.append(f"{indent}  - ✅ **Allow** (`{ow['allow_value']}`): {allowed_str}")
            if ow["deny_permissions"]:
                denied_str = ", ".join(f"`{p}`" for p in ow["deny_permissions"])
                lines.append(f"{indent}  - ❌ **Deny** (`{ow['deny_value']}`): {denied_str}")
        return lines

    @classmethod
    def _format_channel_markdown(cls, ch: dict) -> list[str]:
        """Formats a single channel's settings and overwrites into Markdown lines."""
        ch_type = ch["type"]
        icon = "💬"
        if ch_type in ("voice", "stage_voice"):
            icon = "🔊" if ch_type == "voice" else "🎙️"
        elif ch_type == "forum":
            icon = "🗂️"
        elif ch.get("is_news"):
            icon = "📢"

        lines = [
            f"#### {icon} `{ch['name']}` (ID: `{ch['id']}`)",
            f"- **Type:** `{ch_type}` | **Position:** `{ch['position']}` | **Synced with Category:** `{ch['permissions_synced']}`",
        ]

        # Channel-specific settings
        details = []
        if ch.get("nsfw"):
            details.append("NSFW: `True`")
        if ch.get("slowmode_delay"):
            details.append(f"Slowmode: `{ch['slowmode_delay']}s`")
        if ch.get("default_auto_archive_duration"):
            details.append(f"Auto-Archive: `{ch['default_auto_archive_duration']}m`")
        if ch.get("bitrate"):
            details.append(f"Bitrate: `{ch['bitrate'] // 1000} kbps`")
        if ch.get("user_limit"):
            details.append(f"User Limit: `{ch['user_limit']}`")
        if ch.get("rtc_region"):
            details.append(f"Region: `{ch['rtc_region']}`")
        if details:
            lines.append("- **Settings:** " + " | ".join(details))

        if ch.get("topic"):
            topic_clean = ch["topic"].replace("\n", " ")
            lines.append(f"- **Topic:** {topic_clean}")

        if ch.get("available_tags"):
            tags_str = ", ".join(
                f"{t['emoji'] + ' ' if t['emoji'] else ''}`{t['name']}`"
                for t in ch["available_tags"]
            )
            lines.append(f"- **Forum Tags:** {tags_str}")

        if ch["permissions_synced"] and ch["category_id"] is not None:
            lines.append("- **Permission Overwrites:** *Synced directly with parent category*")
        else:
            lines.append("- **Permission Overwrites:**")
            lines.extend(cls._format_overwrites_markdown(ch["overwrites"], indent="  "))

        lines.append("")
        return lines

    @classmethod
    def _generate_markdown_blueprint(cls, snapshot: dict) -> str:
        """Generates a human-readable Markdown recovery blueprint from the snapshot payload."""
        guild_info = snapshot["guild"]
        lines = [
            f"# 🛡️ Server Structure Blueprint: {guild_info['name']}",
            "",
            f"- **Server ID:** `{guild_info['id']}`",
            f"- **Snapshot Taken:** `{snapshot['snapshot_timestamp_utc']}`",
            f"- **Bot Had Administrator:** `{snapshot['bot_had_administrator']}`",
            f"- **Verification Level:** `{guild_info['verification_level']}`",
            f"- **Explicit Content Filter:** `{guild_info['explicit_content_filter']}`",
            f"- **Default Notifications:** `{guild_info['default_notifications']}`",
            f"- **AFK Channel:** `{guild_info['afk_channel_name'] or 'None'}` (Timeout: `{guild_info['afk_timeout']}s`)",
            f"- **System Channel:** `{guild_info['system_channel_name'] or 'None'}`",
            f"- **Rules Channel:** `{guild_info['rules_channel_name'] or 'None'}`",
            "",
            "---",
            "",
            f"## 🎭 Server Roles ({len(snapshot['roles'])} Total — Top to Bottom Hierarchy)",
            "",
            "| Pos | Role Name | Role ID | Color | Hoist (Separate) | Mentionable | Managed (Bot/Boost) | Perms Bitfield |",
            "| :---: | :--- | :--- | :---: | :---: | :---: | :---: | :--- |",
        ]

        for role in snapshot["roles"]:
            lines.append(
                f"| `{role['position']}` | **{role['name']}** | `{role['id']}` | `{role['color_hex']}` | "
                f"`{role['hoist']}` | `{role['mentionable']}` | `{role['managed']}` | `{role['permissions_value']}` |"
            )

        lines.extend(["", "### Role Permissions Breakdown", ""])
        for role in snapshot["roles"]:
            perms = role["permissions_enabled"]
            perms_str = ", ".join(f"`{p}`" for p in perms) if perms else "*None*"
            tag = " *(Managed by Bot/Integration)*" if role["managed"] else ""
            lines.append(
                f"- **[{role['position']}] `{role['name']}`** (`{role['color_hex']}`){tag}: {perms_str}"
            )

        lines.extend(
            [
                "",
                "---",
                "",
                f"## 📁 Categories & Channels ({len(snapshot['categories'])} Categories, {len(snapshot['channels'])} Channels)",
                "",
            ]
        )

        # 1. Uncategorized channels first
        uncategorized = [ch for ch in snapshot["channels"] if ch["category_id"] is None]
        if uncategorized:
            lines.extend(["### 📂 [Uncategorized Channels]", ""])
            for ch in uncategorized:
                lines.extend(cls._format_channel_markdown(ch))

        # 2. Each category and its child channels
        for cat in snapshot["categories"]:
            lines.extend(
                [
                    f"### 📁 Category: `{cat['name']}` (ID: `{cat['id']}` | Position: `{cat['position']}` | NSFW: `{cat['nsfw']}`)",
                    "",
                    "- **Category Permission Overwrites:**",
                ]
            )
            lines.extend(cls._format_overwrites_markdown(cat["overwrites"], indent="  "))
            lines.append("")

            cat_channels = [
                ch for ch in snapshot["channels"] if ch["category_id"] == cat["id"]
            ]
            if not cat_channels:
                lines.extend(["*No channels in this category.*", ""])
            else:
                for ch in cat_channels:
                    lines.extend(cls._format_channel_markdown(ch))

            lines.extend(["---", ""])

        return "\n".join(lines)

    # ---------------------------------------------------------
    # SNAPSHOT COMMAND
    # ---------------------------------------------------------
    @commands.command(name="snapshot")
    @commands.has_permissions(administrator=True)
    async def snapshot(self, ctx):
        if not is_env_enabled("ENABLE_CMD_SNAPSHOT"):
            return

        status_msg = await ctx.send("📸 Capturing server structure snapshot (roles, categories, channels, and permissions)...")

        snapshot_data = self._build_snapshot_payload(ctx.guild)
        markdown_content = self._generate_markdown_blueprint(snapshot_data)

        async with self.snapshot_lock:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            save_json_data(SNAPSHOT_JSON_FILE, snapshot_data)
            with open(SNAPSHOT_MD_FILE, "w", encoding="utf-8") as f:
                f.write(markdown_content)

        # Count channel types for summary
        channels = snapshot_data["channels"]
        text_count = sum(1 for c in channels if c["type"] in ("text", "news", "forum"))
        voice_count = sum(1 for c in channels if c["type"] in ("voice", "stage_voice"))

        has_admin = snapshot_data["bot_had_administrator"]
        embed_color = discord.Color.green() if has_admin else discord.Color.orange()

        embed = discord.Embed(
            title="📸 Server Structure Snapshot Complete",
            description=(
                "Saved both a **machine-readable JSON** (for automated `!restore`) and a "
                "**human-readable Markdown blueprint** (for manual recovery).\n"
                "🔒 *Zero user data, messages, or member-specific overwrites were stored.*"
            ),
            color=embed_color,
        )
        embed.add_field(
            name="📊 Captured Structure",
            value=(
                f"• **Roles:** `{len(snapshot_data['roles'])}`\n"
                f"• **Categories:** `{len(snapshot_data['categories'])}`\n"
                f"• **Text / Forum Channels:** `{text_count}`\n"
                f"• **Voice / Stage Channels:** `{voice_count}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="💾 Local Disk Paths",
            value=f"• `{SNAPSHOT_JSON_FILE}`\n• `{SNAPSHOT_MD_FILE}`",
            inline=False,
        )

        if has_admin:
            embed.add_field(
                name="✅ Full Visibility Confirmed",
                value=(
                    "The bot currently has `Administrator` permission, ensuring 100% of restricted/private "
                    "channels were visible. You may now safely revoke `Administrator` and disable this command in `.env`."
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="⚠️ Administrator Permission Notice",
                value=(
                    "The bot did **not** have `Administrator` when this snapshot was taken. Any private channels "
                    "hidden from the bot's roles will not appear in this snapshot. Grant `Administrator` temporarily "
                    "and re-run `!snapshot` for a guaranteed 100% complete capture."
                ),
                inline=False,
            )

        embed.set_footer(text=f"Timestamp: {snapshot_data['snapshot_timestamp_utc']}")

        json_file = discord.File(SNAPSHOT_JSON_FILE, filename="server_snapshot.json")
        md_file = discord.File(
            fp=io.BytesIO(markdown_content.encode("utf-8")),
            filename="server_snapshot.md",
        )

        await status_msg.delete()
        await ctx.send(embed=embed, files=[json_file, md_file])
        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 📸 Server structure snapshot saved by {ctx.author} "
            f"({len(snapshot_data['roles'])} roles, {len(snapshot_data['categories'])} categories, {len(channels)} channels)."
        )


async def setup(bot):
    await bot.add_cog(Backup(bot))
