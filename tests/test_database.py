import os
import sys
import unittest
import tempfile
import shutil
import json
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from database import Database


class TestDatabase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.test_db_path = os.path.join(self.test_dir, "test.db")
        self.db = Database(self.test_db_path)
        await self.db.initialize()

    async def asyncTearDown(self):
        await self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    async def test_monthly_activity_and_top_users(self):
        # Record batch of messages
        user_batch = {
            (2026, 11, 101, "Alice"): 25,
            (2026, 11, 102, "Bob"): 40,
            (2026, 11, 103, "Charlie"): 10,
            (2026, 10, 101, "Alice"): 50,  # Prior month
        }
        await self.db.record_messages_batch(user_batch, {}, {})

        # Query top users for month 11
        top_m11 = await self.db.get_top_users(2026, 11, limit=2)
        self.assertEqual(len(top_m11), 2)
        self.assertEqual(top_m11[0], ("Bob", 40))
        self.assertEqual(top_m11[1], ("Alice", 25))

        # Query yearly total for 2026
        top_year = await self.db.get_top_users(2026, None, limit=2)
        self.assertEqual(top_year[0], ("Alice", 75))  # 25 + 50
        self.assertEqual(top_year[1], ("Bob", 40))

    async def test_pronoun_active_users(self):
        user_batch = {
            (2026, 5, 201, "UserA"): 100,
            (2026, 5, 202, "UserB"): 20,
            (2026, 6, 203, "UserC"): 60,
        }
        await self.db.record_messages_batch(user_batch, {}, {})

        active = await self.db.get_active_users_for_year(2026, min_messages=50)
        self.assertIn(201, active)
        self.assertIn(203, active)
        self.assertNotIn(202, active)

    async def test_activity_hourly_and_pruning(self):
        now = datetime.now(timezone.utc)
        recent_dh = now.strftime("%Y-%m-%d %H")
        old_dh = (now - timedelta(days=35)).strftime("%Y-%m-%d %H")

        hourly_batch = {
            recent_dh: 15,
            old_dh: 30,
        }
        await self.db.record_messages_batch({}, hourly_batch, {})

        # Check rows from 30 days ago
        cutoff_30d = (now - timedelta(days=30)).strftime("%Y-%m-%d %H")
        rows = await self.db.get_activity_30_days(cutoff_30d)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0], (recent_dh, 15))

        # Prune old rows
        deleted = await self.db.prune_activity_hourly(cutoff_30d)
        self.assertEqual(deleted, 1)

    async def test_channel_sync_watermarks(self):
        syncs = {
            111: (999001, "2026-11-06T12:00:00"),
            222: (999002, "2026-11-06T12:01:00"),
        }
        await self.db.record_messages_batch({}, {}, syncs)

        all_syncs = await self.db.get_all_channel_syncs()
        self.assertEqual(all_syncs[111], 999001)
        self.assertEqual(all_syncs[222], 999002)

    async def test_emoji_daily_usage_and_pruning(self):
        now = datetime.now(timezone.utc)
        today_str = now.strftime("%Y-%m-%d")
        old_str = (now - timedelta(days=35)).strftime("%Y-%m-%d")

        emoji_batch = {
            (today_str, 1001, "Alice", "🔥"): 5,
            (today_str, 1001, "Alice", "🎉"): 2,
            (today_str, 1002, "Bob", "🔥"): 8,
            (old_str, 1001, "Alice", "💀"): 10,
        }
        await self.db.record_emoji_batch(emoji_batch)

        # Personal top emojis for Alice
        cutoff_30d = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        top_emojis, unique_count, total_uses = await self.db.get_user_top_emojis(
            1001, cutoff_30d, limit=10
        )
        self.assertEqual(len(top_emojis), 2)
        self.assertEqual(top_emojis[0], ("🔥", 5))
        self.assertEqual(top_emojis[1], ("🎉", 2))
        self.assertEqual(unique_count, 2)
        self.assertEqual(total_uses, 7)

        # Server leaderboard
        lb, s_unique, s_total = await self.db.get_server_emoji_leaderboard(cutoff_30d, limit=15)
        self.assertEqual(len(lb), 2)
        # Top emoji is 🔥 with total 13 (Bob 8, Alice 5)
        self.assertEqual(lb[0], ("🔥", 13, "Bob", 8))
        self.assertEqual(lb[1], ("🎉", 2, "Alice", 2))
        self.assertEqual(s_unique, 2)
        self.assertEqual(s_total, 15)

        # Prune old emoji records
        deleted = await self.db.prune_emoji_daily_usage(cutoff_30d)
        self.assertEqual(deleted, 1)

    async def test_moderation_operations(self):
        # 1. Prison
        self.assertFalse(await self.db.has_prison_record(555))
        await self.db.set_prison_record(555, [1, 2, 3])
        self.assertTrue(await self.db.has_prison_record(555))
        self.assertEqual(await self.db.get_prison_record(555), [1, 2, 3])
        deleted = await self.db.delete_prison_record(555)
        self.assertEqual(deleted, [1, 2, 3])
        self.assertFalse(await self.db.has_prison_record(555))

        # 2. Unc
        self.assertEqual(await self.db.get_unc_count(666), 0)
        c1 = await self.db.increment_unc_count(666)
        self.assertEqual(c1, 1)
        c2 = await self.db.increment_unc_count(666)
        self.assertEqual(c2, 2)

        # 3. Trust
        self.assertEqual(await self.db.get_trust_points(777), 0.0)
        await self.db.set_trust_points(777, 15.5)
        self.assertEqual(await self.db.get_trust_points(777), 15.5)
        await self.db.delete_trust_record(777)
        self.assertEqual(await self.db.get_trust_points(777), 0.0)

        # 4. Quarantine
        self.assertFalse(await self.db.has_quarantine_record(888))
        await self.db.set_quarantine_record(
            888, [10, 20], reason="Bad behavior", q_type="automod"
        )
        self.assertTrue(await self.db.has_quarantine_record(888))
        rec = await self.db.get_quarantine_record(888)
        self.assertEqual(rec["roles"], [10, 20])
        self.assertEqual(rec["type"], "automod")
        self.assertEqual(rec["reason"], "Bad behavior")
        del_rec = await self.db.delete_quarantine_record(888)
        self.assertEqual(del_rec["type"], "automod")
        self.assertFalse(await self.db.has_quarantine_record(888))

    async def test_unc_state_defaults_for_unknown_user(self):
        state = await self.db.get_unc_state(4040)
        self.assertEqual(state, {"last_counted": None, "locked_until": None})

    async def test_unc_last_counted_roundtrip_and_overwrite(self):
        await self.db.set_unc_last_counted(1, 1000.5)
        self.assertEqual((await self.db.get_unc_state(1))["last_counted"], 1000.5)
        await self.db.set_unc_last_counted(1, 2000.25)
        self.assertEqual((await self.db.get_unc_state(1))["last_counted"], 2000.25)
        self.assertIsNone((await self.db.get_unc_state(2))["last_counted"])

    async def test_unc_state_survives_reopening_database(self):
        await self.db.set_unc_last_counted(1, 1234.0)
        await self.db.set_unc_lockout(1, 9999.0)
        await self.db.add_unc_short_stay(1, 10, 1.0, 2.0, 0.0)
        await self.db.close()
        reopened = Database(self.test_db_path)
        try:
            state = await reopened.get_unc_state(1)
            self.assertEqual(state, {"last_counted": 1234.0, "locked_until": 9999.0})
            self.assertEqual(len(await reopened.get_unc_short_stays(1, 0.0)), 1)
        finally:
            await reopened.close()

    async def test_unc_state_table_columns(self):
        conn = self.db._get_connection()
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(unc_state);")]
        self.assertEqual(columns, ["user_id", "last_counted", "locked_until"])

    async def test_unc_short_stays_table_columns(self):
        conn = self.db._get_connection()
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(unc_short_stays);")]
        self.assertIn("user_id", columns)
        self.assertIn("channel_id", columns)
        self.assertIn("joined_at", columns)
        self.assertIn("left_at", columns)

    async def test_unc_lockout_preserves_last_counted_and_vice_versa(self):
        await self.db.set_unc_last_counted(1, 50.0)
        await self.db.set_unc_lockout(1, 500.0)
        await self.db.set_unc_last_counted(1, 60.0)
        self.assertEqual(
            await self.db.get_unc_state(1),
            {"last_counted": 60.0, "locked_until": 500.0},
        )

    async def test_unc_lockout_overwrites_previous_lockout(self):
        await self.db.set_unc_lockout(1, 500.0)
        await self.db.set_unc_lockout(1, 900.0)
        self.assertEqual((await self.db.get_unc_state(1))["locked_until"], 900.0)

    async def test_unc_lockout_creates_state_for_unknown_user(self):
        await self.db.set_unc_lockout(7, 123.0)
        self.assertEqual(
            await self.db.get_unc_state(7),
            {"last_counted": None, "locked_until": 123.0},
        )

    async def test_unc_lockout_does_not_affect_other_users(self):
        await self.db.set_unc_lockout(1, 500.0)
        await self.db.set_unc_lockout(2, 900.0)
        self.assertEqual((await self.db.get_unc_state(1))["locked_until"], 500.0)
        self.assertEqual((await self.db.get_unc_state(2))["locked_until"], 900.0)

    async def test_unc_short_stays_empty_for_unknown_user(self):
        self.assertEqual(await self.db.get_unc_short_stays(1, 0.0), [])

    async def test_unc_short_stays_filtered_by_window_and_user_and_ordered(self):
        await self.db.add_unc_short_stay(1, 11, 300.0, 302.0, 0.0)
        await self.db.add_unc_short_stay(1, 10, 100.0, 101.0, 0.0)
        await self.db.add_unc_short_stay(2, 10, 200.0, 201.0, 0.0)
        both = [
            {"channel_id": 10, "joined_at": 100.0, "left_at": 101.0},
            {"channel_id": 11, "joined_at": 300.0, "left_at": 302.0},
        ]
        self.assertEqual(await self.db.get_unc_short_stays(1, 0.0), both)
        self.assertEqual(await self.db.get_unc_short_stays(1, 101.0), both)
        self.assertEqual(await self.db.get_unc_short_stays(1, 101.5), both[1:])

    async def test_unc_add_short_stay_prunes_only_that_users_expired_rows(self):
        await self.db.add_unc_short_stay(1, 10, 1.0, 2.0, 0.0)
        await self.db.add_unc_short_stay(2, 10, 1.0, 2.0, 0.0)
        await self.db.add_unc_short_stay(1, 10, 500.0, 501.0, 400.0)
        self.assertEqual(
            await self.db.get_unc_short_stays(1, 0.0),
            [{"channel_id": 10, "joined_at": 500.0, "left_at": 501.0}],
        )
        self.assertEqual(len(await self.db.get_unc_short_stays(2, 0.0)), 1)

    async def test_unc_add_short_stay_keeps_row_exactly_at_prune_boundary(self):
        await self.db.add_unc_short_stay(1, 10, 1.0, 400.0, 0.0)
        await self.db.add_unc_short_stay(1, 10, 500.0, 501.0, 400.0)
        self.assertEqual(len(await self.db.get_unc_short_stays(1, 0.0)), 2)

    async def test_unc_lockout_never_deletes_short_stay_history(self):
        await self.db.add_unc_short_stay(1, 10, 1.0, 2.0, 0.0)
        await self.db.add_unc_short_stay(1, 10, 3.0, 4.0, 0.0)
        await self.db.add_unc_short_stay(2, 10, 1.0, 2.0, 0.0)
        await self.db.set_unc_lockout(1, 5000.0)
        await self.db.set_unc_lockout(1, 6000.0)
        self.assertEqual(len(await self.db.get_unc_short_stays(1, 0.0)), 2)
        self.assertEqual(len(await self.db.get_unc_short_stays(2, 0.0)), 1)

    async def test_unc_lockout_does_not_touch_unc_count(self):
        await self.db.increment_unc_count(1)
        await self.db.increment_unc_count(1)
        await self.db.set_unc_lockout(1, 5000.0)
        self.assertEqual(await self.db.get_unc_count(1), 2)

    async def test_bot_metadata_and_clear_activity(self):
        # 1. Metadata tests
        self.assertFalse(await self.db.is_initial_sync_done())
        await self.db.set_metadata("custom_key", "custom_val")
        self.assertEqual(await self.db.get_metadata("custom_key"), "custom_val")

        await self.db.set_initial_sync_done(True)
        self.assertTrue(await self.db.is_initial_sync_done())
        await self.db.set_initial_sync_done(False)
        self.assertFalse(await self.db.is_initial_sync_done())

        # 2. Clear month user activity
        await self.db.record_messages_batch(
            {(2026, 11, 999, "TempUser"): 50, (2026, 10, 999, "TempUser"): 20},
            {},
            {},
        )
        top11 = await self.db.get_top_users(2026, 11)
        self.assertEqual(len(top11), 1)

        await self.db.clear_month_user_activity(2026, 11)
        top11_cleared = await self.db.get_top_users(2026, 11)
        self.assertEqual(len(top11_cleared), 0)

        # Ensure month 10 remains untouched
        top10 = await self.db.get_top_users(2026, 10)
        self.assertEqual(len(top10), 1)

    async def test_get_monthly_totals(self):
        # Insert multi-user message activity across distinct months and years
        user_batch = {
            (2025, 11, 101, "Alice"): 10,
            (2025, 11, 102, "Bob"): 5,
            (2025, 12, 101, "Alice"): 20,
            (2026, 1, 101, "Alice"): 30,
            (2026, 1, 102, "Bob"): 15,
            (2026, 2, 101, "Alice"): 40,
            (2026, 2, 102, "Bob"): 25,
        }
        await self.db.record_messages_batch(user_batch, {}, {})

        # Query range including a month with no recorded activity (2025, 10)
        target_months = [(2025, 10), (2025, 11), (2025, 12), (2026, 1), (2026, 2)]
        totals = await self.db.get_monthly_totals(target_months)

        self.assertEqual(totals[(2025, 10)], 0)
        self.assertEqual(totals[(2025, 11)], 15)
        self.assertEqual(totals[(2025, 12)], 20)
        self.assertEqual(totals[(2026, 1)], 45)
        self.assertEqual(totals[(2026, 2)], 65)

        # Boundary test: empty month list
        empty_totals = await self.db.get_monthly_totals([])
        self.assertEqual(empty_totals, {})


if __name__ == "__main__":
    unittest.main()
