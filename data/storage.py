"""Data storage for candles and trades — thread-safe SQLite."""

import logging
import os
import sqlite3
import threading
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent
DB_PATH = DATA_DIR / "trades.db"

# Thread-safe singleton connection
_db_lock = threading.Lock()
_connection: sqlite3.Connection | None = None
_initialized = False


def _get_connection() -> sqlite3.Connection:
    """Get or create the singleton SQLite connection (thread-safe)."""
    global _connection, _initialized
    with _db_lock:
        if _connection is None:
            os.makedirs(DATA_DIR, exist_ok=True)
            _connection = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        if not _initialized:
            _connection.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    strategy TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    size REAL NOT NULL,
                    token_id TEXT NOT NULL,
                    pnl REAL DEFAULT 0,
                    account TEXT DEFAULT ''
                )
            """)
            _connection.commit()
            _initialized = True
        return _connection


def close_db() -> None:
    """Close the singleton connection (call on shutdown)."""
    global _connection, _initialized
    with _db_lock:
        if _connection is not None:
            _connection.close()
            _connection = None
            _initialized = False


def save_candles_csv(df: pd.DataFrame, filename: str) -> Path:
    """Save candle DataFrame to CSV."""
    path = DATA_DIR / filename
    df.to_csv(path, index=False)
    logger.info("Saved %d candles to %s", len(df), path)
    return path


def load_candles_csv(filename: str) -> pd.DataFrame:
    """Load candle DataFrame from CSV."""
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"No data file: {path}")
    df = pd.read_csv(path, parse_dates=["timestamp"])
    return df


def log_trade(
    strategy: str,
    side: str,
    price: float,
    size: float,
    token_id: str,
    pnl: float = 0,
    account: str = "",
) -> None:
    """Insert a trade record into the database."""
    from datetime import datetime, timezone

    conn = _get_connection()
    with _db_lock:
        conn.execute(
            "INSERT INTO trades (timestamp, strategy, side, price, size, token_id, pnl, account) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), strategy, side, price, size, token_id, pnl, account),
        )
        conn.commit()


def get_trades(strategy: str = "", account: str = "") -> pd.DataFrame:
    """Query trades from the database."""
    conn = _get_connection()
    query = "SELECT * FROM trades WHERE 1=1"
    params: list = []
    if strategy:
        query += " AND strategy = ?"
        params.append(strategy)
    if account:
        query += " AND account = ?"
        params.append(account)
    with _db_lock:
        df = pd.read_sql_query(query, conn, params=params)
    return df
