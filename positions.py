"""
Position reconstruction service.
Conforms to PRD.md Section 8 and SYSTEM_DESIGN.md Section 4.4.

Rebuilds closed positions from raw orders in a single SQLite transaction.
"""

import time
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Any
import sqlite3

logger = logging.getLogger(__name__)


@dataclass
class RebuildResult:
    """Outcome of rebuilding positions."""
    position_count: int
    open_positions: int = 0


def weighted_avg(orders: list[dict]) -> Optional[float]:
    """
    Computes volume-weighted average price: sum(qty * price) / sum(qty).
    Returns None if orders is empty or total quantity is 0 (PRD Section 8).
    """
    if not orders:
        return None
    total_qty = sum(o["executed_qty"] for o in orders)
    if total_qty <= 0:
        return None
    total_value = sum(o["executed_qty"] * o["avg_price"] for o in orders)
    return total_value / total_qty


def rebuild(conn: sqlite3.Connection) -> RebuildResult:
    """
    Rebuilds the `positions` table from `orders` inside a single transaction.
    
    Algorithm (PRD.md Section 8):
    1. Keep orders where status = 'FILLED' AND executed_qty > 0.
    2. Group by position_id. Sort each group by update_time asc.
    3. entry_orders = [o for o in group if not o.reduce_only]
       exit_orders  = [o for o in group if o.reduce_only]
    4. Skip if exit_orders is empty (position still open).
    5. For each closed group:
       - symbol, direction = group[0].symbol, group[0].position_side
       - leverage = group[0].leverage
       - open_time = entry_orders[0].update_time (None if empty)
       - close_time = exit_orders[-1].update_time
       - entry_price = weighted_avg(entry_orders) (None if empty)
       - exit_price  = weighted_avg(exit_orders)
       - quantity = sum(exit_orders.executed_qty)
       - fees     = -sum(all_orders.commission)
       - gross_pnl = sum(all_orders.profit)
       - net_pnl  = gross_pnl - fees
       - note:
           if entry_orders empty -> "missing entry order"
           if sum(entry_qty) != quantity -> "partially closed"
           (both -> "missing entry order; partially closed")
           else NULL
    6. Sort by close_time asc for storage.
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT order_id, symbol, position_id, position_side, status,
               reduce_only, executed_qty, avg_price, profit, commission,
               leverage, update_time
        FROM orders
        WHERE status = 'FILLED' AND executed_qty > 0
        ORDER BY update_time ASC
        """
    )
    rows = cur.fetchall()

    # Step 2: Group by position_id
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["position_id"])].append(dict(row))

    positions_to_insert = []
    open_positions = 0
    now_epoch_ms = int(time.time() * 1000)

    for position_id, group in groups.items():
        # Sort group by update_time asc
        group.sort(key=lambda o: o["update_time"])

        # Step 3: Split into entry and exit orders
        entry_orders = [o for o in group if not o["reduce_only"]]
        exit_orders = [o for o in group if o["reduce_only"]]

        # Step 4: Skip if exit_orders is empty (position still open)
        if not exit_orders:
            open_positions += 1
            continue

        # Step 5: Derive position fields
        first_order = group[0]
        symbol = first_order["symbol"]
        direction = first_order["position_side"]
        leverage = first_order["leverage"]

        open_time = entry_orders[0]["update_time"] if entry_orders else None
        close_time = exit_orders[-1]["update_time"]

        entry_price = weighted_avg(entry_orders)
        exit_price = weighted_avg(exit_orders) or 0.0

        quantity = sum(o["executed_qty"] for o in exit_orders)
        sum_entry_qty = sum(o["executed_qty"] for o in entry_orders)

        # commission is stored as negative in orders (e.g. -0.008974), so fees > 0
        fees = -sum(o["commission"] for o in group)
        gross_pnl = sum(o["profit"] for o in group)
        net_pnl = gross_pnl - fees

        # Note flags (PRD Section 8 and Section 11/15 edge cases):
        notes = []
        if not entry_orders:
            # No entry order in sync history (opened before fetched window)
            notes.append("missing entry order")
        elif sum_entry_qty < quantity - 1e-9:
            # Exited more quantity than recorded in entries -> entry missing
            notes.append("missing entry order")

        if entry_orders and abs(sum_entry_qty - quantity) > 1e-9:
            # Quantity mismatch -> partially closed
            notes.append("partially closed")

        note = "; ".join(notes) if notes else None

        positions_to_insert.append({
            "position_id": position_id,
            "symbol": symbol,
            "direction": direction,
            "leverage": leverage,
            "open_time": open_time,
            "close_time": close_time,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "quantity": quantity,
            "fees": fees,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "note": note,
        })

    # Step 6: Sort by close_time asc for storage
    positions_to_insert.sort(key=lambda p: p["close_time"])

    # Invariant: Rebuild runs inside a SINGLE TRANSACTION (SYSTEM_DESIGN.md Section 4.4)
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM positions")
        insert_sql = """
        INSERT INTO positions (
            position_id, symbol, direction, leverage,
            open_time, close_time, entry_price, exit_price,
            quantity, fees, gross_pnl, net_pnl, note, rebuilt_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        for p in positions_to_insert:
            conn.execute(
                insert_sql,
                (
                    p["position_id"],
                    p["symbol"],
                    p["direction"],
                    p["leverage"],
                    p["open_time"],
                    p["close_time"],
                    p["entry_price"],
                    p["exit_price"],
                    p["quantity"],
                    p["fees"],
                    p["gross_pnl"],
                    p["net_pnl"],
                    p["note"],
                    now_epoch_ms,
                ),
            )
        conn.execute("COMMIT")
    except Exception as e:
        conn.execute("ROLLBACK")
        logger.exception("Failed during position rebuild transaction, rolled back.")
        raise e

    logger.info(
        f"Position rebuild complete: {len(positions_to_insert)} positions created, "
        f"{open_positions} open positions skipped."
    )
    return RebuildResult(position_count=len(positions_to_insert), open_positions=open_positions)

