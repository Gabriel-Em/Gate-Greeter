# Server Management Bot

A modular Discord bot built with `discord.py` designed for stable operations, robust moderation, and server analytics. Features, thresholds, role IDs, and channel IDs can be individually configured or toggled via environment variables.

## Features

### 🛡️ Moderation & Security System
- **New Member Greeting**: Greets new members with a welcome message mentioning their username, bolding the server name, and linking directly to `#rules` (`<#RULES_CHANNEL_ID>`) for onboarding instructions.
- **Manual Quarantine (`!quarantine @user`)**: Allows staff with `manage_roles` permissions to manually quarantine a user, securely snapshotting all their roles, stripping non-category roles, assigning the Quarantine role, and posting an alert in the quarantine channel with a one-click release button.
- **Prison & Quarantine Release (`!release @user` / `!unquarantine @user` / "🌿 Release into the Wild" Button)**:
  - Releases quarantined users or prison inmates, automatically restoring all of their prior saved roles.
  - Quarantine alerts in the quarantine channel include an interactive, persistent **"🌿 Release into the Wild"** button allowing staff to release quarantined users with a single click.
  - Staff manually removing the Quarantine role in Discord's UI also automatically triggers saved role restoration.
- **Auto-Quarantine (New Account Protection)**: Automatically quarantines newly created accounts (default: `<= 7` days old, configurable via `QUARANTINE_ACCOUNT_AGE_DAYS`) upon joining, wiping non-category roles, assigning the Quarantine role (overriding any prior prison state), pinging the user with an explanation in the quarantine channel, and logging the action in the quarantine logs channel.
- **AutoMod New Member Monitoring**: Gate Greeter actively monitors new members who joined the server less than 7 days ago (`QUARANTINE_NEW_MEMBER_DAYS`). If they trigger Discord AutoMod (e.g., banned words, mention spam, or regex filters), they are immediately dragged to quarantine, their prior roles are preserved, and they receive an @-pinged explanation in the quarantine channel with a one-click staff release button.
- **Evasion Prevention & Strict Role Enforcement**:
  - If an imprisoned or quarantined user leaves and rejoins the server to bypass their lockdown, the bot detects their return, strips any newly acquired onboarding roles, drags them back into prison or quarantine, and logs the evasion attempt.
  - Monitors member role updates in real time (`on_member_update`) and immediately strips any unauthorized self-assigned or onboarding roles acquired while a user is in Quarantine or Prison.
- **"Unc" Misclick Dynasty**: Detects users who misclick voice channels (joining and leaving within `UNC_MISCLICK_MAX_SECONDS`, default 3.0s), or sitting completely alone in an empty channel for up to `UNC_SOLO_MAX_SECONDS` (default 25s), and assigns them the "Unc" role. Solo stays longer than `UNC_SOLO_DODGE_MIN_SECONDS` (default 10.0s) trigger dedicated timer-dodging and tricking callouts, whereas shorter solo stays use harmless wandering templates. Includes a 5-tier progression system (Unc ➔ Grand Unc ➔ Great-Grand Unc ➔ Ancestral Unc ➔ The First Unc) tracked persistently in the database, protected by a configurable anti-spam cooldown (`UNC_COOLDOWN_SECONDS`, default 1 hour).
  - **Anti-gaming protection (rolling trap)**: every short stay is recorded. If a user is currently shadowbanned, further short stays are silently ignored and extend the lockout by `UNC_ABUSE_LOCKOUT_SECONDS` (default 6 hours). When the user accumulates `UNC_ABUSE_THRESHOLD` (default 5) short stays within `UNC_ABUSE_WINDOW_SECONDS` (default 7 days / 604800s), they receive a public roast, are not awarded, and are locked out for 6 hours. History is never cleared on lockout and only leaves the record by naturally aging out of the 7-day rolling window. If the lockout expires and they still have 5+ short stays within the 7-day window, their next attempt triggers another roast and reapplies the 6-hour lockout. Cooldown (`UNC_COOLDOWN_SECONDS`, default 1 hour) applies to awards between normal misclicks. Lockouts and roasts do not demote a user or strip previously earned Unc roles. Cooldown, lockout, and history are stored in SQLite and survive restarts.
- **Trust System (Anti-Raid Media Permissions)**: Protects the server by automatically awarding a "Trusted Media" role once a non-restricted user reaches `TRUST_THRESHOLD` (default 30) valid chat interactions. Features a configurable cooldown (`TRUST_COOLDOWN_SECONDS`, default 60s) between counted messages to prevent spamming.
- **Anti-Nuke System (Channel Deletion Protection)**: Monitors server audit logs for channel deletions. If a user or bot deletes `ANTI_NUKE_CHANNEL_LIMIT` (default 2) channels within `ANTI_NUKE_WINDOW_SECONDS` (default 600s / 10 minutes), the bot automatically kicks rogue bots or strips all roles from compromised human accounts and alerts moderators in `MODS_CHAT_ID`.

### 🗂️ Server Backup & Snapshot
- **Server Structure Snapshot (`!snapshot`)**: Captures the complete structural state of the server—including server settings, roles (hierarchy order, colors, hoist/mentionable flags, and permissions), categories, and channels (text, voice, stage, forum, announcement settings, and role permission overwrites).
- **Dual Machine & Human-Readable Formats**:
  - `backups/server_snapshot.json`: Stores raw IDs, integer colors, and permission bitfields ready for an automated `!restore` workflow alongside decoded permission names.
  - `backups/server_snapshot.md`: Generates a formatted Markdown recovery manual with tables and lists so admins can manually rebuild any part of the server without needing the bot.
  - Both files are saved locally in `backups/` and attached directly to the Discord response.
- **Strict Zero-User-Data Policy**: Never stores messages, member lists, or user-specific channel overwrites (only `discord.Role` overwrites are recorded).

### 📈 Analytics
*Channels ending in `logs` or `log` are excluded from tracking.*
- **`!activity [months]`**: Generates a monthly message activity graph for the past number of months (default 12, up to 120). Automatically backfills and scrapes unarchived historical months on demand.
- **`!topusers [year] <month>`**: Displays the top 10 most active members for a given year or specific month.
- **`!activity30`**: Generates 30-day hourly (adjusted by `ANALYTICS_TIMEZONE_OFFSET_HOURS`) and daily server-wide message activity graphs.
- **`!pronounstats <year>`**: Graphs demographics for active users (`PRONOUN_ACTIVE_MSG_THRESHOLD`+ messages in a single month) across configured pronoun roles (`PRONOUN_ROLE_IDS`).
- **`!myemojis`**: Displays your top 10 most used emojis over the past 30 days, along with total unique emojis and total usage counts.
- **`!emojileaderboard`**: Server-wide leaderboard ranking the top 15 most used emojis over the past 30 days, their total uses, and the top user for each emoji.
- **`!syncbaseline`**: Admin-only command to trigger a 30-day baseline backfill across channels.

### 🤖 Auto Responder & Core Functions
- **Auto Responder**: Handles regex-triggered explanations in server channels—automatically replying to queries about VO2 Max (`ENABLE_RESP_VO2MAX`) and whether Alex O'Connor is on or runs the server (`ENABLE_RESP_ALEX`).
- **Dynamic Help (`!help`)**: A custom embed help command that displays all available commands, their arguments, and whether they are currently `Active` or `Disabled` based on the `.env` configuration.
- **Admin Restart (`!restart`)**: Allows admins to trigger a container reboot and pull the latest code directly from Discord.
- **Global Error Handling**: Intercepts unknown commands, invalid/missing arguments, permission errors, and internal backend crashes, sending formatted feedback directly to the Discord channel.

## 🗄️ Database Storage

Persistent state is stored in an embedded SQLite database (`bot_database.db`) configured with Write-Ahead Logging (`WAL`). Message events are buffered in memory and flushed periodically in batches to reduce disk operations. Moderation actions (quarantine, prison) commit immediately in isolated transactions.

### Database Tables & Tracked Data

| Table Name | Tracked Columns | Description & Retention Policy |
| :--- | :--- | :--- |
| **`user_monthly_activity`** | `year`, `month`, `user_id`, `user_name`, `message_count` | Aggregates message counts per user per month. Powers **`!topusers`** and active user eligibility for **`!pronounstats`**. |
| **`month_metadata`** | `year`, `month`, `is_complete` | Flags whether a past calendar month has ended and is fully archived so it never needs to be re-scanned. |
| **`activity_hourly`** | `date_hour` (UTC `'YYYY-MM-DD HH'`), `message_count` | Stores server-wide message volume in hourly increments (max 720 rows). Feeds the 30-day graphs for **`!activity30`**. An hourly maintenance task automatically deletes entries older than 30 days. |
| **`channel_sync`** | `channel_id`, `last_message_id`, `last_updated_at` | Stores the latest processed message ID watermark per channel. On bot startup or after an outage, the bot performs a catch-up scan pulling only the delta messages sent during downtime. |
| **`emoji_daily_usage`** | `date` (`'YYYY-MM-DD'`), `user_id`, `user_name`, `emoji`, `count` | Stores daily emoji counts per user. Feeds **`!myemojis`** and **`!emojileaderboard`**. Entries older than 30 days are automatically deleted by the hourly maintenance loop. |
| **`prison_records`** | `user_id`, `roles` (JSON list of role IDs), `jailed_at` | Preserves all stripped non-category roles when a member is sent to Prison Island (`!prison`), allowing full role restoration on release. |
| **`unc_records`** | `user_id`, `count` | Tracks voice channel misclick counts for the 5-tier Unc dynasty role progression. |
| **`trust_records`** | `user_id`, `points` | Tracks interaction points for new members towards unlocking media permissions (`TRUST_THRESHOLD`). The row is cleared once the role is awarded. |
| **`quarantine_records`** | `user_id`, `roles` (JSON list of role IDs), `quarantined_at`, `reason`, `type` | Preserves prior roles, timestamp, reason, and trigger type (`automod`, `account_age`, `manual`, `evasion`) for quarantined users to restore roles on release or re-enforce quarantine on rejoin. |
| **`bot_metadata`** | `key`, `value` | Tracks internal state flags, such as `initial_30d_sync_done`, ensuring the initial 30-day baseline backfill executes exactly once. |

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
   ENABLE_CMD_ACTIVITY=True
   ENABLE_CMD_ACTIVITY30=True
   ENABLE_CMD_TOPUSERS=True
   ENABLE_CMD_PRONOUNSTATS=True
   ENABLE_CMD_MYEMOJIS=True
   ENABLE_CMD_EMOJILEADERBOARD=True

   # Granular Auto-Responders
   ENABLE_RESP_VO2MAX=True
   ENABLE_RESP_ALEX=True

   # Moderation Commands
   ENABLE_CMD_PRISON=True

   # Backup Commands
   ENABLE_CMD_SNAPSHOT=True

   # Moderation Thresholds & Timers
   QUARANTINE_ACCOUNT_AGE_DAYS=7
   QUARANTINE_NEW_MEMBER_DAYS=7
   TRUST_THRESHOLD=30
   TRUST_COOLDOWN_SECONDS=60
   UNC_MISCLICK_MAX_SECONDS=3.0
   UNC_SOLO_DODGE_MIN_SECONDS=10.0
   UNC_SOLO_MAX_SECONDS=25.0
   UNC_COOLDOWN_SECONDS=3600
   UNC_ABUSE_THRESHOLD=5
   UNC_ABUSE_WINDOW_SECONDS=604800
   UNC_ABUSE_LOCKOUT_SECONDS=21600
   ANTI_NUKE_CHANNEL_LIMIT=2
   ANTI_NUKE_WINDOW_SECONDS=600

   # Discord Channel IDs
   GENERAL_CHANNEL_ID=100000000000000001
   QUARANTINE_CHANNEL_ID=100000000000000002
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
