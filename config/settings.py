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

# --- Alerts ---
# Email (SMTP)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO", SMTP_USER)

# Telegram (optionnel, en complement ou a la place)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
ALERT_LOSS_THRESHOLD = float(os.getenv("ALERT_LOSS_THRESHOLD", "5"))
ALERT_GAIN_THRESHOLD = float(os.getenv("ALERT_GAIN_THRESHOLD", "10"))
ALERT_DAILY_LOSS_THRESHOLD = float(os.getenv("ALERT_DAILY_LOSS_THRESHOLD", "20"))
ALERT_DAILY_GAIN_THRESHOLD = float(os.getenv("ALERT_DAILY_GAIN_THRESHOLD", "50"))

# --- Strategy defaults (constants) ---
MACD_FAST = 3
MACD_SLOW = 15
MACD_SIGNAL = 3

RSI_PERIOD = 14
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

CVD_LOOKBACK = 20

# --- Copy-Trading defaults ---
COPYTRADE_MIN_TRADES = int(os.getenv("COPYTRADE_MIN_TRADES", "20"))
COPYTRADE_MIN_WIN_RATE = float(os.getenv("COPYTRADE_MIN_WIN_RATE", "0.55"))
COPYTRADE_TOP_N = int(os.getenv("COPYTRADE_TOP_N", "6"))
COPYTRADE_SCAN_INTERVAL = int(os.getenv("COPYTRADE_SCAN_INTERVAL", "60"))
COPYTRADE_RESCORE_INTERVAL = int(os.getenv("COPYTRADE_RESCORE_INTERVAL", "3600"))


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
    risk_per_trade_pct: float = 0.01
    trailing_tp_enabled: bool = False
    trailing_tp_activation: float = 0.30
    trailing_tp_distance: float = 0.15
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
                "risk_per_trade_pct": self.risk_per_trade_pct,
                "trailing_tp_enabled": self.trailing_tp_enabled,
                "trailing_tp_activation": self.trailing_tp_activation,
                "trailing_tp_distance": self.trailing_tp_distance,
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
    risk_per_trade_pct=float(os.getenv("RISK_PER_TRADE_PCT", "0.01")),
    trailing_tp_enabled=os.getenv("TRAILING_TP_ENABLED", "false").lower() == "true",
    trailing_tp_activation=float(os.getenv("TRAILING_TP_ACTIVATION", "0.30")),
    trailing_tp_distance=float(os.getenv("TRAILING_TP_DISTANCE", "0.15")),
    log_level=os.getenv("LOG_LEVEL", "INFO"),
    token_id=os.getenv("POLYMARKET_TOKEN_ID", ""),
)
