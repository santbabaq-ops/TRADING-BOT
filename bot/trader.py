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
        position_size: float = settings.DEFAULT_POSITION_SIZE,
        account_name: str = "default",
    ):
        self.strategy = strategy
        self.client = client
        self.token_id = token_id
        self.position_size = position_size
        self.account_name = account_name

        self.risk = RiskManager()
        self.orders = OrderManager(client)
        self.positions = PositionTracker()
        self._running = False

        # Callback for trade events: on_trade(side, price, size, pnl)
        self.on_trade: Optional[Callable[[str, float, float, float], None]] = None

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

        # --- Close position on opposite signal ---
        if has_pos:
            pos = self.positions.get_position(self.token_id)
            should_close = (
                (pos.side == "BUY" and signal.signal == Signal.SELL)
                or (pos.side == "SELL" and signal.signal == Signal.BUY)
            )
            if should_close:
                self.orders.cancel_all(self.token_id)
                # Place exit order
                exit_side = "SELL" if pos.side == "BUY" else "BUY"
                self.orders.place_order(self.token_id, exit_side, price, pos.size)
                pnl = self.positions.close_position(self.token_id, price)
                if pnl is not None:
                    self.risk.on_trade_closed(pnl)
                    log_trade(
                        strategy=self.strategy.name,
                        side=exit_side,
                        price=price,
                        size=pos.size,
                        token_id=self.token_id,
                        pnl=pnl,
                        account=self.account_name,
                    )
                    if self.on_trade:
                        self.on_trade(exit_side, price, pos.size, pnl)
                has_pos = False

        # --- Open new position ---
        if not has_pos and signal.signal in (Signal.BUY, Signal.SELL):
            allowed, reason = self.risk.can_trade(self.position_size)
            if not allowed:
                logger.warning("Risk manager blocked trade: %s", reason)
                return

            side = signal.signal.value
            self.orders.place_order(self.token_id, side, price, self.position_size)
            self.positions.open_position(
                self.token_id, side, price, self.position_size, self.strategy.name,
            )
            self.risk.on_trade_opened()
            log_trade(
                strategy=self.strategy.name,
                side=side,
                price=price,
                size=self.position_size,
                token_id=self.token_id,
                account=self.account_name,
            )
            if self.on_trade:
                self.on_trade(side, price, self.position_size, 0.0)

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

        if data_fetcher is not None:
            # Live mode: fetch new data each cycle
            while self._running:
                try:
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
