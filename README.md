# Server Management Bot

A modular Discord bot built with `discord.py` that provides chat analytics, automated auto-responses, and voice channel moderation. 

## Features
* **Analytics (`!archive`, `!activity30`, `!topusers`)**: Scrapes message history to track server activity over time. Excludes channels ending in `logs`.
* **Auto Responder**: Tracks user greetings (greets users once ever via local storage) and handles specific regex trigger queries.
* **Moderation**: Detects users who misclick voice channels (join and leave within 3 seconds) and automatically assigns them the "unc" role.

## Setup Instructions

1. **Install Dependencies:**
   Ensure you have Python 3.8+ installed, then run:
   ```bash
   pip install -r requirements.txt