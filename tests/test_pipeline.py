"""
Integration and unit tests for sync.py and positions.py.
"""

import unittest
from unittest.mock import patch
import sqlite3
import config
import db
import positions
import sync


class TestPipeline(unittest.TestCase):

    def test_positions_rebuild(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        with open(config.SCHEMA_PATH, "r") as f:
            conn.executescript(f.read())

        orders = [
            # Fixture 1: Normal position (entry 1.0 @ 100, exit 1.0 @ 110)
            {
                "orderId": 101, "symbol": "BTC-USDT", "positionID": "pos_normal",
                "positionSide": "LONG", "status": "FILLED", "reduceOnly": False,
                "executedQty": 1.0, "avgPrice": 100.0, "profit": 0.0,
                "commission": -0.05, "leverage": 10, "updateTime": 1000,
            },
            {
                "orderId": 102, "symbol": "BTC-USDT", "positionID": "pos_normal",
                "positionSide": "LONG", "status": "FILLED", "reduceOnly": True,
                "executedQty": 1.0, "avgPrice": 110.0, "profit": 10.0,
                "commission": -0.05, "leverage": 10, "updateTime": 2000,
            },
            # Fixture 2: Missing entry order (only exit order)
            {
                "orderId": 201, "symbol": "ETH-USDT", "positionID": "pos_no_entry",
                "positionSide": "SHORT", "status": "FILLED", "reduceOnly": True,
                "executedQty": 2.0, "avgPrice": 2000.0, "profit": 50.0,
                "commission": -0.10, "leverage": 20, "updateTime": 3000,
            },
            # Fixture 3: Partially closed (entry 2.0 @ 50, exit 1.0 @ 60)
            {
                "orderId": 301, "symbol": "SOL-USDT", "positionID": "pos_partial",
                "positionSide": "LONG", "status": "FILLED", "reduceOnly": False,
                "executedQty": 2.0, "avgPrice": 50.0, "profit": 0.0,
                "commission": -0.02, "leverage": 5, "updateTime": 4000,
            },
            {
                "orderId": 302, "symbol": "SOL-USDT", "positionID": "pos_partial",
                "positionSide": "LONG", "status": "FILLED", "reduceOnly": True,
                "executedQty": 1.0, "avgPrice": 60.0, "profit": 10.0,
                "commission": -0.01, "leverage": 5, "updateTime": 5000,
            },
            # Fixture 4: Both flags (entry 0.5 @ 10, exit 1.0 @ 12 -> entry missing + partial close)
            {
                "orderId": 401, "symbol": "DOGE-USDT", "positionID": "pos_both",
                "positionSide": "LONG", "status": "FILLED", "reduceOnly": False,
                "executedQty": 0.5, "avgPrice": 10.0, "profit": 0.0,
                "commission": -0.01, "leverage": 2, "updateTime": 6000,
            },
            {
                "orderId": 402, "symbol": "DOGE-USDT", "positionID": "pos_both",
                "positionSide": "LONG", "status": "FILLED", "reduceOnly": True,
                "executedQty": 1.0, "avgPrice": 12.0, "profit": 2.0,
                "commission": -0.02, "leverage": 2, "updateTime": 7000,
            },
            # Fixture 5: Open position (only entry orders, no exits) -> must be skipped
            {
                "orderId": 501, "symbol": "XRP-USDT", "positionID": "pos_open",
                "positionSide": "LONG", "status": "FILLED", "reduceOnly": False,
                "executedQty": 100.0, "avgPrice": 0.5, "profit": 0.0,
                "commission": -0.05, "leverage": 10, "updateTime": 8000,
            },
        ]

        ins, upd = db.upsert_orders(orders, conn=conn)
        self.assertEqual(ins, 8)
        self.assertEqual(upd, 0)

        result = positions.rebuild(conn)
        self.assertEqual(result.position_count, 4)
        self.assertEqual(result.open_positions, 1)

        rebuilt = db.fetch_positions(conn=conn)
        by_id = {p["position_id"]: p for p in rebuilt}

        self.assertNotIn("pos_open", by_id)

        # Normal position
        p_norm = by_id["pos_normal"]
        self.assertEqual(p_norm["open_time"], 1000)
        self.assertEqual(p_norm["close_time"], 2000)
        self.assertEqual(p_norm["entry_price"], 100.0)
        self.assertEqual(p_norm["exit_price"], 110.0)
        self.assertEqual(p_norm["quantity"], 1.0)
        self.assertAlmostEqual(p_norm["fees"], 0.10, places=4)
        self.assertAlmostEqual(p_norm["gross_pnl"], 10.0, places=4)
        self.assertAlmostEqual(p_norm["net_pnl"], 9.90, places=4)
        self.assertIsNone(p_norm["note"])

        # Missing entry order
        p_no_entry = by_id["pos_no_entry"]
        self.assertIsNone(p_no_entry["open_time"])
        self.assertIsNone(p_no_entry["entry_price"])
        self.assertEqual(p_no_entry["exit_price"], 2000.0)
        self.assertEqual(p_no_entry["quantity"], 2.0)
        self.assertAlmostEqual(p_no_entry["fees"], 0.10, places=4)
        self.assertAlmostEqual(p_no_entry["gross_pnl"], 50.0, places=4)
        self.assertAlmostEqual(p_no_entry["net_pnl"], 49.90, places=4)
        self.assertEqual(p_no_entry["note"], "missing entry order")

        # Partially closed
        p_partial = by_id["pos_partial"]
        self.assertEqual(p_partial["open_time"], 4000)
        self.assertEqual(p_partial["close_time"], 5000)
        self.assertEqual(p_partial["entry_price"], 50.0)
        self.assertEqual(p_partial["exit_price"], 60.0)
        self.assertEqual(p_partial["quantity"], 1.0)
        self.assertEqual(p_partial["note"], "partially closed")

        # Both flags
        p_both = by_id["pos_both"]
        self.assertEqual(p_both["open_time"], 6000)
        self.assertEqual(p_both["close_time"], 7000)
        self.assertEqual(p_both["entry_price"], 10.0)
        self.assertEqual(p_both["exit_price"], 12.0)
        self.assertEqual(p_both["note"], "missing entry order; partially closed")

        conn.close()

    def test_sync_windows_and_range(self):
        now = 10_000_000_000
        start_89d = now - 89 * 24 * 3600 * 1000
        w_89 = sync.build_windows(start_89d, now)
        self.assertEqual(len(w_89), 13)

        start_1d = now - 1 * 24 * 3600 * 1000
        w_1 = sync.build_windows(start_1d, now)
        self.assertEqual(len(w_1), 1)

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        with open(config.SCHEMA_PATH, "r") as f:
            conn.executescript(f.read())

        # First sync range
        s1, e1 = sync.get_sync_range(conn)
        self.assertEqual(s1, config.CUTOFF_EPOCH_MS)

        # Subsequent sync range
        db.set_meta("last_synced_at", str(now), conn=conn)
        s2, e2 = sync.get_sync_range(conn)
        self.assertLess(s2, config.CUTOFF_EPOCH_MS)
        self.assertEqual(s2, e2 - (89 * 24 * 3600 * 1000))

        conn.close()

    def test_sync_pipeline_run(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        with open(config.SCHEMA_PATH, "r") as f:
            conn.executescript(f.read())

        mock_orders = [
            {
                "orderId": 999, "symbol": "BTC-USDT", "positionID": "pos_synced",
                "positionSide": "LONG", "status": "FILLED", "reduceOnly": False,
                "executedQty": 1.0, "avgPrice": 50000.0, "profit": 0.0,
                "commission": -2.0, "leverage": 20, "updateTime": 1000,
            },
            {
                "orderId": 1000, "symbol": "BTC-USDT", "positionID": "pos_synced",
                "positionSide": "LONG", "status": "FILLED", "reduceOnly": True,
                "executedQty": 1.0, "avgPrice": 52000.0, "profit": 2000.0,
                "commission": -2.0, "leverage": 20, "updateTime": 2000,
            },
        ]

        mock_balance = {
            "asset": "USDT",
            "balance": 500.0,
            "equity": 520.0,
            "unrealized_profit": 20.0,
            "ts": 3000,
        }

        progress = {}
        with patch("bingx_client.fetch_orders_window", return_value=mock_orders) as mock_fetch_orders, \
             patch("bingx_client.fetch_balance", return_value=mock_balance) as mock_fetch_bal:

            summary = sync.run(progress=progress, conn=conn)

            self.assertGreater(summary["inserted_orders"], 0)
            self.assertEqual(summary["new_positions"], 1)
            self.assertEqual(summary["errors"], [])
            self.assertFalse(progress["running"])
            self.assertEqual(progress["window_index"], progress["window_total"])

            # Check meta updated
            last_sync = db.get_meta("last_synced_at", conn=conn)
            self.assertIsNotNone(last_sync)

            # Check balance inserted
            bal = db.fetch_balance(conn=conn)
            self.assertEqual(bal["balance"], 500.0)

            # Check position created
            positions_list = db.fetch_positions(conn=conn)
            self.assertEqual(len(positions_list), 1)
            self.assertEqual(positions_list[0]["position_id"], "pos_synced")
            self.assertEqual(positions_list[0]["net_pnl"], 1996.0)

        conn.close()


if __name__ == "__main__":
    unittest.main()
