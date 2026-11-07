"""
Unit and integration tests for time_utils.py and api.py.
"""

import unittest
from unittest.mock import patch
import sqlite3
from flask import Flask

import config
import db
import time_utils
from api import api_bp, _sync_lock, _progress


class TestTimeUtils(unittest.TestCase):

    def test_ist_today(self):
        today = time_utils.ist_today()
        self.assertRegex(today, r"^\d{4}-\d{2}-\d{2}$")

    def test_is_valid_date_str(self):
        self.assertTrue(time_utils.is_valid_date_str("2026-09-14"))
        self.assertFalse(time_utils.is_valid_date_str("2026-02-30"))
        self.assertFalse(time_utils.is_valid_date_str("invalid"))
        self.assertFalse(time_utils.is_valid_date_str(None))

    def test_date_str_to_ist_epoch(self):
        # 2026-09-14 00:00:00 IST = 1789324200000 ms
        start_ms = time_utils.date_str_to_ist_epoch("2026-09-14", end_of_day=False)
        self.assertEqual(start_ms, config.CUTOFF_EPOCH_MS)

        end_ms = time_utils.date_str_to_ist_epoch("2026-09-14", end_of_day=True)
        # 23:59:59.999 is 86399999 ms later
        self.assertEqual(end_ms - start_ms, 86_399_999)

    def test_epoch_to_ist_str_and_day_of(self):
        date_str = time_utils.epoch_to_ist_str(config.CUTOFF_EPOCH_MS)
        self.assertEqual(date_str, "2026-09-14")
        self.assertEqual(time_utils.ist_day_of(config.CUTOFF_EPOCH_MS), "2026-09-14")

    def test_validate_and_clamp_range(self):
        today = time_utils.ist_today()

        # Missing params
        with self.assertRaises(ValueError) as ctx:
            time_utils.validate_and_clamp_range(None, today)
        self.assertIn("missing from or to", str(ctx.exception))

        # Invalid format
        with self.assertRaises(ValueError) as ctx:
            time_utils.validate_and_clamp_range("not-a-date", today)
        self.assertIn("invalid date format", str(ctx.exception))

        # Clamping earlier from up to CUTOFF_DATE
        c_from, c_to, f_ts, t_ts = time_utils.validate_and_clamp_range("2026-01-01", today)
        self.assertEqual(c_from, config.CUTOFF_DATE)
        self.assertEqual(c_to, today)

        # Clamping future to down to today
        c_from, c_to, f_ts, t_ts = time_utils.validate_and_clamp_range("2026-09-14", "2099-12-31")
        self.assertEqual(c_to, today)

        # Invalid range after clamping (from > to)
        with self.assertRaises(ValueError) as ctx:
            # from clamped to 2026-09-14, to is 2026-09-10 -> 2026-09-14 > 2026-09-10
            time_utils.validate_and_clamp_range("2026-09-01", "2026-09-10")
        self.assertIn("invalid range: from > to", str(ctx.exception))


class TestApiRoutes(unittest.TestCase):

    def setUp(self):
        self.app = Flask(__name__)
        self.app.register_blueprint(api_bp)
        self.client = self.app.test_client()

        # Use in-memory SQLite DB
        self.db_conn = sqlite3.connect(":memory:")
        self.db_conn.row_factory = sqlite3.Row
        with open(config.SCHEMA_PATH, "r") as f:
            self.db_conn.executescript(f.read())

        # Seed meta schema_version
        self.db_conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '1')")
        self.db_conn.commit()

        # Patch get_db to return our in-memory connection
        self.patcher = patch("db.get_db", return_value=self.db_conn)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.db_conn.close()

    def test_get_meta_empty(self):
        res = self.client.get("/api/meta")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIsNone(data["last_synced_at"])
        self.assertEqual(data["order_count"], 0)
        self.assertEqual(data["position_count"], 0)
        self.assertEqual(data["schema_version"], 1)

    def test_get_positions_validation(self):
        # Missing from / to
        res = self.client.get("/api/positions")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json(), {"error": "missing from or to"})

        # Invalid date format
        res = self.client.get("/api/positions?from=2026/09/14&to=2026-11-06")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json(), {"error": "invalid date format"})

        # Invalid range after clamping
        res = self.client.get("/api/positions?from=2026-09-01&to=2026-09-10")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json(), {"error": "invalid range: from > to"})

        # Valid range
        res = self.client.get("/api/positions?from=2026-09-14&to=2026-11-06")
        self.assertEqual(res.status_code, 200)
        self.assertIsInstance(res.get_json(), list)

    def test_get_positions_data(self):
        # Insert a position within range and one outside range
        # 2026-09-15 12:00:00 IST
        ts_in = time_utils.date_str_to_ist_epoch("2026-09-15") + 12 * 3600 * 1000
        # 2026-08-01 12:00:00 IST (outside default cutoff)
        ts_out = time_utils.date_str_to_ist_epoch("2026-08-01") + 12 * 3600 * 1000

        self.db_conn.execute(
            """
            INSERT INTO positions (
                position_id, symbol, direction, leverage,
                open_time, close_time, entry_price, exit_price,
                quantity, fees, gross_pnl, net_pnl, note, rebuilt_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("pos_1", "BTC-USDT", "LONG", 10, ts_in - 1000, ts_in, 100.0, 110.0, 1.0, 0.1, 10.0, 9.9, None, 12345)
        )
        self.db_conn.execute(
            """
            INSERT INTO positions (
                position_id, symbol, direction, leverage,
                open_time, close_time, entry_price, exit_price,
                quantity, fees, gross_pnl, net_pnl, note, rebuilt_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            ("pos_2", "BTC-USDT", "SHORT", 10, ts_out - 1000, ts_out, 100.0, 90.0, 1.0, 0.1, 10.0, 9.9, None, 12345)
        )
        self.db_conn.commit()

        res = self.client.get("/api/positions?from=2026-09-14&to=2026-09-20")
        self.assertEqual(res.status_code, 200)
        positions = res.get_json()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]["position_id"], "pos_1")
        self.assertEqual(positions[0]["net_pnl"], 9.9)

    def test_get_balance_empty_and_populated(self):
        # Empty
        res = self.client.get("/api/balance")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json(), {"balance": None})

        # Populated
        self.db_conn.execute(
            "INSERT INTO balance_snapshots (ts, asset, balance, equity, unrealized_profit) "
            "VALUES (?, ?, ?, ?, ?)",
            (1730000000000, "USDT", 194.8212, 196.7431, 1.9219)
        )
        self.db_conn.commit()

        res = self.client.get("/api/balance")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["balance"], 194.8212)
        self.assertEqual(data["equity"], 196.7431)
        self.assertEqual(data["unrealized_profit"], 1.9219)
        self.assertEqual(data["ts"], 1730000000000)

    def test_post_sync_and_status(self):
        # Test 409 when lock is held
        _sync_lock.acquire()
        try:
            res = self.client.post("/api/sync")
            self.assertEqual(res.status_code, 409)
            self.assertEqual(res.get_json(), {"error": "sync already in progress"})
        finally:
            _sync_lock.release()

        # Test 202 when started
        with patch("sync.run", return_value={"inserted_orders": 0}):
            res = self.client.post("/api/sync")
            self.assertEqual(res.status_code, 202)
            self.assertEqual(res.get_json(), {"started": True})

        # Test sync status
        res = self.client.get("/api/sync/status")
        self.assertEqual(res.status_code, 200)
        status_data = res.get_json()
        self.assertIn("running", status_data)
        self.assertIn("window_index", status_data)
        self.assertIn("window_total", status_data)


if __name__ == "__main__":
    unittest.main()
