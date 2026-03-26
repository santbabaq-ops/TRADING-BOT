"""Backtest engine — runs a strategy against historical data."""

import logging

import pandas as pd

from strategies.base_strategy import BaseStrategy, Signal
from backtesting.metrics import BacktestResult, compute_metrics
from config import settings

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Simulate strategy execution on historical candles."""

    def __init__(
        self,
        strategy: BaseStrategy,
        position_size: float = settings.DEFAULT_POSITION_SIZE,
        stop_loss_pct: float = settings.STOP_LOSS_PCT,
        take_profit_pct: float = settings.TAKE_PROFIT_PCT,
        commission: float = 0.0,  # Polymarket limit orders = 0 fees
    ):
        self.strategy = strategy
        self.position_size = position_size
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.commission = commission

    def run(self, df: pd.DataFrame) -> BacktestResult:
        """Run backtest and return results.

        Args:
            df: OHLCV DataFrame with columns: timestamp, open, high, low, close, volume
        """
        df = self.strategy.compute_indicators(df.copy())

        trades = []
        position = None  # {"side": "BUY", "entry_price": ..., "size": ...}

        for i in range(1, len(df)):
            chunk = df.iloc[: i + 1]
            curr = df.iloc[i]
            price = curr["close"]

            # Check stop-loss / take-profit on intra-candle high/low
            if position is not None:
                entry = position["entry_price"]
                high = curr["high"]
                low = curr["low"]

                if position["side"] == "BUY":
                    sl_price = entry * (1 - self.stop_loss_pct)
                    tp_price = entry * (1 + self.take_profit_pct)
                    sl_hit = low <= sl_price
                    tp_hit = high >= tp_price
                else:
                    sl_price = entry * (1 + self.stop_loss_pct)
                    tp_price = entry * (1 - self.take_profit_pct)
                    sl_hit = high >= sl_price
                    tp_hit = low <= tp_price

                # If both could be hit in same candle, assume stop-loss first (conservative)
                if sl_hit:
                    exit_price = sl_price
                    pnl_pct = -self.stop_loss_pct
                    pnl = pnl_pct * self.position_size - self.commission
                    trades.append({
                        "entry_price": entry,
                        "exit_price": round(exit_price, 6),
                        "side": position["side"],
                        "size": self.position_size,
                        "pnl": pnl,
                        "exit_reason": "stop_loss",
                        "timestamp": curr["timestamp"],
                    })
                    position = None
                    continue

                if tp_hit:
                    exit_price = tp_price
                    pnl_pct = self.take_profit_pct
                    pnl = pnl_pct * self.position_size - self.commission
                    trades.append({
                        "entry_price": entry,
                        "exit_price": round(exit_price, 6),
                        "side": position["side"],
                        "size": self.position_size,
                        "pnl": pnl,
                        "exit_reason": "take_profit",
                        "timestamp": curr["timestamp"],
                    })
                    position = None
                    continue

            signal = self.strategy.generate_signal(chunk)

            # Open position
            if position is None and signal.signal in (Signal.BUY, Signal.SELL):
                position = {
                    "side": signal.signal.value,
                    "entry_price": price,
                    "size": self.position_size,
                }
                continue

            # Close position on opposite signal
            if position is not None:
                should_close = (
                    (position["side"] == "BUY" and signal.signal == Signal.SELL)
                    or (position["side"] == "SELL" and signal.signal == Signal.BUY)
                )
                if should_close:
                    entry = position["entry_price"]
                    if position["side"] == "BUY":
                        pnl_pct = (price - entry) / entry
                    else:
                        pnl_pct = (entry - price) / entry
                    pnl = pnl_pct * self.position_size - self.commission
                    trades.append({
                        "entry_price": entry,
                        "exit_price": price,
                        "side": position["side"],
                        "size": self.position_size,
                        "pnl": pnl,
                        "exit_reason": "signal_reversal",
                        "timestamp": curr["timestamp"],
                    })
                    # Open new position in opposite direction
                    position = {
                        "side": signal.signal.value,
                        "entry_price": price,
                        "size": self.position_size,
                    }

        # Close any remaining position at last price
        if position is not None:
            price = df.iloc[-1]["close"]
            entry = position["entry_price"]
            if position["side"] == "BUY":
                pnl_pct = (price - entry) / entry
            else:
                pnl_pct = (entry - price) / entry
            pnl = pnl_pct * self.position_size - self.commission
            trades.append({
                "entry_price": entry,
                "exit_price": price,
                "side": position["side"],
                "size": self.position_size,
                "pnl": pnl,
                "exit_reason": "end_of_data",
                "timestamp": df.iloc[-1]["timestamp"],
            })

        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame(
            columns=["entry_price", "exit_price", "side", "size", "pnl", "exit_reason", "timestamp"]
        )
        return compute_metrics(trades_df, self.strategy.name)
