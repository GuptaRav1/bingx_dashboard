-- BingX Dashboard SQLite Schema
-- Invariants: WAL mode, synchronous=NORMAL, foreign_keys=ON, busy_timeout=5000

CREATE TABLE IF NOT EXISTS orders (
    order_id INTEGER PRIMARY KEY,
    symbol TEXT NOT NULL,
    position_id TEXT NOT NULL,
    position_side TEXT NOT NULL,
    status TEXT NOT NULL,
    reduce_only INTEGER NOT NULL,
    executed_qty REAL NOT NULL,
    avg_price REAL NOT NULL,
    profit REAL NOT NULL,
    commission REAL NOT NULL,
    leverage INTEGER NOT NULL,
    update_time INTEGER NOT NULL,
    synced_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_position_id ON orders(position_id);
CREATE INDEX IF NOT EXISTS idx_orders_update_time ON orders(update_time);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

CREATE TABLE IF NOT EXISTS positions (
    position_id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    leverage INTEGER NOT NULL,
    open_time INTEGER,
    close_time INTEGER NOT NULL,
    entry_price REAL,
    exit_price REAL NOT NULL,
    quantity REAL NOT NULL,
    fees REAL NOT NULL,
    gross_pnl REAL NOT NULL,
    net_pnl REAL NOT NULL,
    note TEXT,
    rebuilt_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_positions_close_time ON positions(close_time);
CREATE INDEX IF NOT EXISTS idx_positions_net_pnl ON positions(net_pnl);

CREATE TABLE IF NOT EXISTS balance_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    asset TEXT NOT NULL,
    balance REAL NOT NULL,
    equity REAL NOT NULL,
    unrealized_profit REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_balance_snapshots_ts ON balance_snapshots(ts);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

