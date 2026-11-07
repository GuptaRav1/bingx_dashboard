"""
Database Backup & Notification Service.
Supports:
1. Automated Telegram dispatch upon sync completion.
2. Safe SQLite snapshot using sqlite3.Connection.backup API.
"""

import os
import time
import tempfile
import logging
import sqlite3
from typing import Optional, Any
from pathlib import Path
from datetime import datetime
import requests

import time_utils

logger = logging.getLogger(__name__)


def create_sqlite_snapshot(source_conn: sqlite3.Connection, target_path: str) -> None:
    """
    Safely creates a consistent SQLite backup snapshot using sqlite3 backup API.
    Guarantees WAL checkpointing and transactional integrity without file locking conflicts.
    """
    dest_conn = sqlite3.connect(target_path)
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()


def send_telegram_document(
    bot_token: str,
    chat_id: str,
    file_path: str,
    caption: str,
    timeout: int = 30
) -> dict[str, Any]:
    """
    Sends a file document to a Telegram chat via Telegram Bot API.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    filename = Path(file_path).name

    with open(file_path, "rb") as f:
        files = {"document": (filename, f, "application/x-sqlite3")}
        data = {
            "chat_id": chat_id,
            "caption": caption,
            "parse_mode": "HTML"
        }
        resp = requests.post(url, data=data, files=files, timeout=timeout)

    resp.raise_for_status()
    body = resp.json()
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body.get('description', 'Unknown error')}")
    return body


def send_backup_if_configured(
    conn: sqlite3.Connection,
    summary: Optional[dict[str, Any]] = None
) -> Optional[dict[str, Any]]:
    """
    If TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in environment / .env,
    creates a clean SQLite snapshot and sends the .db file to Telegram.
    Returns result summary dict, or None if not configured.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not bot_token or not chat_id:
        logger.debug("Telegram backup not configured (TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing).")
        return None

    now_ms = int(time.time() * 1000)
    ist_dt = datetime.fromtimestamp(now_ms / 1000, tz=time_utils.IST)
    ist_str = ist_dt.strftime("%d/%m/%y %H:%M:%S")
    file_date = ist_dt.strftime("%Y%m%d_%H%M%S")
    backup_filename = f"trades_backup_{file_date}.db"

    # Query quick stats for caption
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM positions")
        pos_count = cur.fetchone()[0]
        cur.execute("SELECT balance FROM balance_snapshots ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        balance_str = f"{row[0]:.4f} USDT" if row and row[0] is not None else "—"
    except Exception as e:
        logger.warning(f"Failed to query stats for backup caption: {e}")
        pos_count = "—"
        balance_str = "—"

    ins_orders = summary.get("inserted_orders", 0) if summary else 0
    new_pos = summary.get("new_positions", 0) if summary else 0

    caption = (
        f"📊 <b>Trade Terminal Database Backup</b>\n"
        f"⏰ <b>Time:</b> {ist_str} (IST)\n"
        f"📦 <b>Total Positions:</b> {pos_count}\n"
        f"💰 <b>Balance:</b> {balance_str}\n"
        f"⚡ <b>Sync:</b> +{ins_orders} orders, +{new_pos} positions"
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        target_path = os.path.join(tmpdir, backup_filename)
        create_sqlite_snapshot(conn, target_path)

        logger.info(f"Sending database backup ({backup_filename}) to Telegram chat {chat_id}...")
        res = send_telegram_document(bot_token, chat_id, target_path, caption)
        logger.info("Database backup successfully delivered via Telegram.")
        return {"status": "sent", "file": backup_filename, "timestamp": now_ms}

