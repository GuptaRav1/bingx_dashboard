"""
Unit tests for backup.py.
"""

import os
import unittest
from unittest.mock import patch, MagicMock
import sqlite3
import tempfile

import backup


class TestBackup(unittest.TestCase):

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE test_table (id INTEGER PRIMARY KEY, val TEXT);")
        self.conn.execute("INSERT INTO test_table (val) VALUES ('hello'), ('world');")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_create_sqlite_snapshot(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            backup.create_sqlite_snapshot(self.conn, tmp_path)
            # Verify the snapshot has the same table and data
            snap_conn = sqlite3.connect(tmp_path)
            rows = snap_conn.execute("SELECT val FROM test_table ORDER BY id").fetchall()
            snap_conn.close()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0], "hello")
            self.assertEqual(rows[1][0], "world")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_send_backup_skipped_when_not_configured(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            result = backup.send_backup_if_configured(self.conn)
            self.assertIsNone(result)

    @patch("backup.send_telegram_document")
    def test_send_backup_when_configured(self, mock_send):
        mock_send.return_value = {"ok": True}
        # Add required tables for stats query
        self.conn.execute("CREATE TABLE positions (id TEXT);")
        self.conn.execute("CREATE TABLE balance_snapshots (id INTEGER PRIMARY KEY, balance REAL);")
        self.conn.execute("INSERT INTO positions VALUES ('p1');")
        self.conn.execute("INSERT INTO balance_snapshots (balance) VALUES (12.791);")
        self.conn.commit()

        env = {
            "TELEGRAM_BOT_TOKEN": "123456:FAKE_TOKEN",
            "TELEGRAM_CHAT_ID": "987654321",
        }
        with patch.dict(os.environ, env):
            res = backup.send_backup_if_configured(self.conn, summary={"inserted_orders": 5, "new_positions": 2})
            self.assertIsNotNone(res)
            self.assertEqual(res["status"], "sent")
            self.assertTrue(res["file"].startswith("trades_backup_"))
            self.assertTrue(mock_send.called)
            args, _ = mock_send.call_args
            self.assertEqual(args[0], "123456:FAKE_TOKEN")
            self.assertEqual(args[1], "987654321")
            self.assertIn("12.7910 USDT", args[3])
            self.assertIn("+5 orders", args[3])


if __name__ == "__main__":
    unittest.main()

