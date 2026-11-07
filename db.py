"""
SQLite Database Layer.
Conforms to SYSTEM_DESIGN.md Section 4, Section 5, Section 8.3, and PRD.md Section 6.
"""

import sqlite3
import time
from typing import Any, Optional
from pathlib import Path

import config

try:
    from flask import g, has_app_context
except ImportError:
    has_app_context = lambda: False
    g = None


def get_db(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Returns a SQLite connection with required PRAGMAs configured.
    Within Flask request context, reuses connection on `g._database`.
    Outside Flask (sync thread, CLI, tests), returns a standalone connection.
    
    PRAGMAs (SYSTEM_DESIGN.md Section 4.3):
    - journal_mode=WAL
    - synchronous=NORMAL
    - foreign_keys=ON
    - busy_timeout=5000
    """
    path = db_path or config.SQLITE_PATH

    # If inside a Flask request, reuse connection via flask.g
    if has_app_context() and db_path is None:
        if not hasattr(g, "_database") or g._database is None:
            conn = sqlite3.connect(path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            _configure_pragmas(conn)
            g._database = conn
        return g._database

    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    _configure_pragmas(conn)
    return conn


def _configure_pragmas(conn: sqlite3.Connection):
    """Enforces SQLite performance and concurrency invariants."""
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")


def close_db(e: Any = None):
    """Flask teardown callback to close per-request database connection."""
    if has_app_context() and hasattr(g, "_database") and g._database is not None:
        g._database.close()
        g._database = None


def init_db(db_path: Optional[str] = None):
    """
    Initializes database tables and indexes from schema.sql idempotently.
    Sets meta.schema_version = 1 if not present.
    """
    conn = get_db(db_path)
    schema_file = config.SCHEMA_PATH

    if schema_file.exists():
        with open(schema_file, "r", encoding="utf-8") as f:
            ddl = f.read()
    else:
        raise FileNotFoundError(f"Schema file not found at {schema_file}")

    conn.executescript(ddl)

    # Initialize schema_version in meta if not present
    cur = conn.cursor()
    cur.execute("SELECT value FROM meta WHERE key = 'schema_version'")
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(config.SCHEMA_VERSION),),
        )

    conn.commit()

    # If opened outside Flask context, close connection
    if not has_app_context():
        conn.close()


# ---------------------------------------------------------------------------
# Metadata Queries
# ---------------------------------------------------------------------------

def get_meta(key: str, conn: Optional[sqlite3.Connection] = None) -> Optional[str]:
    """Retrieves a single metadata value by key."""
    close_when_done = False
    if conn is None:
        conn = get_db()
        close_when_done = not has_app_context()

    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM meta WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else None
    finally:
        if close_when_done:
            conn.close()


def set_meta(key: str, value: str, conn: Optional[sqlite3.Connection] = None):
    """Inserts or updates a key-value pair in meta table."""
    close_when_done = False
    if conn is None:
        conn = get_db()
        close_when_done = not has_app_context()

    try:
        with conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    finally:
        if close_when_done:
            conn.close()


def fetch_meta(conn: Optional[sqlite3.Connection] = None) -> dict:
    """
    Returns metadata for GET /api/meta (SYSTEM_DESIGN.md Section 5.1):
    - last_synced_at: epoch ms or None
    - order_count: total rows in orders
    - position_count: total rows in positions
    - schema_version: int (1)
    """
    close_when_done = False
    if conn is None:
        conn = get_db()
        close_when_done = not has_app_context()

    try:
        cur = conn.cursor()

        # last_synced_at
        cur.execute("SELECT value FROM meta WHERE key = 'last_synced_at'")
        row = cur.fetchone()
        last_synced_at = int(row["value"]) if row and row["value"] is not None else None

        # schema_version
        cur.execute("SELECT value FROM meta WHERE key = 'schema_version'")
        row = cur.fetchone()
        schema_version = int(row["value"]) if row and row["value"] is not None else config.SCHEMA_VERSION

        # Counts
        cur.execute("SELECT COUNT(*) AS cnt FROM orders")
        order_count = int(cur.fetchone()["cnt"])

        cur.execute("SELECT COUNT(*) AS cnt FROM positions")
        position_count = int(cur.fetchone()["cnt"])

        return {
            "last_synced_at": last_synced_at,
            "order_count": order_count,
            "position_count": position_count,
            "schema_version": schema_version,
        }
    finally:
        if close_when_done:
            conn.close()


# ---------------------------------------------------------------------------
# Balance Queries
# ---------------------------------------------------------------------------

def fetch_balance(conn: Optional[sqlite3.Connection] = None) -> dict:
    """
    Returns latest USDT balance snapshot (SYSTEM_DESIGN.md Section 5.3).
    Returns {"balance": null} if never synced.
    """
    close_when_done = False
    if conn is None:
        conn = get_db()
        close_when_done = not has_app_context()

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT balance, equity, unrealized_profit, ts "
            "FROM balance_snapshots "
            "WHERE asset = 'USDT' "
            "ORDER BY ts DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            return {
                "balance": None,
                "equity": None,
                "unrealized_profit": None,
                "ts": None,
            }

        return {
            "balance": float(row["balance"]),
            "equity": float(row["equity"]),
            "unrealized_profit": float(row["unrealized_profit"]),
            "ts": int(row["ts"]),
        }
    finally:
        if close_when_done:
            conn.close()


def insert_balance_snapshot(snap: dict, conn: Optional[sqlite3.Connection] = None):
    """
    Inserts a balance snapshot into balance_snapshots.
    Prunes snapshots older than 90 days per PRD Section 6.3.
    """
    close_when_done = False
    if conn is None:
        conn = get_db()
        close_when_done = not has_app_context()

    try:
        ts = int(snap["ts"])
        asset = str(snap.get("asset", "USDT"))
        balance = float(snap["balance"])
        equity = float(snap["equity"])
        unrealized_profit = float(snap.get("unrealized_profit", 0.0))

        with conn:
            conn.execute(
                "INSERT INTO balance_snapshots (ts, asset, balance, equity, unrealized_profit) "
                "VALUES (?, ?, ?, ?, ?)",
                (ts, asset, balance, equity, unrealized_profit),
            )
            # Prune older than 90 days
            cutoff_prune = ts - (90 * 24 * 3600 * 1000)
            conn.execute("DELETE FROM balance_snapshots WHERE ts < ?", (cutoff_prune,))
    finally:
        if close_when_done:
            conn.close()


# ---------------------------------------------------------------------------
# Orders Queries
# ---------------------------------------------------------------------------

def _parse_leverage(leverage_val: Any) -> int:
    """Parses leverage which can be '100X', '20', or 20 into integer."""
    if leverage_val is None:
        return 1
    s = str(leverage_val).strip().upper().replace("X", "")
    try:
        return int(float(s))
    except ValueError:
        return 1


def _parse_reduce_only(reduce_val: Any) -> int:
    """Parses reduce_only from boolean/string/int into 0 or 1."""
    if reduce_val in (True, 1, "1", "true", "TRUE", "True"):
        return 1
    return 0


def upsert_orders(orders: list[dict], conn: Optional[sqlite3.Connection] = None) -> tuple[int, int]:
    """
    Upserts orders deduplicated by order_id (PRD Section 6.1, Section 7.2).
    Returns (inserted_count, updated_count).
    """
    if not orders:
        return 0, 0

    close_when_done = False
    if conn is None:
        conn = get_db()
        close_when_done = not has_app_context()

    now_epoch_ms = int(time.time() * 1000)
    inserted = 0
    updated = 0

    sql = """
    INSERT INTO orders (
        order_id, symbol, position_id, position_side, status,
        reduce_only, executed_qty, avg_price, profit, commission,
        leverage, update_time, synced_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(order_id) DO UPDATE SET
        symbol = excluded.symbol,
        position_id = excluded.position_id,
        position_side = excluded.position_side,
        status = excluded.status,
        reduce_only = excluded.reduce_only,
        executed_qty = excluded.executed_qty,
        avg_price = excluded.avg_price,
        profit = excluded.profit,
        commission = excluded.commission,
        leverage = excluded.leverage,
        update_time = excluded.update_time,
        synced_at = excluded.synced_at
    """

    try:
        cur = conn.cursor()
        with conn:
            for o in orders:
                order_id = int(o["orderId"])

                # Check if exists to count insert vs update
                cur.execute("SELECT 1 FROM orders WHERE order_id = ?", (order_id,))
                exists = cur.fetchone() is not None

                symbol = str(o.get("symbol", ""))
                position_id = str(o.get("positionID", ""))
                position_side = str(o.get("positionSide", "LONG"))
                status = str(o.get("status", ""))
                reduce_only = _parse_reduce_only(o.get("reduceOnly", False))
                executed_qty = float(o.get("executedQty", 0.0))
                avg_price = float(o.get("avgPrice", 0.0))
                profit = float(o.get("profit", 0.0))
                commission = float(o.get("commission", 0.0))
                leverage = _parse_leverage(o.get("leverage", 1))
                update_time = int(o.get("updateTime") or o.get("time") or now_epoch_ms)

                cur.execute(
                    sql,
                    (
                        order_id,
                        symbol,
                        position_id,
                        position_side,
                        status,
                        reduce_only,
                        executed_qty,
                        avg_price,
                        profit,
                        commission,
                        leverage,
                        update_time,
                        now_epoch_ms,
                    ),
                )

                if exists:
                    updated += 1
                else:
                    inserted += 1

        return inserted, updated
    finally:
        if close_when_done:
            conn.close()


# ---------------------------------------------------------------------------
# Positions Queries
# ---------------------------------------------------------------------------

def fetch_positions(
    from_ts: Optional[int] = None,
    to_ts: Optional[int] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict]:
    """
    Queries positions from positions table (SYSTEM_DESIGN.md Section 5.2).
    Filters by close_time within [from_ts, to_ts] inclusive if provided.
    Ordered by close_time DESC.
    """
    close_when_done = False
    if conn is None:
        conn = get_db()
        close_when_done = not has_app_context()

    try:
        query = (
            "SELECT position_id, symbol, direction, leverage, "
            "open_time, close_time, entry_price, exit_price, "
            "quantity, fees, gross_pnl, net_pnl, note "
            "FROM positions "
        )
        params = []
        conditions = []

        if from_ts is not None:
            conditions.append("close_time >= ?")
            params.append(from_ts)

        if to_ts is not None:
            conditions.append("close_time <= ?")
            params.append(to_ts)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY close_time DESC"

        cur = conn.cursor()
        cur.execute(query, tuple(params))
        rows = cur.fetchall()

        results = []
        for r in rows:
            results.append({
                "position_id": str(r["position_id"]),
                "symbol": str(r["symbol"]),
                "direction": str(r["direction"]),
                "leverage": int(r["leverage"]),
                "open_time": int(r["open_time"]) if r["open_time"] is not None else None,
                "close_time": int(r["close_time"]),
                "entry_price": float(r["entry_price"]) if r["entry_price"] is not None else None,
                "exit_price": float(r["exit_price"]),
                "quantity": float(r["quantity"]),
                "fees": float(r["fees"]),
                "gross_pnl": float(r["gross_pnl"]),
                "net_pnl": float(r["net_pnl"]),
                "note": str(r["note"]) if r["note"] is not None else None,
            })
        return results
    finally:
        if close_when_done:
            conn.close()

