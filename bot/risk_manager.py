"""Risk management — the last line of defense before any trade."""

import logging
from datetime import datetime, timezone

from config import settings

logger = logging.getLogger(__name__)


class RiskManager:
    """Enforces position limits, daily loss limits, size caps, and dynamic sizing."""

    def __init__(
        self,
        max_position_size: float = None,
        max_daily_loss: float = None,
        max_open_positions: int = None,
        stop_loss_pct: float = None,
        take_profit_pct: float = None,
        risk_per_trade_pct: float = None,
    ):
        self.max_position_size = max_position_size if max_position_size is not None else settings.runtime.max_position_size
        self.max_daily_loss = max_daily_loss if max_daily_loss is not None else settings.runtime.max_daily_loss
        self.max_open_positions = max_open_positions if max_open_positions is not None else settings.runtime.max_open_positions
        self.stop_loss_pct = stop_loss_pct if stop_loss_pct is not None else settings.runtime.stop_loss_pct
        self.take_profit_pct = take_profit_pct if take_profit_pct is not None else settings.runtime.take_profit_pct
        self.risk_per_trade_pct = risk_per_trade_pct if risk_per_trade_pct is not None else settings.runtime.risk_per_trade_pct

        self._daily_pnl = 0.0
        self._daily_reset_date = datetime.now(timezone.utc).date()
        self._open_positions = 0

    def _maybe_reset_daily(self) -> None:
        """Reset daily PnL counter at midnight UTC."""
        today = datetime.now(timezone.utc).date()
        if today != self._daily_reset_date:
            logger.info("New day — resetting daily PnL (was $%.2f)", self._daily_pnl)
            self._daily_pnl = 0.0
            self._daily_reset_date = today

    def can_trade(self, size: float) -> tuple[bool, str]:
        """Check if a new trade is allowed.

        Returns (allowed, reason).
        """
        self._maybe_reset_daily()

        if size > self.max_position_size:
            return False, f"Size ${size:.2f} exceeds max ${self.max_position_size:.2f}"

        if self._open_positions >= self.max_open_positions:
            return False, f"Max open positions reached ({self.max_open_positions})"

        if self._daily_pnl <= -self.max_daily_loss:
            return False, f"Daily loss limit hit (${self._daily_pnl:.2f})"

        return True, "ok"

    def on_trade_opened(self) -> None:
        """Track a newly opened position."""
        self._open_positions += 1
        logger.info("Position opened — %d open", self._open_positions)

    def on_trade_closed(self, pnl: float) -> None:
        """Track a closed position and update daily PnL."""
        self._maybe_reset_daily()
        self._open_positions = max(0, self._open_positions - 1)
        self._daily_pnl += pnl
        logger.info(
            "Position closed — PnL: $%.2f | Daily: $%.2f | Open: %d",
            pnl, self._daily_pnl, self._open_positions,
        )

    def get_stop_loss_price(self, entry_price: float, side: str) -> float:
        """Calculate stop-loss price for a given entry."""
        if side.upper() == "BUY":
            return entry_price * (1 - self.stop_loss_pct)
        return entry_price * (1 + self.stop_loss_pct)

    def get_take_profit_price(self, entry_price: float, side: str) -> float:
        """Calculate take-profit price for a given entry."""
        if side.upper() == "BUY":
            return entry_price * (1 + self.take_profit_pct)
        return entry_price * (1 - self.take_profit_pct)

    def calculate_position_size(self, capital: float, entry_price: float) -> float:
        """Calculate position size based on risk % of capital and stop-loss.

        Formula:
          max_loss_allowed = capital * risk_per_trade_pct
          loss_per_unit     = entry_price * stop_loss_pct
          size              = max_loss_allowed / loss_per_unit

        On Polymarket, buying shares at price P means you can lose up to P
        per share (binary outcome). With a stop-loss, your max loss per share
        is P * stop_loss_pct.

        The result is capped at max_position_size.
        """
        if capital <= 0 or entry_price <= 0:
            return self.max_position_size

        max_loss_allowed = capital * self.risk_per_trade_pct
        loss_per_unit = entry_price * self.stop_loss_pct

        if loss_per_unit <= 0:
            return self.max_position_size

        size = max_loss_allowed / loss_per_unit
        # Convert from number of shares to dollar amount
        size_usd = size * entry_price
        capped = min(size_usd, self.max_position_size)

        logger.info(
            "Dynamic sizing: capital=$%.2f, risk=%.1f%%, SL=%.0f%% -> $%.2f (capped $%.2f)",
            capital, self.risk_per_trade_pct * 100, self.stop_loss_pct * 100,
            size_usd, capped,
        )
        return round(capped, 2)

    def should_stop_loss(self, entry_price: float, current_price: float, side: str) -> bool:
        """Check if current price has hit the stop-loss level."""
        sl_price = self.get_stop_loss_price(entry_price, side)
        if side.upper() == "BUY":
            return current_price <= sl_price
        return current_price >= sl_price

    def should_take_profit(self, entry_price: float, current_price: float, side: str) -> bool:
        """Check if current price has hit the take-profit level."""
        tp_price = self.get_take_profit_price(entry_price, side)
        if side.upper() == "BUY":
            return current_price >= tp_price
        return current_price <= tp_price

    @property
    def daily_pnl(self) -> float:
        self._maybe_reset_daily()
        return self._daily_pnl

    @property
    def open_positions(self) -> int:
        return self._open_positions
