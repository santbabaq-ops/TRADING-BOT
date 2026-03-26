"""Tests for copy-trading strategy and wallet scanner."""

from unittest.mock import patch, MagicMock
import pytest

from data.wallet_scanner import WalletScanner, WalletScore, CopySignal
from strategies.copytrade_strategy import CopyTradeStrategy
from strategies.base_strategy import Signal


# --- Mock trade data ---

def _make_trades(n: int, win_ratio: float = 0.6) -> list[dict]:
    """Generate mock trades with a given win ratio."""
    trades = []
    for i in range(n):
        is_win = i < int(n * win_ratio)
        trades.append({
            "maker": f"0xwallet{i % 3:03d}",
            "taker": f"0xtaker{i:03d}",
            "side": "BUY",
            "price": 0.35 if is_win else 0.65,  # buy below 0.5 = win
            "size": 10,
            "asset_id": f"token_{i % 5}",
            "market": f"Market {i % 5}",
            "timestamp": f"2026-03-26T{i:02d}:00:00Z",
        })
    return trades


class TestWalletScoring:
    def test_score_profitable_wallet(self):
        scanner = WalletScanner(min_trades=5, min_win_rate=0.5, top_n=3)
        trades = _make_trades(20, win_ratio=0.7)
        score = scanner.score_wallet("0xtest", trades)

        assert score.address == "0xtest"
        assert score.trade_count == 20
        assert score.win_rate > 0.5
        assert score.composite_score > 0

    def test_score_empty_trades(self):
        scanner = WalletScanner()
        score = scanner.score_wallet("0xempty", [])
        assert score.trade_count == 0
        assert score.win_rate == 0.0

    def test_score_losing_wallet(self):
        scanner = WalletScanner()
        trades = _make_trades(20, win_ratio=0.2)
        score = scanner.score_wallet("0xloser", trades)
        assert score.win_rate < 0.5

    @patch.object(WalletScanner, "fetch_recent_trades")
    def test_get_top_wallets_filters(self, mock_fetch):
        mock_fetch.return_value = _make_trades(100, win_ratio=0.7)
        scanner = WalletScanner(min_trades=10, min_win_rate=0.5, top_n=2)
        top = scanner.get_top_wallets(force_refresh=True)

        assert len(top) <= 2
        for w in top:
            assert w.win_rate >= 0.5
            assert w.trade_count >= 10

    @patch.object(WalletScanner, "fetch_recent_trades")
    def test_wallet_cache(self, mock_fetch):
        mock_fetch.return_value = _make_trades(50, win_ratio=0.7)
        scanner = WalletScanner(min_trades=5, min_win_rate=0.3, top_n=3, rescore_interval=3600)

        top1 = scanner.get_top_wallets(force_refresh=True)
        top2 = scanner.get_top_wallets()  # should use cache

        assert mock_fetch.call_count == 1  # only called once


class TestCopySignalDetection:
    @patch.object(WalletScanner, "get_top_wallets")
    @patch.object(WalletScanner, "get_wallet_latest_trades")
    def test_detect_new_positions(self, mock_latest, mock_top):
        mock_top.return_value = [
            WalletScore(address="0xabc", win_rate=0.7, composite_score=0.8, trade_count=30),
        ]
        mock_latest.return_value = [
            {"asset_id": "token_123", "side": "BUY", "price": 0.45, "market": "Test Market", "timestamp": "2026-01-01"},
        ]

        scanner = WalletScanner()
        signals = scanner.detect_new_positions({})

        assert len(signals) == 1
        assert signals[0].side == "BUY"
        assert signals[0].token_id == "token_123"

    @patch.object(WalletScanner, "get_top_wallets")
    @patch.object(WalletScanner, "get_wallet_latest_trades")
    def test_no_duplicate_signals(self, mock_latest, mock_top):
        mock_top.return_value = [
            WalletScore(address="0xabc", win_rate=0.7, composite_score=0.8, trade_count=30),
        ]
        mock_latest.return_value = [
            {"asset_id": "token_123", "side": "BUY", "price": 0.45, "market": "Test", "timestamp": "2026-01-01"},
        ]

        scanner = WalletScanner()
        # First call: new signal
        signals1 = scanner.detect_new_positions({})
        assert len(signals1) == 1

        # Second call with known positions: no signal
        known = {"0xabc:token_123:BUY": signals1[0]}
        signals2 = scanner.detect_new_positions(known)
        assert len(signals2) == 0


class TestCopyTradeStrategy:
    @patch.object(WalletScanner, "detect_new_positions")
    @patch.object(WalletScanner, "get_top_wallets")
    def test_generate_buy_signal(self, mock_top, mock_detect):
        mock_top.return_value = [
            WalletScore(address="0xabc", composite_score=0.8, trade_count=30),
        ]
        mock_detect.return_value = [
            CopySignal(wallet="0xabc", token_id="t1", market_slug="Test", side="BUY", price=0.45, wallet_score=0.8),
        ]

        strategy = CopyTradeStrategy(min_trades=5, min_win_rate=0.3, top_n=3)
        import pandas as pd
        signal = strategy.generate_signal(pd.DataFrame())

        assert signal.signal == Signal.BUY
        assert signal.confidence > 0
        assert "Copy" in signal.reason

    @patch.object(WalletScanner, "detect_new_positions")
    def test_hold_when_no_signals(self, mock_detect):
        mock_detect.return_value = []
        strategy = CopyTradeStrategy(min_trades=5, min_win_rate=0.3, top_n=3)
        import pandas as pd
        signal = strategy.generate_signal(pd.DataFrame())

        assert signal.signal == Signal.HOLD

    def test_mark_copied(self):
        strategy = CopyTradeStrategy(min_trades=5, min_win_rate=0.3, top_n=3)
        sig = CopySignal(wallet="0xabc", token_id="t1", market_slug="Test", side="BUY", price=0.45, wallet_score=0.8)
        strategy.mark_copied(sig)

        assert "0xabc:t1:BUY" in strategy._known_positions
