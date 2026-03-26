"""Global bot configuration — thread-safe settings."""

import os
import threading
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()

# --- Constants (never change at runtime) ---
POLYMARKET_HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet
SIGNATURE_TYPE = 2  # EIP-1271
ORDER_TYPE = "limit"  # always limit — market orders have fees on Polymarket

PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")

# --- Strategy defaults (constants) ---
MACD_FAST = 3
MACD_SLOW = 15
MACD_SIGNAL = 3

RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

CVD_LOOKBACK = 20


@dataclass
class Settings:
    """Thread-safe mutable settings. Use update() to change values."""

    dry_run: bool = True
    default_position_size: float = 1.0
    default_timeframe: str = "5m"
    max_position_size: float = 10.0
    max_daily_loss: float = 50.0
    max_open_positions: int = 3
    stop_loss_pct: float = 0.05
    take_profit_pct: float = 0.10
    log_level: str = "INFO"
    token_id: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def update(self, **kwargs) -> None:
        """Thread-safe update of settings."""
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k) and not k.startswith("_"):
                    setattr(self, k, v)

    def snapshot(self) -> dict:
        """Return a thread-safe copy of current settings."""
        with self._lock:
            return {
                "dry_run": self.dry_run,
                "default_position_size": self.default_position_size,
                "default_timeframe": self.default_timeframe,
                "max_position_size": self.max_position_size,
                "max_daily_loss": self.max_daily_loss,
                "max_open_positions": self.max_open_positions,
                "stop_loss_pct": self.stop_loss_pct,
                "take_profit_pct": self.take_profit_pct,
                "log_level": self.log_level,
                "token_id": self.token_id,
            }


# Singleton instance — loaded from env
runtime = Settings(
    dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
    default_position_size=1.0,
    max_position_size=float(os.getenv("MAX_POSITION_SIZE", "10")),
    max_daily_loss=float(os.getenv("MAX_DAILY_LOSS", "50")),
    max_open_positions=int(os.getenv("MAX_OPEN_POSITIONS", "3")),
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    token_id=os.getenv("POLYMARKET_TOKEN_ID", ""),
)

# --- Backward compatibility: module-level aliases ---
DEFAULT_TIMEFRAME = runtime.default_timeframe
DEFAULT_POSITION_SIZE = runtime.default_position_size
MAX_POSITION_SIZE = runtime.max_position_size
MAX_DAILY_LOSS = runtime.max_daily_loss
MAX_OPEN_POSITIONS = runtime.max_open_positions
STOP_LOSS_PCT = runtime.stop_loss_pct
TAKE_PROFIT_PCT = runtime.take_profit_pct
DRY_RUN = runtime.dry_run
LOG_LEVEL = runtime.log_level
