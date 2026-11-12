import os
import json
import sqlite3
import asyncio
from datetime import datetime, timezone
from typing import Optional, Dict, List, Tuple, Any

DEFAULT_DB_PATH = os.getenv("DATABASE_PATH", "bot_database.db")


class Database:
    """Async thread-safe SQLite database manager for analytics and moderation."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._lock = asyncio.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=20.0,
            )
            self._conn.row_factory = sqlite3.Row
            # Configure high-performance concurrency pragmas
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA busy_timeout = 5000;")
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.execute("PRAGMA synchronous = NORMAL;")
            self._sync_init_schema()
        return self._conn

    async def initialize(self) -> None:
        """Initializes tables and executes schema migrations if needed."""
        async with self._lock:
            await asyncio.to_thread(self._sync_init_schema)

    def _sync_init_schema(self) -> None:
        conn = self._get_connection()
        with conn:
            # 1. Monthly User Activity (Top users & Pronoun stats)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_monthly_activity (
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (year, month, user_id)
                );
            """)

            # 2. Month Scrape Metadata
            conn.execute("""
                CREATE TABLE IF NOT EXISTS month_metadata (
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    is_complete INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (year, month)
                );
            """)

            # 3. Rolling 30-Day Activity (activity30)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS activity_hourly (
                    date_hour TEXT PRIMARY KEY,
                    message_count INTEGER NOT NULL DEFAULT 0
                );
            """)

            # 4. Channel Watermark Synchronization
            conn.execute("""
                CREATE TABLE IF NOT EXISTS channel_sync (
                    channel_id INTEGER PRIMARY KEY,
                    last_message_id INTEGER NOT NULL,
                    last_updated_at TEXT NOT NULL
                );
            """)

            # 5. Rolling 30-Day Emoji Usage (!myemojis & !emojileaderboard)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS emoji_daily_usage (
                    date TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    user_name TEXT NOT NULL,
                    emoji TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (date, user_id, emoji)
                );
            """)

            # 6. Moderation: Prison Island
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prison_records (
                    user_id INTEGER PRIMARY KEY,
                    roles TEXT NOT NULL,
                    jailed_at TEXT NOT NULL
                );
            """)

            # 6. Moderation: Unc Role Hierarchy
            conn.execute("""
                CREATE TABLE IF NOT EXISTS unc_records (
                    user_id INTEGER PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0
                );
            """)

            # 6b. Moderation: Unc anti-abuse state (cooldown, lockout) and short-stay history
            conn.execute("""
                CREATE TABLE IF NOT EXISTS unc_state (
                    user_id INTEGER PRIMARY KEY,
                    last_counted REAL,
                    locked_until REAL
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS unc_short_stays (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    joined_at REAL NOT NULL,
                    left_at REAL NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_unc_short_stays_user
                ON unc_short_stays (user_id, left_at);
            """)

            # 7. Moderation: Trusted Media Interaction Points
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trust_records (
                    user_id INTEGER PRIMARY KEY,
                    points REAL NOT NULL DEFAULT 0.0
                );
            """)

            # 8. Moderation: Quarantine System
            conn.execute("""
                CREATE TABLE IF NOT EXISTS quarantine_records (
                    user_id INTEGER PRIMARY KEY,
                    roles TEXT NOT NULL,
                    quarantined_at TEXT NOT NULL,
                    reason TEXT,
                    type TEXT DEFAULT 'account_age'
                );
            """)

            # 9. System & Bot Metadata
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bot_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

            # Indexing for high-speed queries
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_monthly_user_year_month ON user_monthly_activity(year, month);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_monthly_user_count ON user_monthly_activity(message_count DESC);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emoji_date ON emoji_daily_usage(date);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emoji_user_date ON emoji_daily_usage(user_id, date);"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_emoji_name_date ON emoji_daily_usage(emoji, date);"
            )

    async def close(self) -> None:
        """Closes the underlying database connection."""
        async with self._lock:
            if self._conn:
                await asyncio.to_thread(self._conn.close)
                self._conn = None

    # ---------------------------------------------------------
    # LEGACY JSON MIGRATION
    # ---------------------------------------------------------
    async def auto_migrate_legacy_json(self) -> None:
        """Scans for legacy JSON data files and migrates their contents to SQLite."""
        async with self._lock:
            await asyncio.to_thread(self._sync_auto_migrate)

    def _sync_auto_migrate(self) -> None:
        conn = self._get_connection()

        # 1. Prison data migration
        if os.path.exists("prison_data.json"):
            try:
                with open("prison_data.json", "r", encoding="utf-8") as f:
                    p_data = json.load(f)
                now_iso = datetime.now(timezone.utc).isoformat()
                with conn:
                    for uid_str, roles in p_data.items():
                        uid = int(uid_str)
                        roles_json = json.dumps(roles if isinstance(roles, list) else [])
                        conn.execute(
                            "INSERT OR REPLACE INTO prison_records (user_id, roles, jailed_at) VALUES (?, ?, ?);",
                            (uid, roles_json, now_iso),
                        )
                os.rename("prison_data.json", "prison_data.json.migrated")
                print("📦 Migrated prison_data.json to SQLite database.")
            except Exception as e:
                print(f"⚠️ Failed to migrate prison_data.json: {e}")

        # 2. Unc data migration
        if os.path.exists("unc_data.json"):
            try:
                with open("unc_data.json", "r", encoding="utf-8") as f:
                    unc_data = json.load(f)
                with conn:
                    for uid_str, count in unc_data.items():
                        conn.execute(
                            "INSERT OR REPLACE INTO unc_records (user_id, count) VALUES (?, ?);",
                            (int(uid_str), int(count)),
                        )
                os.rename("unc_data.json", "unc_data.json.migrated")
                print("📦 Migrated unc_data.json to SQLite database.")
            except Exception as e:
                print(f"⚠️ Failed to migrate unc_data.json: {e}")

        # 3. Trust data migration
        if os.path.exists("trust_data.json"):
            try:
                with open("trust_data.json", "r", encoding="utf-8") as f:
                    trust_data = json.load(f)
                with conn:
                    for uid_str, points in trust_data.items():
                        conn.execute(
                            "INSERT OR REPLACE INTO trust_records (user_id, points) VALUES (?, ?);",
                            (int(uid_str), float(points)),
                        )
                os.rename("trust_data.json", "trust_data.json.migrated")
                print("📦 Migrated trust_data.json to SQLite database.")
            except Exception as e:
                print(f"⚠️ Failed to migrate trust_data.json: {e}")

        # 4. Quarantine data migration
        if os.path.exists("quarantine_data.json"):
            try:
                with open("quarantine_data.json", "r", encoding="utf-8") as f:
                    q_data = json.load(f)
                now_iso = datetime.now(timezone.utc).isoformat()
                with conn:
                    for uid_str, entry in q_data.items():
                        uid = int(uid_str)
                        if isinstance(entry, dict):
                            roles = entry.get("roles", [])
                            q_at = entry.get("quarantined_at", now_iso)
                            reason = entry.get("reason", "Migrated legacy quarantine")
                            q_type = entry.get("type", "account_age")
                        else:
                            roles = entry if isinstance(entry, list) else []
                            q_at = now_iso
                            reason = "Migrated legacy quarantine"
                            q_type = "account_age"

                        conn.execute(
                            """
                            INSERT OR REPLACE INTO quarantine_records
                            (user_id, roles, quarantined_at, reason, type)
                            VALUES (?, ?, ?, ?, ?);
                            """,
                            (uid, json.dumps(roles), q_at, reason, q_type),
                        )
                os.rename("quarantine_data.json", "quarantine_data.json.migrated")
                print("📦 Migrated quarantine_data.json to SQLite database.")
            except Exception as e:
                print(f"⚠️ Failed to migrate quarantine_data.json: {e}")

        # 5. Monthly Stats archives migration
        stats_dir = "monthly_stats"
        if os.path.isdir(stats_dir):
            for fname in os.listdir(stats_dir):
                if fname.startswith("stats_") and fname.endswith(".json"):
                    fpath = os.path.join(stats_dir, fname)
                    try:
                        # Format: stats_YYYY_MM.json
                        parts = fname.replace(".json", "").split("_")
                        if len(parts) >= 3:
                            year = int(parts[1])
                            month = int(parts[2])
                        else:
                            continue

                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)

                        is_complete = 1 if data.get("complete", False) else 0

                        with conn:
                            conn.execute(
                                "INSERT OR REPLACE INTO month_metadata (year, month, is_complete) VALUES (?, ?, ?);",
                                (year, month, is_complete),
                            )
                            for uid_str, uinfo in data.get("users", {}).items():
                                uid = int(uid_str)
                                uname = uinfo.get("name", "Unknown User")
                                total_msgs = sum(uinfo.get("channels", {}).values())
                                conn.execute(
                                    """
                                    INSERT INTO user_monthly_activity (year, month, user_id, user_name, message_count)
                                    VALUES (?, ?, ?, ?, ?)
                                    ON CONFLICT(year, month, user_id) DO UPDATE SET
                                        user_name = excluded.user_name,
                                        message_count = excluded.message_count;
                                    """,
                                    (year, month, uid, uname, total_msgs),
                                )
                        os.rename(fpath, fpath + ".migrated")
                        print(f"📦 Migrated {fname} to SQLite database.")
                    except Exception as e:
                        print(f"⚠️ Failed to migrate {fname}: {e}")

    # ---------------------------------------------------------
    # ANALYTICS OPERATIONS
    # ---------------------------------------------------------
    async def record_messages_batch(
        self,
        user_increments: Dict[Tuple[int, int, int, str], int],
        hourly_increments: Dict[str, int],
        channel_sync_updates: Dict[int, Tuple[int, str]],
    ) -> None:
        """Applies an in-memory buffer of message counts atomically."""
        if not user_increments and not hourly_increments and not channel_sync_updates:
            return

        async with self._lock:
            await asyncio.to_thread(
                self._sync_record_messages_batch,
                user_increments,
                hourly_increments,
                channel_sync_updates,
            )

    def _sync_record_messages_batch(
        self,
        user_increments: Dict[Tuple[int, int, int, str], int],
        hourly_increments: Dict[str, int],
        channel_sync_updates: Dict[int, Tuple[int, str]],
    ) -> None:
        conn = self._get_connection()
        with conn:
            # 1. Update user monthly counts
            for (year, month, uid, uname), count in user_increments.items():
                conn.execute(
                    """
                    INSERT INTO user_monthly_activity (year, month, user_id, user_name, message_count)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(year, month, user_id) DO UPDATE SET
                        user_name = excluded.user_name,
                        message_count = user_monthly_activity.message_count + excluded.message_count;
                    """,
                    (year, month, uid, uname, count),
                )

            # 2. Update hourly activity
            for date_hour, count in hourly_increments.items():
                conn.execute(
                    """
                    INSERT INTO activity_hourly (date_hour, message_count)
                    VALUES (?, ?)
                    ON CONFLICT(date_hour) DO UPDATE SET
                        message_count = activity_hourly.message_count + excluded.message_count;
                    """,
                    (date_hour, count),
                )

            # 3. Update channel watermarks
            for cid, (last_mid, timestamp_str) in channel_sync_updates.items():
                conn.execute(
                    """
                    INSERT INTO channel_sync (channel_id, last_message_id, last_updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(channel_id) DO UPDATE SET
                        last_message_id = excluded.last_message_id,
                        last_updated_at = excluded.last_updated_at;
                    """,
                    (cid, last_mid, timestamp_str),
                )

    async def prune_activity_hourly(self, cutoff_date_hour: str) -> int:
        """Deletes hourly activity records older than cutoff_date_hour (e.g. 30 days ago)."""
        async with self._lock:
            return await asyncio.to_thread(self._sync_prune_activity_hourly, cutoff_date_hour)

    def _sync_prune_activity_hourly(self, cutoff_date_hour: str) -> int:
        conn = self._get_connection()
        with conn:
            cur = conn.execute(
                "DELETE FROM activity_hourly WHERE date_hour < ?;",
                (cutoff_date_hour,),
            )
            return cur.rowcount

    async def get_top_users(
        self, year: int, month: Optional[int] = None, limit: int = 10
    ) -> List[Tuple[str, int]]:
        """Returns top users sorted by message count for a given year or specific month."""
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_top_users, year, month, limit)

    def _sync_get_top_users(
        self, year: int, month: Optional[int], limit: int
    ) -> List[Tuple[str, int]]:
        conn = self._get_connection()
        if month is not None:
            cur = conn.execute(
                """
                SELECT user_name, message_count
                FROM user_monthly_activity
                WHERE year = ? AND month = ?
                ORDER BY message_count DESC
                LIMIT ?;
                """,
                (year, month, limit),
            )
            return [(row["user_name"], row["message_count"]) for row in cur.fetchall()]
        else:
            cur = conn.execute(
                """
                SELECT user_name, SUM(message_count) as total
                FROM user_monthly_activity
                WHERE year = ?
                GROUP BY user_id
                ORDER BY total DESC
                LIMIT ?;
                """,
                (year, limit),
            )
            return [(row["user_name"], row["total"]) for row in cur.fetchall()]

    async def get_active_users_for_year(self, year: int, min_messages: int) -> List[int]:
        """Returns distinct user IDs who sent at least min_messages in any month of the year."""
        async with self._lock:
            return await asyncio.to_thread(
                self._sync_get_active_users_for_year, year, min_messages
            )

    def _sync_get_active_users_for_year(self, year: int, min_messages: int) -> List[int]:
        conn = self._get_connection()
        cur = conn.execute(
            """
            SELECT DISTINCT user_id
            FROM user_monthly_activity
            WHERE year = ? AND message_count >= ?;
            """,
            (year, min_messages),
        )
        return [row["user_id"] for row in cur.fetchall()]

    async def get_activity_30_days(self, start_date_hour: str) -> List[Tuple[str, int]]:
        """Fetches all hourly buckets from start_date_hour onwards."""
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_activity_30_days, start_date_hour)

    def _sync_get_activity_30_days(self, start_date_hour: str) -> List[Tuple[str, int]]:
        conn = self._get_connection()
        cur = conn.execute(
            """
            SELECT date_hour, message_count
            FROM activity_hourly
            WHERE date_hour >= ?
            ORDER BY date_hour ASC;
            """,
            (start_date_hour,),
        )
        return [(row["date_hour"], row["message_count"]) for row in cur.fetchall()]

    async def get_monthly_totals(
        self, months: List[Tuple[int, int]]
    ) -> Dict[Tuple[int, int], int]:
        """Returns a mapping of (year, month) -> total message count across all users."""
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_monthly_totals, months)

    def _sync_get_monthly_totals(
        self, months: List[Tuple[int, int]]
    ) -> Dict[Tuple[int, int], int]:
        conn = self._get_connection()
        result: Dict[Tuple[int, int], int] = {m: 0 for m in months}
        if not months:
            return result

        placeholders = " OR ".join(["(year = ? AND month = ?)"] * len(months))
        params: List[int] = [item for m in months for item in m]
        cur = conn.execute(
            f"""
            SELECT year, month, SUM(message_count) as total
            FROM user_monthly_activity
            WHERE {placeholders}
            GROUP BY year, month;
            """,
            params,
        )
        for row in cur.fetchall():
            result[(row["year"], row["month"])] = row["total"] or 0
        return result

    async def is_month_complete(self, year: int, month: int) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._sync_is_month_complete, year, month)

    def _sync_is_month_complete(self, year: int, month: int) -> bool:
        conn = self._get_connection()
        cur = conn.execute(
            "SELECT is_complete FROM month_metadata WHERE year = ? AND month = ?;",
            (year, month),
        )
        row = cur.fetchone()
        return bool(row["is_complete"]) if row else False

    async def set_month_complete(self, year: int, month: int, is_complete: bool = True) -> None:
        async with self._lock:
            await asyncio.to_thread(self._sync_set_month_complete, year, month, is_complete)

    def _sync_set_month_complete(self, year: int, month: int, is_complete: bool) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO month_metadata (year, month, is_complete)
                VALUES (?, ?, ?)
                ON CONFLICT(year, month) DO UPDATE SET is_complete = excluded.is_complete;
                """,
                (year, month, 1 if is_complete else 0),
            )

    async def get_all_channel_syncs(self) -> Dict[int, int]:
        """Returns a mapping of channel_id -> last_message_id."""
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_all_channel_syncs)

    def _sync_get_all_channel_syncs(self) -> Dict[int, int]:
        conn = self._get_connection()
        cur = conn.execute("SELECT channel_id, last_message_id FROM channel_sync;")
        return {row["channel_id"]: row["last_message_id"] for row in cur.fetchall()}

    async def clear_month_user_activity(self, year: int, month: int) -> None:
        """Clears user monthly activity for a specific year and month (used before backfilling current month)."""
        async with self._lock:
            await asyncio.to_thread(self._sync_clear_month_user_activity, year, month)

    def _sync_clear_month_user_activity(self, year: int, month: int) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                "DELETE FROM user_monthly_activity WHERE year = ? AND month = ?;",
                (year, month),
            )

    async def get_metadata(self, key: str) -> Optional[str]:
        """Gets a string value from bot_metadata."""
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_metadata, key)

    def _sync_get_metadata(self, key: str) -> Optional[str]:
        conn = self._get_connection()
        cur = conn.execute("SELECT value FROM bot_metadata WHERE key = ?;", (key,))
        row = cur.fetchone()
        return row["value"] if row else None

    async def set_metadata(self, key: str, value: str) -> None:
        """Sets a string value in bot_metadata."""
        async with self._lock:
            await asyncio.to_thread(self._sync_set_metadata, key, value)

    def _sync_set_metadata(self, key: str, value: str) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO bot_metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """,
                (key, value),
            )

    async def is_initial_sync_done(self) -> bool:
        """Checks if the one-time 30-day baseline backfill has been completed."""
        val = await self.get_metadata("initial_30d_sync_done")
        return val == "1"

    async def set_initial_sync_done(self, done: bool = True) -> None:
        """Sets whether the one-time 30-day baseline backfill is marked complete."""
        await self.set_metadata("initial_30d_sync_done", "1" if done else "0")

    # ---------------------------------------------------------
    # EMOJI ANALYTICS (!myemojis & !emojileaderboard)
    # ---------------------------------------------------------
    async def record_emoji_batch(
        self, emoji_increments: Dict[Tuple[str, int, str, str], int]
    ) -> None:
        """Records a batch of (date_str, user_id, user_name, emoji) -> count."""
        if not emoji_increments:
            return
        async with self._lock:
            await asyncio.to_thread(self._sync_record_emoji_batch, emoji_increments)

    def _sync_record_emoji_batch(
        self, emoji_increments: Dict[Tuple[str, int, str, str], int]
    ) -> None:
        conn = self._get_connection()
        with conn:
            for (date_str, uid, uname, emoji), count in emoji_increments.items():
                conn.execute(
                    """
                    INSERT INTO emoji_daily_usage (date, user_id, user_name, emoji, count)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(date, user_id, emoji) DO UPDATE SET
                        user_name = excluded.user_name,
                        count = emoji_daily_usage.count + excluded.count;
                    """,
                    (date_str, uid, uname, emoji, count),
                )

    async def get_user_top_emojis(
        self, user_id: int, start_date: str, limit: int = 10
    ) -> Tuple[List[Tuple[str, int]], int, int]:
        """Returns (top_emojis, total_unique, total_uses) for a user from start_date."""
        async with self._lock:
            return await asyncio.to_thread(
                self._sync_get_user_top_emojis, user_id, start_date, limit
            )

    def _sync_get_user_top_emojis(
        self, user_id: int, start_date: str, limit: int
    ) -> Tuple[List[Tuple[str, int]], int, int]:
        conn = self._get_connection()
        cur = conn.execute(
            """
            SELECT emoji, SUM(count) as total
            FROM emoji_daily_usage
            WHERE user_id = ? AND date >= ?
            GROUP BY emoji
            ORDER BY total DESC;
            """,
            (user_id, start_date),
        )
        all_rows = cur.fetchall()
        top_emojis = [(row["emoji"], row["total"]) for row in all_rows[:limit]]
        total_unique = len(all_rows)
        total_uses = sum(row["total"] for row in all_rows)
        return top_emojis, total_unique, total_uses

    async def get_server_emoji_leaderboard(
        self, start_date: str, limit: int = 15
    ) -> Tuple[List[Tuple[str, int, str, int]], int, int]:
        """Returns (leaderboard, total_unique, total_uses) server-wide from start_date."""
        async with self._lock:
            return await asyncio.to_thread(
                self._sync_get_server_emoji_leaderboard, start_date, limit
            )

    def _sync_get_server_emoji_leaderboard(
        self, start_date: str, limit: int
    ) -> Tuple[List[Tuple[str, int, str, int]], int, int]:
        conn = self._get_connection()
        # 1. Top emojis across server
        cur = conn.execute(
            """
            SELECT emoji, SUM(count) as total
            FROM emoji_daily_usage
            WHERE date >= ?
            GROUP BY emoji
            ORDER BY total DESC
            LIMIT ?;
            """,
            (start_date, limit),
        )
        top_rows = cur.fetchall()

        leaderboard = []
        for row in top_rows:
            emoji = row["emoji"]
            total = row["total"]
            # Find top user for this emoji
            u_cur = conn.execute(
                """
                SELECT user_name, SUM(count) as user_total
                FROM emoji_daily_usage
                WHERE emoji = ? AND date >= ?
                GROUP BY user_id
                ORDER BY user_total DESC
                LIMIT 1;
                """,
                (emoji, start_date),
            )
            top_user_row = u_cur.fetchone()
            top_uname = top_user_row["user_name"] if top_user_row else "Unknown User"
            top_ucount = top_user_row["user_total"] if top_user_row else 0
            leaderboard.append((emoji, total, top_uname, top_ucount))

        # Overall server totals
        tot_cur = conn.execute(
            """
            SELECT COUNT(DISTINCT emoji) as unique_emojis, COALESCE(SUM(count), 0) as total_uses
            FROM emoji_daily_usage
            WHERE date >= ?;
            """,
            (start_date,),
        )
        tot_row = tot_cur.fetchone()
        unique_emojis = tot_row["unique_emojis"] if tot_row else 0
        total_uses = tot_row["total_uses"] if tot_row else 0

        return leaderboard, unique_emojis, total_uses

    async def prune_emoji_daily_usage(self, cutoff_date: str) -> int:
        """Deletes emoji usage records older than cutoff_date."""
        async with self._lock:
            return await asyncio.to_thread(self._sync_prune_emoji_daily_usage, cutoff_date)

    def _sync_prune_emoji_daily_usage(self, cutoff_date: str) -> int:
        conn = self._get_connection()
        with conn:
            cur = conn.execute(
                "DELETE FROM emoji_daily_usage WHERE date < ?;",
                (cutoff_date,),
            )
            return cur.rowcount

    # ---------------------------------------------------------
    # MODERATION: PRISON
    # ---------------------------------------------------------
    async def get_prison_record(self, user_id: int) -> Optional[List[int]]:
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_prison_record, user_id)

    def _sync_get_prison_record(self, user_id: int) -> Optional[List[int]]:
        conn = self._get_connection()
        cur = conn.execute("SELECT roles FROM prison_records WHERE user_id = ?;", (user_id,))
        row = cur.fetchone()
        if not row:
            return None
        return json.loads(row["roles"])

    async def set_prison_record(
        self, user_id: int, roles: List[int], jailed_at: Optional[str] = None
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(self._sync_set_prison_record, user_id, roles, jailed_at)

    def _sync_set_prison_record(
        self, user_id: int, roles: List[int], jailed_at: Optional[str]
    ) -> None:
        conn = self._get_connection()
        timestamp = jailed_at or datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """
                INSERT INTO prison_records (user_id, roles, jailed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    roles = excluded.roles,
                    jailed_at = excluded.jailed_at;
                """,
                (user_id, json.dumps(roles), timestamp),
            )

    async def delete_prison_record(self, user_id: int) -> Optional[List[int]]:
        async with self._lock:
            return await asyncio.to_thread(self._sync_delete_prison_record, user_id)

    def _sync_delete_prison_record(self, user_id: int) -> Optional[List[int]]:
        conn = self._get_connection()
        with conn:
            cur = conn.execute("SELECT roles FROM prison_records WHERE user_id = ?;", (user_id,))
            row = cur.fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM prison_records WHERE user_id = ?;", (user_id,))
            return json.loads(row["roles"])

    async def has_prison_record(self, user_id: int) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._sync_has_prison_record, user_id)

    def _sync_has_prison_record(self, user_id: int) -> bool:
        conn = self._get_connection()
        cur = conn.execute("SELECT 1 FROM prison_records WHERE user_id = ?;", (user_id,))
        return cur.fetchone() is not None

    async def get_all_prison_records(self) -> Dict[str, List[int]]:
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_all_prison_records)

    def _sync_get_all_prison_records(self) -> Dict[str, List[int]]:
        conn = self._get_connection()
        cur = conn.execute("SELECT user_id, roles FROM prison_records;")
        return {str(row["user_id"]): json.loads(row["roles"]) for row in cur.fetchall()}

    # ---------------------------------------------------------
    # MODERATION: UNC
    # ---------------------------------------------------------
    async def get_unc_count(self, user_id: int) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_unc_count, user_id)

    def _sync_get_unc_count(self, user_id: int) -> int:
        conn = self._get_connection()
        cur = conn.execute("SELECT count FROM unc_records WHERE user_id = ?;", (user_id,))
        row = cur.fetchone()
        return row["count"] if row else 0

    async def increment_unc_count(self, user_id: int) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._sync_increment_unc_count, user_id)

    def _sync_increment_unc_count(self, user_id: int) -> int:
        conn = self._get_connection()
        with conn:
            cur = conn.execute(
                """
                INSERT INTO unc_records (user_id, count)
                VALUES (?, 1)
                ON CONFLICT(user_id) DO UPDATE SET count = unc_records.count + 1
                RETURNING count;
                """,
                (user_id,),
            )
            row = cur.fetchone()
            return row["count"]

    async def set_unc_count(self, user_id: int, count: int) -> None:
        async with self._lock:
            await asyncio.to_thread(self._sync_set_unc_count, user_id, count)

    def _sync_set_unc_count(self, user_id: int, count: int) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO unc_records (user_id, count)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET count = excluded.count;
                """,
                (user_id, count),
            )

    async def get_unc_state(self, user_id: int) -> Dict[str, Any]:
        """Returns the last counted time and lockout end for a user."""
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_unc_state, user_id)

    def _sync_get_unc_state(self, user_id: int) -> Dict[str, Any]:
        conn = self._get_connection()
        cur = conn.execute(
            "SELECT last_counted, locked_until FROM unc_state WHERE user_id = ?;",
            (user_id,),
        )
        row = cur.fetchone()
        if row is None:
            return {"last_counted": None, "locked_until": None}
        return {"last_counted": row["last_counted"], "locked_until": row["locked_until"]}

    async def set_unc_last_counted(self, user_id: int, timestamp: float) -> None:
        async with self._lock:
            await asyncio.to_thread(self._sync_set_unc_last_counted, user_id, timestamp)

    def _sync_set_unc_last_counted(self, user_id: int, timestamp: float) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO unc_state (user_id, last_counted)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET last_counted = excluded.last_counted;
                """,
                (user_id, timestamp),
            )

    async def set_unc_lockout(self, user_id: int, locked_until: float) -> None:
        """Sets the lockout end for a user without clearing history."""
        async with self._lock:
            await asyncio.to_thread(self._sync_set_unc_lockout, user_id, locked_until)

    def _sync_set_unc_lockout(self, user_id: int, locked_until: float) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO unc_state (user_id, locked_until)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET locked_until = excluded.locked_until;
                """,
                (user_id, locked_until),
            )

    async def get_unc_short_stays(self, user_id: int, since: float) -> List[Dict[str, Any]]:
        """Returns the user's short stays that ended at or after `since`, oldest first."""
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_unc_short_stays, user_id, since)

    def _sync_get_unc_short_stays(self, user_id: int, since: float) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        cur = conn.execute(
            """
            SELECT channel_id, joined_at, left_at FROM unc_short_stays
            WHERE user_id = ? AND left_at >= ?
            ORDER BY left_at ASC, id ASC;
            """,
            (user_id, since),
        )
        return [
            {
                "channel_id": row["channel_id"],
                "joined_at": row["joined_at"],
                "left_at": row["left_at"],
            }
            for row in cur.fetchall()
        ]

    async def add_unc_short_stay(
        self, user_id: int, channel_id: int, joined_at: float, left_at: float, keep_since: float
    ) -> None:
        """Records a short stay and prunes that user's stays that ended before `keep_since`."""
        async with self._lock:
            await asyncio.to_thread(
                self._sync_add_unc_short_stay, user_id, channel_id, joined_at, left_at, keep_since
            )

    def _sync_add_unc_short_stay(
        self, user_id: int, channel_id: int, joined_at: float, left_at: float, keep_since: float
    ) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                "DELETE FROM unc_short_stays WHERE user_id = ? AND left_at < ?;",
                (user_id, keep_since),
            )
            conn.execute(
                """
                INSERT INTO unc_short_stays (user_id, channel_id, joined_at, left_at)
                VALUES (?, ?, ?, ?);
                """,
                (user_id, channel_id, joined_at, left_at),
            )

    # ---------------------------------------------------------
    # MODERATION: TRUST
    # ---------------------------------------------------------
    async def get_trust_points(self, user_id: int) -> float:
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_trust_points, user_id)

    def _sync_get_trust_points(self, user_id: int) -> float:
        conn = self._get_connection()
        cur = conn.execute("SELECT points FROM trust_records WHERE user_id = ?;", (user_id,))
        row = cur.fetchone()
        return row["points"] if row else 0.0

    async def set_trust_points(self, user_id: int, points: float) -> None:
        async with self._lock:
            await asyncio.to_thread(self._sync_set_trust_points, user_id, points)

    def _sync_set_trust_points(self, user_id: int, points: float) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO trust_records (user_id, points)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET points = excluded.points;
                """,
                (user_id, points),
            )

    async def delete_trust_record(self, user_id: int) -> None:
        async with self._lock:
            await asyncio.to_thread(self._sync_delete_trust_record, user_id)

    def _sync_delete_trust_record(self, user_id: int) -> None:
        conn = self._get_connection()
        with conn:
            conn.execute("DELETE FROM trust_records WHERE user_id = ?;", (user_id,))

    # ---------------------------------------------------------
    # MODERATION: QUARANTINE
    # ---------------------------------------------------------
    async def get_quarantine_record(self, user_id: int) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_quarantine_record, user_id)

    def _sync_get_quarantine_record(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        cur = conn.execute(
            "SELECT roles, quarantined_at, reason, type FROM quarantine_records WHERE user_id = ?;",
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "roles": json.loads(row["roles"]),
            "quarantined_at": row["quarantined_at"],
            "reason": row["reason"],
            "type": row["type"],
        }

    async def set_quarantine_record(
        self,
        user_id: int,
        roles: List[int],
        reason: str = "",
        q_type: str = "account_age",
        quarantined_at: Optional[str] = None,
    ) -> None:
        async with self._lock:
            await asyncio.to_thread(
                self._sync_set_quarantine_record,
                user_id,
                roles,
                reason,
                q_type,
                quarantined_at,
            )

    def _sync_set_quarantine_record(
        self,
        user_id: int,
        roles: List[int],
        reason: str,
        q_type: str,
        quarantined_at: Optional[str],
    ) -> None:
        conn = self._get_connection()
        timestamp = quarantined_at or datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """
                INSERT INTO quarantine_records (user_id, roles, quarantined_at, reason, type)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    roles = excluded.roles,
                    quarantined_at = excluded.quarantined_at,
                    reason = excluded.reason,
                    type = excluded.type;
                """,
                (user_id, json.dumps(roles), timestamp, reason, q_type),
            )

    async def delete_quarantine_record(self, user_id: int) -> Optional[Dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._sync_delete_quarantine_record, user_id)

    def _sync_delete_quarantine_record(self, user_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        with conn:
            cur = conn.execute(
                "SELECT roles, quarantined_at, reason, type FROM quarantine_records WHERE user_id = ?;",
                (user_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            conn.execute("DELETE FROM quarantine_records WHERE user_id = ?;", (user_id,))
            return {
                "roles": json.loads(row["roles"]),
                "quarantined_at": row["quarantined_at"],
                "reason": row["reason"],
                "type": row["type"],
            }

    async def has_quarantine_record(self, user_id: int) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._sync_has_quarantine_record, user_id)

    def _sync_has_quarantine_record(self, user_id: int) -> bool:
        conn = self._get_connection()
        cur = conn.execute("SELECT 1 FROM quarantine_records WHERE user_id = ?;", (user_id,))
        return cur.fetchone() is not None

    async def get_all_quarantine_records(self) -> Dict[str, Dict[str, Any]]:
        async with self._lock:
            return await asyncio.to_thread(self._sync_get_all_quarantine_records)

    def _sync_get_all_quarantine_records(self) -> Dict[str, Dict[str, Any]]:
        conn = self._get_connection()
        cur = conn.execute(
            "SELECT user_id, roles, quarantined_at, reason, type FROM quarantine_records;"
        )
        result = {}
        for row in cur.fetchall():
            result[str(row["user_id"])] = {
                "roles": json.loads(row["roles"]),
                "quarantined_at": row["quarantined_at"],
                "reason": row["reason"],
                "type": row["type"],
            }
        return result


# Singleton global instance
db = Database()
