"""Main trader — orchestrates strategy, risk, orders, and positions."""

import logging
import time
from typing import Callable, Optional

import pandas as pd

from config import settings
from data.polymarket_client import PolymarketClient
from bot.risk_manager import RiskManager
from bot.order_manager import OrderManager
from bot.position_tracker import PositionTracker
from incubation.scaler import Scaler
from strategies.base_strategy import BaseStrategy, Signal
from data.storage import log_trade

logger = logging.getLogger(__name__)


class Trader:
    """The main trading loop: fetch data -> signal -> risk check -> execute."""

    def __init__(
        self,
        strategy: BaseStrategy,
        client: PolymarketClient,
        token_id: str,
        position_size: float = None,
        account_name: str = "default",
        scaler: Optional[Scaler] = None,
    ):
        self.strategy = strategy
        self.client = client
        self.token_id = token_id
        self.position_size = position_size if position_size is not None else settings.runtime.default_position_size
        self.account_name = account_name

        self.risk = RiskManager()
        self.orders = OrderManager(client)
        self.positions = PositionTracker()
        self.scaler = scaler
        self._running = False
        self._cached_capital: float = 0.0

        # Callback for trade events: on_trade(side, price, size, pnl)
        self.on_trade: Optional[Callable[[str, float, float, float], None]] = None

    def _close_position(self, price: float, reason: str) -> None:
        """Close the current position and record the trade."""
        pos = self.positions.get_position(self.token_id)
        if pos is None:
            return

        self.orders.cancel_all(self.token_id)
        exit_side = "SELL" if pos.side == "BUY" else "BUY"
        self.orders.place_order(self.token_id, exit_side, price, pos.size)
        pnl = self.positions.close_position(self.token_id, price)

        if pnl is not None:
            self.risk.on_trade_closed(pnl)
            if self.scaler:
                self.scaler.record_trade(pnl)
            log_trade(
                strategy=self.strategy.name,
                side=exit_side,
                price=price,
                size=pos.size,
                token_id=self.token_id,
                pnl=pnl,
                account=self.account_name,
            )
            logger.info("Position closed (%s): %s @ %.4f -> PnL $%.4f", reason, exit_side, price, pnl)
            if self.on_trade:
                self.on_trade(exit_side, price, pos.size, pnl)

    def _get_dynamic_size(self, price: float) -> float:
        """Calculate position size from risk %, capital, and stop-loss."""
        # Use scaler size as upper bound if available
        max_from_scaler = self.scaler.current_size if self.scaler else self.position_size

        # Try to fetch real capital for risk-based sizing
        capital = self._cached_capital
        if capital > 0 and price > 0:
            risk_size = self.risk.calculate_position_size(capital, price)
            return min(risk_size, max_from_scaler)

        return max_from_scaler

    def execute_once(self, df: pd.DataFrame) -> None:
        """Run one cycle of the trading loop.

        Args:
            df: Latest OHLCV data with enough history for indicators
        """
        df = self.strategy.compute_indicators(df.copy())
        signal = self.strategy.generate_signal(df)
        price = signal.price

        logger.info(
            "[%s] Signal: %s @ %.4f (confidence: %.2f) — %s",
            self.strategy.name, signal.signal.value, price, signal.confidence, signal.reason,
        )

        has_pos = self.positions.has_position(self.token_id)

        # --- Check SL / TP / Trailing on open position ---
        if has_pos:
            pos = self.positions.get_position(self.token_id)

            # 1) Stop-Loss check
            if self.risk.should_stop_loss(pos.entry_price, price, pos.side):
                logger.warning(
                    "STOP-LOSS hit: entry=%.4f, current=%.4f, SL=%.0f%%",
                    pos.entry_price, price, self.risk.stop_loss_pct * 100,
                )
                self._close_position(price, "stop-loss")
                return

            # 2) Trailing Take-Profit check (if enabled)
            trailing_enabled = settings.runtime.trailing_tp_enabled
            if trailing_enabled:
                activation = settings.runtime.trailing_tp_activation
                distance = settings.runtime.trailing_tp_distance
                if self.positions.should_trailing_tp(self.token_id, price, activation, distance):
                    logger.info(
                        "TRAILING TP triggered: entry=%.4f, peak=%.4f, current=%.4f",
                        pos.entry_price, pos.peak_price, price,
                    )
                    self._close_position(price, "trailing-tp")
                    return
                # Update peak even if not triggered
                self.positions.update_peak_price(self.token_id, price)

            # 3) Fixed Take-Profit check (only if trailing is off)
            if not trailing_enabled and self.risk.should_take_profit(pos.entry_price, price, pos.side):
                logger.info(
                    "TAKE-PROFIT hit: entry=%.4f, current=%.4f, TP=%.0f%%",
                    pos.entry_price, price, self.risk.take_profit_pct * 100,
                )
                self._close_position(price, "take-profit")
                return

            # 4) Close on opposite signal
            should_close = (
                (pos.side == "BUY" and signal.signal == Signal.SELL)
                or (pos.side == "SELL" and signal.signal == Signal.BUY)
            )
            if should_close:
                self._close_position(price, "signal-reversal")
                has_pos = False
            else:
                return  # Hold position, no new entry

        # --- Open new position ---
        if not has_pos and signal.signal in (Signal.BUY, Signal.SELL):
            trade_size = self._get_dynamic_size(price)

            allowed, reason = self.risk.can_trade(trade_size)
            if not allowed:
                logger.warning("Risk manager blocked trade: %s", reason)
                return

            side = signal.signal.value
            self.orders.place_order(self.token_id, side, price, trade_size)
            self.positions.open_position(
                self.token_id, side, price, trade_size, self.strategy.name,
            )
            self.risk.on_trade_opened()
            log_trade(
                strategy=self.strategy.name,
                side=side,
                price=price,
                size=trade_size,
                token_id=self.token_id,
                account=self.account_name,
            )
            logger.info(
                "Position opened: %s $%.2f @ %.4f (risk-sized)",
                side, trade_size, price,
            )
            if self.on_trade:
                self.on_trade(side, price, trade_size, 0.0)

    def _refresh_capital(self) -> None:
        """Fetch wallet balance for risk-based position sizing."""
        try:
            balance = self.client.get_balance()
            if balance > 0:
                self._cached_capital = balance
                logger.info("Capital refreshed: $%.2f", balance)
        except Exception as e:
            logger.warning("Could not refresh capital: %s", e)

    def run_loop(
        self,
        data_fetcher: Optional[Callable[[], pd.DataFrame]] = None,
        df: Optional[pd.DataFrame] = None,
        interval_seconds: int = 300,
    ) -> None:
        """Continuous trading loop.

        Args:
            data_fetcher: Callable that returns fresh OHLCV DataFrame (live mode)
            df: Static DataFrame to replay (dev/backtest mode)
            interval_seconds: Seconds between cycles in live mode
        """
        self._running = True
        logger.info("Starting trader: %s on %s", self.strategy.name, self.token_id[:16])

        # Fetch initial capital for dynamic sizing
        self._refresh_capital()
        _cycles_since_refresh = 0
        _refresh_every = 20  # refresh capital every ~20 cycles

        if data_fetcher is not None:
            # Live mode: fetch new data each cycle
            while self._running:
                try:
                    # Periodically refresh capital
                    _cycles_since_refresh += 1
                    if _cycles_since_refresh >= _refresh_every:
                        self._refresh_capital()
                        _cycles_since_refresh = 0

                    live_df = data_fetcher()
                    if live_df is not None and not live_df.empty:
                        self.execute_once(live_df)
                    else:
                        logger.warning("No data received, retrying in %ds", interval_seconds)
                except Exception as e:
                    logger.error("Error in trading loop: %s", e)
                time.sleep(interval_seconds)
        elif df is not None:
            # Replay mode: iterate static data (for dev/backtest)
            df = self.strategy.compute_indicators(df.copy())
            min_rows = 30
            for i in range(min_rows, len(df)):
                if not self._running:
                    break
                chunk = df.iloc[: i + 1]
                self.execute_once(chunk)
        else:
            logger.error("No data source provided — pass data_fetcher or df")

        logger.info(
            "Trader stopped. Total PnL: $%.4f over %d trades",
            self.positions.total_realized_pnl,
            self.positions.trade_count,
        )

    def stop(self) -> None:
        self._running = False
