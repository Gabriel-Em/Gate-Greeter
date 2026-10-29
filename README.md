# Server Management Bot

A modular Discord bot built with `discord.py` designed for stable operations, robust moderation, and server analytics. Features, thresholds, role IDs, and channel IDs can be individually configured or toggled via environment variables.

## Features

### 🛡️ Moderation & Security System
- **Persistent Prison (`!prison @user` / `!release @user`)**: Strips a user's roles (preserving safe category roles and protecting staff roles), saves their exact role state to a local ledger (`prison_data.json`), and assigns the Inmate role. Releasing the user automatically restores their original roles.
- **Auto-Quarantine (New Account Protection)**: Automatically quarantines newly created accounts (default: `<= 7` days old, configurable via `QUARANTINE_ACCOUNT_AGE_DAYS`) upon joining, wiping non-category roles, assigning the Quarantine role (overriding any prior prison state), and logging the action in the quarantine logs channel.
- **Evasion Prevention & Strict Role Enforcement**:
  - If an imprisoned user leaves and rejoins the server to bypass their lockdown, the bot detects their return, strips any newly acquired onboarding roles, throws them back into prison, and announces the evasion attempt in the general channel.
  - Monitors member role updates in real time (`on_member_update`) and immediately strips any unauthorized self-assigned or onboarding roles acquired while a user is in Quarantine or Prison.
- **"Unc" Misclick Dynasty**: Detects users who misclick voice channels (joining and leaving within `UNC_MISCLICK_MAX_SECONDS`, default 3.0s) and assigns them the "Unc" role. Includes a 5-tier progression system (Unc ➔ Grand Unc ➔ Great-Grand Unc ➔ Ancestral Unc ➔ The First Unc) tracked persistently via `unc_data.json`, protected by a configurable anti-spam cooldown (`UNC_COOLDOWN_SECONDS`, default 1 hour).
- **Trust System (Anti-Raid Media Permissions)**: Protects the server by automatically awarding a "Trusted Media" role once a non-restricted user reaches `TRUST_THRESHOLD` (default 30) valid chat interactions (`trust_data.json`). Features a configurable cooldown (`TRUST_COOLDOWN_SECONDS`, default 60s) between counted messages to prevent spamming.
- **Anti-Nuke System (Channel Deletion Protection)**: Monitors server audit logs for channel deletions. If a user or bot deletes `ANTI_NUKE_CHANNEL_LIMIT` (default 2) channels within `ANTI_NUKE_WINDOW_SECONDS` (default 600s / 10 minutes), the bot automatically kicks rogue bots or strips all roles from compromised human accounts and alerts moderators in `MODS_CHAT_ID`.

### 🗂️ Server Backup & Snapshot
- **Server Structure Snapshot (`!snapshot`)**: Captures the complete structural state of the server—including server settings, roles (hierarchy order, colors, hoist/mentionable flags, and permissions), categories, and channels (text, voice, stage, forum, announcement settings, and role permission overwrites).
- **Dual Machine & Human-Readable Formats**:
  - `backups/server_snapshot.json`: Stores raw IDs, integer colors, and permission bitfields ready for an automated `!restore` workflow alongside decoded permission names.
  - `backups/server_snapshot.md`: Generates a formatted Markdown recovery manual with tables and lists so admins can manually rebuild any part of the server without needing the bot.
  - Both files are saved locally in `backups/` and attached directly to the Discord response.
- **Strict Zero-User-Data Policy**: Never stores messages, member lists, or user-specific channel overwrites (only `discord.Role` overwrites are recorded).

### 📈 Analytics
*Note: Scrapes message history to track server activity over time. Excludes channels ending in `logs`.*
- **`!topusers [year] <month>`**: Shows the top 10 most active members for a given year or specific month, caching data locally in `monthly_stats/` for speed.
- **`!activity30`**: Generates 30-day hourly (adjusted by `ANALYTICS_TIMEZONE_OFFSET_HOURS`) and daily server-wide message graphs.
- **`!archive`**: Deep scans and archives message counts for the past 12 months.
- **`!pronounstats <year>`**: Graphs demographics for active users (`PRONOUN_ACTIVE_MSG_THRESHOLD`+ messages in a single month) across configured pronoun roles (`PRONOUN_ROLE_IDS`).

### 🤖 Auto Responder & Core Functions
- **Auto Responder**: Handles regex-triggered explanations in server channels—automatically replying to queries about VO2 Max (`ENABLE_RESP_VO2MAX`) and whether Alex O'Connor is on or runs the server (`ENABLE_RESP_ALEX`).
- **Dynamic Help (`!help`)**: A custom embed help command that displays all available commands, their arguments, and whether they are currently `Active` or `Disabled` based on the `.env` configuration.
- **Admin Restart (`!restart`)**: Allows admins to trigger a container reboot and pull the latest code directly from Discord.
- **Global Error Handling**: Intercepts unknown commands, invalid/missing arguments, permission errors, and internal backend crashes, sending formatted feedback directly to the Discord channel.

## Setup Instructions

1. **Install Dependencies:**
   Ensure you have Python 3.12+ / 3.14+ installed (compatible with Wispbyte Premium `parkervcp/yolks:python_3.14`), then run:
   ```bash
   pip install -r requirements.txt
   ```

2. **Environment Variables (`.env`):**
   Create a `.env` file in the root directory (you can copy `.env.dev` as a template) to configure the bot, toggles, thresholds, and Discord IDs:
   ```env
   BOT_TOKEN=your_bot_token_here

   # Module Toggles (Global)
   ENABLE_ANALYTICS=True
   ENABLE_AUTO_RESPONDER=True
   ENABLE_MODERATION=True
   ENABLE_BACKUP=True

   # Granular Analytics Commands
   ENABLE_CMD_ARCHIVE=True
   ENABLE_CMD_ACTIVITY30=True
   ENABLE_CMD_TOPUSERS=True
   ENABLE_CMD_PRONOUNSTATS=True

   # Granular Auto-Responders
   ENABLE_RESP_VO2MAX=True
   ENABLE_RESP_ALEX=True

   # Moderation Commands
   ENABLE_CMD_PRISON=True

   # Backup Commands
   ENABLE_CMD_SNAPSHOT=True

   # Moderation Thresholds & Timers
   QUARANTINE_ACCOUNT_AGE_DAYS=7
   TRUST_THRESHOLD=30
   TRUST_COOLDOWN_SECONDS=60
   UNC_MISCLICK_MAX_SECONDS=3.0
   UNC_COOLDOWN_SECONDS=3600
   ANTI_NUKE_CHANNEL_LIMIT=2
   ANTI_NUKE_WINDOW_SECONDS=600

   # Discord Channel IDs
   GENERAL_CHANNEL_ID=100000000000000001
   QUARANTINE_LOGS_CHANNEL_ID=100000000000000002
   MODS_CHAT_ID=100000000000000003

   # Discord Single Role IDs
   INMATE_ROLE_ID=200000000000000001
   QUARANTINE_ROLE_ID=200000000000000002
   TRUSTED_ROLE_ID=200000000000000003

   # Discord Role ID Lists (Comma-separated)
   # UNC_ROLE_IDS order: Unc, Grand Unc, Great-Grand Unc, Ancestral Unc, The First Unc
   UNC_ROLE_IDS=300000000000000001,300000000000000002,300000000000000003,300000000000000004,300000000000000005
   STAFF_ROLE_IDS=400000000000000001,400000000000000002
   CATEGORY_ROLE_IDS=500000000000000001,500000000000000002,500000000000000003

   # Analytics Thresholds & Settings
   ANALYTICS_TIMEZONE_OFFSET_HOURS=3
   PRONOUN_ACTIVE_MSG_THRESHOLD=50

   # Pronoun Role IDs (Order: He/Him, She/Her, They/Them, Use my name, Any/All, Ask for pronouns)
   PRONOUN_ROLE_IDS=600000000000000001,600000000000000002,600000000000000003,600000000000000004,600000000000000005,600000000000000006
   ```

3. **Discord Portal Configuration:**
   - Enable **Message Content Intent** and **Server Members Intent** in the Discord Developer Portal.
   - Ensure the bot's highest role is placed **above** the Inmate role, Quarantine role, Unc roles, the Trusted Media role, and any user/bot roles it needs to manage to prevent `403 Forbidden` errors.
   - Ensure the bot has the **Manage Roles**, **Kick Members**, and **View Audit Log** permissions (and temporarily grant **Administrator** when running `!snapshot` if you want to ensure hidden staff/private channels are captured without adding the bot to each channel).

4. **Run the Bot:**
   ```bash
   python main.py
   ```
