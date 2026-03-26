"""Tests for data storage — thread-safe SQLite operations."""

import threading
import pytest
from data.storage import log_trade, get_trades, close_db, _get_connection


@pytest.fixture(autouse=True)
def cleanup_db():
    """Clean test trades after each test."""
    yield
    conn = _get_connection()
    conn.execute("DELETE FROM trades WHERE strategy = 'test'")
    conn.commit()


class TestStorage:
    def test_log_and_get_trade(self):
        log_trade(
            strategy="test", side="BUY", price=0.5,
            size=1.0, token_id="token_abc", pnl=0.05,
        )
        df = get_trades(strategy="test")
        assert len(df) >= 1
        assert df.iloc[-1]["side"] == "BUY"

    def test_concurrent_writes(self):
        """Verify thread-safe writes don't crash."""
        errors = []

        def write(i):
            try:
                log_trade(
                    strategy="test", side="BUY", price=0.5,
                    size=1.0, token_id=f"concurrent_{i}", pnl=0.01,
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        df = get_trades(strategy="test")
        assert len(df) >= 20

    def test_filter_by_account(self):
        log_trade(strategy="test", side="BUY", price=0.5, size=1.0, token_id="t1", account="acct_a")
        log_trade(strategy="test", side="SELL", price=0.6, size=1.0, token_id="t2", account="acct_b")
        df = get_trades(strategy="test", account="acct_a")
        assert all(df["account"] == "acct_a")
