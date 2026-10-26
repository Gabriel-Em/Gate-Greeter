# Server Management Bot

A modular Discord bot built with `discord.py` designed for stable operations, robust moderation, and server analytics. Features can be individually toggled on or off via environment variables.

## Features

### 🛡️ Moderation & Prison System
- **Persistent Prison (`!prison @user` / `!release @user`)**: Strips a user's roles (preserving safe category roles), saves their exact role state to a local ledger (`prison_data.json`), and assigns the Inmate role. Releasing the user automatically restores their original roles.
- **Evasion Prevention**: If an imprisoned user leaves and rejoins the server to bypass their lockdown, the bot detects their return, strips any newly acquired onboarding roles, throws them back into prison, and announces the evasion attempt in the general channel.
- **"Unc" Misclick Dynasty**: Detects users who misclick voice channels (joining and leaving within 3 seconds) and assigns them the "Unc" role. Includes a 5-tier progression system (Unc ➔ Grand Unc ➔ Great-Grand Unc ➔ Ancestral Unc ➔ The First Unc) tracked persistently via `unc_data.json`, protected by a 1-hour anti-spam buffer zone.
- **Trust System (Anti-Raid)**: Protects the server by automatically awarding a "Trusted Media" role once a user reaches 30 valid chat interactions. Features a 60-second cooldown between counted messages to prevent spamming, ensuring the role is only granted after genuine, sustained engagement.

### 📈 Analytics
*Note: Scrapes message history to track server activity over time. Excludes channels ending in `logs`.*
- **`!topusers [year] <month>`**: Shows the top 10 most active members for a given year or specific month, caching data locally for speed.
- **`!activity30`**: Generates 30-day hourly and daily server-wide message graphs.
- **`!archive`**: Deep scans and archives message counts for the past 12 months.
- **`!pronounstats <year>`**: Graphs demographics for active users by pronoun roles.

### 🤖 Auto Responder & Core Functions
- **Auto Responder**: Tracks user greetings (greets users once ever via local storage) and handles specific regex trigger queries (e.g., dynamically catching variations of "what is vo2 max?").
- **Dynamic Help (`!help`)**: A custom embed help command that displays all available commands, their arguments, and whether they are currently `Active` or `Disabled` based on the `.env` configuration.
- **Admin Restart (`!restart`)**: Allows admins to trigger a container reboot and pull the latest code directly from Discord.
- **Global Error Handling**: Intercepts command errors, missing arguments, and internal backend crashes (e.g., `UnboundLocalError`, `Forbidden`), sending formatted error feedback directly to the Discord channel instead of just the console.

## Setup Instructions

1. **Install Dependencies:**
   Ensure you have Python 3.14+ installed (compatible with Wispbyte Premium `parkervcp/yolks:python_3.14`), then run:
   ```bash
   pip install -r requirements.txt
   ```

2. **Environment Variables (`.env`):**
   Create a `.env` file in the root directory to configure the bot and toggle specific modules. Example:
   ```env
   BOT_TOKEN=your_bot_token_here
   
   # Module Toggles
   ENABLE_MODERATION=True
   ENABLE_ANALYTICS=True
   ENABLE_AUTO_RESPONDER=True
   
   # Command Toggles
   ENABLE_CMD_PRISON=True
   ENABLE_CMD_TOPUSERS=True
   ENABLE_CMD_ACTIVITY30=True
   ENABLE_CMD_ARCHIVE=True
   ENABLE_CMD_PRONOUNSTATS=True
   
   # Feature Toggles
   ENABLE_RESP_VO2MAX=True
   ```

3. **Discord Portal Configuration:**
   - Enable **Message Content Intent** and **Server Members Intent** in the Discord Developer Portal.
   - Ensure the bot's highest role is placed **above** the Inmate role, Unc roles, the Trusted Media role, and any user roles it needs to manage to prevent `403 Forbidden` errors.
   - Ensure the bot has the **Manage Roles** permission.

4. **Run the Bot:**
   ```bash
   python main.py
   ```