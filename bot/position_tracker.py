"""Track open positions and PnL."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    token_id: str
    side: str
    entry_price: float
    size: float
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    strategy: str = ""
    peak_price: float = 0.0  # highest (BUY) or lowest (SELL) price since entry
    trailing_active: bool = False  # trailing TP activated


class PositionTracker:
    """Track all open and closed positions with PnL."""

    def __init__(self):
        self._positions: dict[str, Position] = {}  # key: token_id
        self._closed_pnl: list[float] = []

    @staticmethod
    def _compute_pnl(entry: float, exit_price: float, side: str, size: float) -> float:
        """Compute realized PnL as return percentage times notional size.

        For Polymarket binary outcomes (price 0-1):
          BUY  at 0.40, exit 0.50, size $10 -> pnl = +$2.50  (25% return on $10)
          SELL at 0.60, exit 0.50, size $10 -> pnl = +$1.67  (16.7% return on $10)
        """
        if side == "BUY":
            return (exit_price - entry) / entry * size
        return (entry - exit_price) / entry * size

    def open_position(
        self, token_id: str, side: str, entry_price: float, size: float, strategy: str = ""
    ) -> Position:
        pos = Position(
            token_id=token_id,
            side=side,
            entry_price=entry_price,
            size=size,
            strategy=strategy,
            peak_price=entry_price,
        )
        self._positions[token_id] = pos
        logger.info(
            "Position opened: %s %s %.2f @ %.4f [%s]",
            side, token_id[:16], size, entry_price, strategy,
        )
        return pos

    def close_position(self, token_id: str, exit_price: float) -> Optional[float]:
        """Close a position and return realized PnL."""
        pos = self._positions.pop(token_id, None)
        if pos is None:
            logger.warning("No open position for %s", token_id[:16])
            return None

        pnl = self._compute_pnl(pos.entry_price, exit_price, pos.side, pos.size)
        self._closed_pnl.append(pnl)
        logger.info(
            "Position closed: %s @ %.4f -> %.4f | PnL: $%.4f",
            token_id[:16], pos.entry_price, exit_price, pnl,
        )
        return pnl

    def get_position(self, token_id: str) -> Optional[Position]:
        return self._positions.get(token_id)

    def has_position(self, token_id: str) -> bool:
        return token_id in self._positions

    def update_peak_price(self, token_id: str, current_price: float) -> None:
        """Update the peak price for trailing take-profit tracking."""
        pos = self._positions.get(token_id)
        if pos is None:
            return
        if pos.side == "BUY":
            if current_price > pos.peak_price:
                pos.peak_price = current_price
        else:
            if pos.peak_price == 0 or current_price < pos.peak_price:
                pos.peak_price = current_price

    def should_trailing_tp(
        self, token_id: str, current_price: float,
        activation_pct: float, distance_pct: float,
    ) -> bool:
        """Check if trailing take-profit should trigger.

        1. Check if gain from entry >= activation_pct (trailing becomes active)
        2. If active, check if price has dropped distance_pct from peak
        """
        pos = self._positions.get(token_id)
        if pos is None:
            return False

        self.update_peak_price(token_id, current_price)

        if pos.side == "BUY":
            gain_from_entry = (pos.peak_price - pos.entry_price) / pos.entry_price
            if gain_from_entry < activation_pct:
                return False
            pos.trailing_active = True
            drop_from_peak = (pos.peak_price - current_price) / pos.peak_price
            return drop_from_peak >= distance_pct
        else:
            gain_from_entry = (pos.entry_price - pos.peak_price) / pos.entry_price
            if gain_from_entry < activation_pct:
                return False
            pos.trailing_active = True
            rise_from_peak = (current_price - pos.peak_price) / pos.peak_price
            return rise_from_peak >= distance_pct

    def unrealized_pnl(self, token_id: str, current_price: float) -> float:
        pos = self._positions.get(token_id)
        if pos is None:
            return 0.0
        return self._compute_pnl(pos.entry_price, current_price, pos.side, pos.size)

    @property
    def open_positions(self) -> list[Position]:
        return list(self._positions.values())

    @property
    def total_realized_pnl(self) -> float:
        return sum(self._closed_pnl)

    @property
    def trade_count(self) -> int:
        return len(self._closed_pnl)
