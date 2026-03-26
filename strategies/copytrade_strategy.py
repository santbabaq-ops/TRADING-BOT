"""Copy-trading strategy — follow top Polymarket wallets.

Scans the Polymarket API for profitable wallets, scores them,
and copies their trades when they enter or exit positions.
"""

import os

import pandas as pd

from strategies.base_strategy import BaseStrategy, Signal, TradeSignal
from data.wallet_scanner import WalletScanner, CopySignal


class CopyTradeStrategy(BaseStrategy):
    name = "copytrade"

    def __init__(
        self,
        min_trades: int = None,
        min_win_rate: float = None,
        top_n: int = None,
        rescore_interval: int = None,
    ):
        self.scanner = WalletScanner(
            min_trades=min_trades or int(os.getenv("COPYTRADE_MIN_TRADES", "20")),
            min_win_rate=min_win_rate or float(os.getenv("COPYTRADE_MIN_WIN_RATE", "0.55")),
            top_n=top_n or int(os.getenv("COPYTRADE_TOP_N", "6")),
            rescore_interval=rescore_interval or int(os.getenv("COPYTRADE_RESCORE_INTERVAL", "3600")),
        )
        self._known_positions: dict[str, CopySignal] = {}
        self._last_signals: list[CopySignal] = []

    def compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """For copy-trading, indicators are wallet scores (not technical)."""
        # If called with standard OHLCV (backtest compatibility), return as-is
        if "wallet" not in df.columns:
            return df

        top = self.scanner.get_top_wallets()
        scores = {w.address: w.composite_score for w in top}
        df["wallet_score"] = df["wallet"].map(scores).fillna(0)
        df["is_top_wallet"] = df["wallet"].isin(scores.keys())
        return df

    def generate_signal(self, df: pd.DataFrame) -> TradeSignal:
        """Detect new copy opportunities from top wallets."""
        signals = self.scanner.detect_new_positions(self._known_positions)
        self._last_signals = signals

        if not signals:
            return TradeSignal(Signal.HOLD, 0.0, 0.0, "No new copy signals")

        # Pick the strongest: highest wallet score
        best = max(signals, key=lambda s: s.wallet_score)

        # Confidence: how many top wallets agree on same direction + market
        agreeing = [s for s in signals if s.token_id == best.token_id and s.side == best.side]
        n_top = len(self.scanner.get_top_wallets()) or 1
        confidence = min(len(agreeing) / n_top * best.wallet_score, 1.0)

        signal_type = Signal.BUY if best.side == "BUY" else Signal.SELL
        reason = (
            f"Copy {best.wallet[:8]}... on '{best.market_slug}' "
            f"({len(agreeing)} wallets, score={best.wallet_score:.2f})"
        )

        return TradeSignal(signal_type, best.price, confidence, reason)

    @property
    def current_token_id(self) -> str:
        """The token_id changes dynamically based on what top wallets trade."""
        if self._last_signals:
            return self._last_signals[0].token_id
        return ""

    def mark_copied(self, signal: CopySignal) -> None:
        """Mark a signal as copied to avoid re-copying."""
        key = f"{signal.wallet}:{signal.token_id}:{signal.side}"
        self._known_positions[key] = signal
