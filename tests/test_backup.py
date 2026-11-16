import json
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import discord
from cogs.backup import Backup

# Discord permission bits. discord.py names the View Channel bit "read_messages".
VIEW_CHANNEL = 1 << 10
SEND_MESSAGES = 1 << 11


def _role(role_id, name, position, default=False):
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.name = name
    role.position = position
    role.is_default.return_value = default
    return role


class TestSnapshotSerialization(unittest.TestCase):
    def test_overwrites_keep_roles_only_and_never_member_data(self):
        """Zero-user-data policy: member overwrites must not reach the snapshot."""
        everyone = _role(1, "@everyone", 0, default=True)
        mods = _role(2, "Mods", 5)
        untouched = _role(3, "Untouched", 9)
        member = MagicMock(spec=discord.Member)
        member.id = 424242
        member.name = "private-person"

        channel = MagicMock(spec=discord.TextChannel)
        channel.overwrites = {
            everyone: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            untouched: discord.PermissionOverwrite(),
            mods: discord.PermissionOverwrite(view_channel=True, send_messages=False),
        }

        result = Backup._serialize_role_overwrites(channel)

        self.assertEqual(
            result,
            [
                {
                    "role_id": 2,
                    "role_name": "Mods",
                    "is_default": False,
                    "allow_value": VIEW_CHANNEL,
                    "deny_value": SEND_MESSAGES,
                    "allow_permissions": ["read_messages"],
                    "deny_permissions": ["send_messages"],
                },
                {
                    "role_id": 1,
                    "role_name": "@everyone",
                    "is_default": True,
                    "allow_value": 0,
                    "deny_value": VIEW_CHANNEL,
                    "allow_permissions": [],
                    "deny_permissions": ["read_messages"],
                },
            ],
        )
        dumped = json.dumps(result)
        self.assertNotIn("424242", dumped)
        self.assertNotIn("private-person", dumped)

    def test_channel_without_overwrites_serializes_to_empty_list(self):
        channel = MagicMock(spec=discord.TextChannel)
        channel.overwrites = {}
        self.assertEqual(Backup._serialize_role_overwrites(channel), [])

    def test_role_color_hex_is_zero_padded(self):
        role = _role(7, "Blue", 3)
        role.color = discord.Color(0x0000FF)
        role.hoist = True
        role.mentionable = False
        role.managed = False
        role.permissions = discord.Permissions(SEND_MESSAGES)

        data = Backup._serialize_role(role)

        self.assertEqual(data["color_int"], 255)
        self.assertEqual(data["color_hex"], "#0000ff")
        self.assertEqual(data["permissions_value"], SEND_MESSAGES)
        self.assertEqual(data["permissions_enabled"], ["send_messages"])


class TestMarkdownBlueprint(unittest.TestCase):
    def _channel(self, channel_id, name, category_id, synced, overwrites=(), **extra):
        return {
            "id": channel_id,
            "name": name,
            "type": "text",
            "position": 0,
            "category_id": category_id,
            "category_name": None,
            "permissions_synced": synced,
            "overwrites": list(overwrites),
            **extra,
        }

    def _snapshot(self):
        mods_overwrite = {
            "role_id": 2,
            "role_name": "Mods",
            "is_default": False,
            "allow_value": VIEW_CHANNEL,
            "deny_value": 0,
            "allow_permissions": ["view_channel"],
            "deny_permissions": [],
        }
        return {
            "snapshot_timestamp_utc": "2026-01-01 00:00:00 UTC",
            "bot_had_administrator": True,
            "guild": {
                "id": 99,
                "name": "Test Guild",
                "verification_level": "low",
                "explicit_content_filter": "disabled",
                "default_notifications": "only_mentions",
                "afk_channel_name": None,
                "afk_timeout": 300,
                "system_channel_name": "general",
                "rules_channel_name": None,
            },
            "roles": [
                {
                    "id": 2, "name": "Mods", "position": 5, "color_hex": "#00ff00",
                    "hoist": True, "mentionable": False, "managed": False,
                    "permissions_value": 8, "permissions_enabled": ["administrator"],
                },
                {
                    "id": 1, "name": "@everyone", "position": 0, "color_hex": "#000000",
                    "hoist": False, "mentionable": False, "managed": False,
                    "permissions_value": 0, "permissions_enabled": [],
                },
            ],
            "categories": [
                {"id": 10, "name": "Staff", "position": 0, "nsfw": False, "overwrites": [mods_overwrite]},
                {"id": 11, "name": "Empty", "position": 1, "nsfw": False, "overwrites": []},
            ],
            "channels": [
                self._channel(20, "lobby", None, False, topic="line one\nline two"),
                self._channel(21, "mod-chat", 10, True, overwrites=[mods_overwrite]),
                self._channel(22, "mod-private", 10, False, overwrites=[mods_overwrite], slowmode_delay=30),
            ],
        }

    def test_blueprint_lists_roles_categories_and_channel_permissions(self):
        md = Backup._generate_markdown_blueprint(self._snapshot())
        lines = md.split("\n")

        self.assertEqual(lines[0], "# 🛡️ Server Structure Blueprint: Test Guild")
        self.assertIn("- **AFK Channel:** `None` (Timeout: `300s`)", lines)
        self.assertIn("| `5` | **Mods** | `2` | `#00ff00` | `True` | `False` | `False` | `8` |", lines)
        self.assertIn("- **[5] `Mods`** (`#00ff00`): `administrator`", lines)
        self.assertIn("- **[0] `@everyone`** (`#000000`): *None*", lines)
        self.assertIn("## 📁 Categories & Channels (2 Categories, 3 Channels)", lines)

        # Sections appear in order: uncategorized, then each category with its own channels.
        order = [
            lines.index("### 📂 [Uncategorized Channels]"),
            lines.index("#### 💬 `lobby` (ID: `20`)"),
            lines.index("### 📁 Category: `Staff` (ID: `10` | Position: `0` | NSFW: `False`)"),
            lines.index("#### 💬 `mod-chat` (ID: `21`)"),
            lines.index("#### 💬 `mod-private` (ID: `22`)"),
            lines.index("### 📁 Category: `Empty` (ID: `11` | Position: `1` | NSFW: `False`)"),
            lines.index("*No channels in this category.*"),
        ]
        self.assertEqual(order, sorted(order))

        # Multi-line topics are flattened so they cannot break the document structure.
        self.assertIn("- **Topic:** line one line two", lines)
        self.assertIn("- **Settings:** Slowmode: `30s`", lines)

    def test_synced_channel_refers_to_category_and_unsynced_lists_overwrites(self):
        snapshot = self._snapshot()
        synced = Backup._format_channel_markdown(snapshot["channels"][1])
        unsynced = Backup._format_channel_markdown(snapshot["channels"][2])
        uncategorized = Backup._format_channel_markdown(snapshot["channels"][0])

        self.assertIn("- **Permission Overwrites:** *Synced directly with parent category*", synced)
        self.assertFalse(any("Role: `Mods`" in line for line in synced))

        self.assertIn("  - **Role: `Mods`** (ID: `2`)", unsynced)
        self.assertIn(f"    - ✅ **Allow** (`{VIEW_CHANNEL}`): `view_channel`", unsynced)
        self.assertFalse(any("Deny" in line for line in unsynced))

        self.assertIn("  - *No custom role overwrites*", uncategorized)


if __name__ == "__main__":
    unittest.main()
