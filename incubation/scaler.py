"""Position size scaler for gradual incubation ramp-up.

Scaling ladder: $1 -> $5 -> $10 -> $50 -> $100
Each level requires consistent profitability over a minimum number of trades.
Scales DOWN on poor performance to protect capital.
"""

import logging

logger = logging.getLogger(__name__)

SCALING_LADDER = [1.0, 5.0, 10.0, 50.0, 100.0]
MIN_TRADES_PER_LEVEL = 20
MIN_WIN_RATE = 0.55
MIN_PROFIT_FACTOR = 1.3

# Level-down thresholds
MIN_WIN_RATE_FLOOR = 0.40
MAX_CONSECUTIVE_LOSSES = 5


class Scaler:
    """Automatically scale position size based on live performance."""

    def __init__(self, starting_level: int = 0):
        self.level = starting_level
        self._trades_at_level = 0
        self._wins_at_level = 0
        self._gross_profit = 0.0
        self._gross_loss = 0.0
        self._consecutive_losses = 0

    @property
    def current_size(self) -> float:
        return SCALING_LADDER[min(self.level, len(SCALING_LADDER) - 1)]

    def record_trade(self, pnl: float) -> None:
        """Record a trade result and check for level-up or level-down."""
        self._trades_at_level += 1
        if pnl > 0:
            self._wins_at_level += 1
            self._gross_profit += pnl
            self._consecutive_losses = 0
        else:
            self._gross_loss += abs(pnl)
            self._consecutive_losses += 1

        # Immediate level-down on consecutive losses
        if self._consecutive_losses >= MAX_CONSECUTIVE_LOSSES and self.level > 0:
            self._level_down(f"{self._consecutive_losses} consecutive losses")
            return

        if self._trades_at_level >= MIN_TRADES_PER_LEVEL:
            self._evaluate_level_change()

    def _evaluate_level_change(self) -> None:
        win_rate = self._wins_at_level / self._trades_at_level
        pf = self._gross_profit / self._gross_loss if self._gross_loss > 0 else float("inf")

        if win_rate >= MIN_WIN_RATE and pf >= MIN_PROFIT_FACTOR:
            if self.level < len(SCALING_LADDER) - 1:
                old = self.current_size
                self.level += 1
                logger.info(
                    "LEVEL UP: $%.0f -> $%.0f (win rate: %.1f%%, PF: %.2f over %d trades)",
                    old, self.current_size, win_rate * 100, pf, self._trades_at_level,
                )
                self._reset_level_stats()
        elif win_rate < MIN_WIN_RATE_FLOOR and self.level > 0:
            self._level_down(f"poor performance (win rate: {win_rate:.1%}, PF: {pf:.2f})")
        else:
            logger.info(
                "Staying at $%.0f (win rate: %.1f%%, PF: %.2f — need %.0f%% / %.1f)",
                self.current_size, win_rate * 100, pf,
                MIN_WIN_RATE * 100, MIN_PROFIT_FACTOR,
            )
            self._reset_level_stats()

    def _level_down(self, reason: str) -> None:
        old = self.current_size
        self.level = max(0, self.level - 1)
        logger.warning(
            "LEVEL DOWN: $%.0f -> $%.0f (%s)", old, self.current_size, reason
        )
        self._reset_level_stats()
        self._consecutive_losses = 0

    def _reset_level_stats(self) -> None:
        self._trades_at_level = 0
        self._wins_at_level = 0
        self._gross_profit = 0.0
        self._gross_loss = 0.0
