"""
BingX Order Sync and Pipeline Orchestrator.
Conforms to SYSTEM_DESIGN.md Section 6.4, Section 8, Section 14.2, and PRD.md Section 7.
"""

import time
import json
import logging
from typing import Optional, Any
import sqlite3

import config
import bingx_client
from bingx_client import BingxError
import db
import positions
import backup

logger = logging.getLogger(__name__)

# Shared default progress state
_default_progress: dict[str, Any] = {
    "running": False,
    "index": 0,
    "window_index": 0,
    "total": 0,
    "window_total": 0,
    "started_at": None,
    "last_result": None,
}


def build_windows(
    start_ms: int,
    end_ms: int,
    window_size_ms: int = config.WINDOW_MS,
) -> list[tuple[int, int]]:
    """
    Builds a list of [start, end] millisecond windows (PRD Section 5.1).
    window_size = 7 * 24 * 3600 * 1000 = 604_800_000 ms
    window_start = <epoch ms>
    window_end = min(window_start + window_size - 1, final_end)
    next_start = window_end + 1
    """
    windows = []
    curr = start_ms
    while curr <= end_ms:
        w_end = min(curr + window_size_ms - 1, end_ms)
        windows.append((curr, w_end))
        curr = w_end + 1
    return windows


def get_sync_range(conn: sqlite3.Connection) -> tuple[int, int]:
    """
    Computes (start_ms, end_ms) for the sync run (PRD Section 7.1):
    - First-ever sync: start = max(cutoff_epoch_ms, now - 89 days), end = now.
    - Subsequent syncs: start = now - 89 days, end = now.
    """
    now = int(time.time() * 1000)
    retention_ms = config.HISTORY_DAYS * 24 * 3600 * 1000
    earliest_allowed = now - retention_ms

    last_synced_at = db.get_meta("last_synced_at", conn=conn)
    if not last_synced_at:
        # First-ever sync
        start = max(config.CUTOFF_EPOCH_MS, earliest_allowed)
    else:
        # Subsequent rolling sync
        start = earliest_allowed

    return start, now


def run(progress: Optional[dict[str, Any]] = None, conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    """
    Orchestrates the sync pipeline according to SYSTEM_DESIGN.md Section 6.4:
    1. Determine start and end timestamps.
    2. Build 7-day window slices.
    3. Loop over windows, fetch orders from BingX, upsert into DB.
    4. Fetch and snapshot account balance.
    5. Rebuild positions in a single transaction if at least one window succeeded.
    6. Update meta (last_synced_at, last_sync_result).
    """
    close_conn_when_done = False
    if conn is None:
        conn = db.get_db()
        close_conn_when_done = True

    prog = progress if progress is not None else _default_progress

    started_at = int(time.time() * 1000)
    prog["running"] = True
    prog["started_at"] = started_at
    prog["index"] = 0
    prog["window_index"] = 0
    prog["total"] = 0
    prog["window_total"] = 0

    inserted_orders = 0
    updated_orders = 0
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    rebuilt_positions_count = 0

    try:
        start_ms, end_ms = get_sync_range(conn)
        windows = build_windows(start_ms, end_ms)
        prog["total"] = len(windows)
        prog["window_total"] = len(windows)

        logger.info(
            f"Starting sync: {len(windows)} windows from {start_ms} to {end_ms}"
        )

        # Loop through each 7-day window
        for i, (ws, we) in enumerate(windows):
            prog["index"] = i + 1
            prog["window_index"] = i + 1

            try:
                orders = bingx_client.fetch_orders_window(ws, we)
            except (BingxError, Exception) as e:
                logger.error(f"Window {i + 1}/{len(windows)} failed ({ws} -> {we}): {e}")
                errors.append({"window": [ws, we], "reason": str(e)})
                time.sleep(config.RATE_LIMIT_SLEEP)
                continue

            # Check for 1000 orders truncation warning (PRD Section 5.1, §7.2)
            if len(orders) == 1000:
                logger.warning(f"Window {i + 1} ({ws} -> {we}) returned exactly 1000 orders (truncated).")
                warnings.append({"window": [ws, we], "reason": "truncated"})

            ins, upd = db.upsert_orders(orders, conn=conn)
            inserted_orders += ins
            updated_orders += upd

            time.sleep(config.RATE_LIMIT_SLEEP)

        # Fetch and snapshot balance
        try:
            snap = bingx_client.fetch_balance()
            db.insert_balance_snapshot(snap, conn=conn)
            logger.info("Balance snapshot updated successfully.")
        except (BingxError, Exception) as e:
            logger.warning(f"Failed to refresh balance snapshot: {e}")
            errors.append({"endpoint": "balance", "reason": str(e)})

        # Rebuild positions if at least one window succeeded (SYSTEM_DESIGN.md Section 6.4)
        sync_succeeded = len(windows) == 0 or len(errors) < len(windows)

        if sync_succeeded:
            rebuild_res = positions.rebuild(conn)
            rebuilt_positions_count = rebuild_res.position_count

        finished_at = int(time.time() * 1000)
        duration_ms = finished_at - started_at

        summary = {
            "inserted_orders": inserted_orders,
            "updated_orders": updated_orders,
            "new_positions": rebuilt_positions_count,
            "errors": errors,
            "warnings": warnings,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
        }

        # Update metadata audit trail
        if sync_succeeded:
            db.set_meta("last_synced_at", str(finished_at), conn=conn)
        db.set_meta("last_sync_result", json.dumps(summary), conn=conn)

        # Trigger database backup if configured (Telegram)
        try:
            backup_res = backup.send_backup_if_configured(conn, summary=summary)
            if backup_res:
                summary["backup"] = backup_res
                db.set_meta("last_sync_result", json.dumps(summary), conn=conn)
        except Exception as e:
            logger.warning(f"Database backup dispatch failed: {e}")
            summary["backup_error"] = str(e)
            db.set_meta("last_sync_result", json.dumps(summary), conn=conn)

        prog["last_result"] = summary
        logger.info(
            f"Sync finished in {duration_ms} ms: inserted={inserted_orders}, "
            f"updated={updated_orders}, positions={rebuilt_positions_count}, "
            f"errors={len(errors)}, warnings={len(warnings)}"
        )
        return summary

    finally:
        prog["running"] = False
        if close_conn_when_done:
            conn.close()

