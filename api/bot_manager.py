"""Bot manager — runs trading bots in background threads."""

import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from config import settings
from data.polymarket_client import PolymarketClient
from data.downloader import OHLCVDownloader
from bot.trader import Trader
from bot.risk_manager import RiskManager
from incubation.alerter import Alerter
from incubation.scaler import Scaler
from incubation.logger import setup_logging, log_trade_event
from strategies.macd_strategy import MACDStrategy
from strategies.rsi_mean_reversion import RSIMeanReversionStrategy
from strategies.cvd_strategy import CVDStrategy
from strategies.base_strategy import Signal
from strategies.copytrade_strategy import CopyTradeStrategy

logger = logging.getLogger(__name__)

STRATEGY_MAP = {
    "macd": MACDStrategy,
    "rsi": RSIMeanReversionStrategy,
    "cvd": CVDStrategy,
    "copytrade": CopyTradeStrategy,
}

STRATEGY_META = {
    "macd": {"name": "MACD (3/15/3)", "desc": "Momentum · Trend Following", "color": "#00e676"},
    "rsi": {"name": "RSI + VWAP", "desc": "Mean Reversion · Counter-Trend", "color": "#448aff"},
    "cvd": {"name": "CVD Divergence", "desc": "Volume Delta · Reversal Detection", "color": "#b388ff"},
    "copytrade": {"name": "Copy Trading", "desc": "Top Wallets · Signal Mirroring", "color": "#ff9100"},
}


@dataclass
class BotState:
    """Runtime state for a single bot."""
    strategy_key: str
    running: bool = False
    thread: Optional[threading.Thread] = None
    trader: Optional[Trader] = None
    trades: list = field(default_factory=list)
    pnl_history: list = field(default_factory=list)
    total_pnl: float = 0.0
    win_count: int = 0
    loss_count: int = 0
    started_at: Optional[str] = None

    @property
    def total_trades(self) -> int:
        return self.win_count + self.loss_count

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.win_count / self.total_trades

    def record_trade(self, side: str, price: float, size: float, pnl: float, token_id: str = ""):
        trade = {
            "time": datetime.now(timezone.utc).isoformat(),
            "strategy": STRATEGY_META[self.strategy_key]["name"],
            "side": side,
            "price": price,
            "size": size,
            "pnl": round(pnl, 4),
            "status": "filled",
        }
        self.trades.append(trade)
        self.total_pnl += pnl
        self.pnl_history.append(round(self.total_pnl, 4))
        if pnl > 0:
            self.win_count += 1
        else:
            self.loss_count += 1

    def to_dict(self) -> dict:
        meta = STRATEGY_META[self.strategy_key]
        return {
            "key": self.strategy_key,
            "name": meta["name"],
            "desc": meta["desc"],
            "color": meta["color"],
            "running": self.running,
            "win_rate": round(self.win_rate * 100, 1),
            "total_pnl": round(self.total_pnl, 2),
            "total_trades": self.total_trades,
            "pnl_history": self.pnl_history[-60:],
            "started_at": self.started_at,
        }


class BotManager:
    """Singleton manager for all trading bots."""

    def __init__(self):
        self.bots: dict[str, BotState] = {
            "macd": BotState(strategy_key="macd"),
            "rsi": BotState(strategy_key="rsi"),
            "cvd": BotState(strategy_key="cvd"),
            "copytrade": BotState(strategy_key="copytrade"),
        }
        self.risk_manager = RiskManager()
        self.alerter = Alerter(
            loss_threshold=settings.ALERT_LOSS_THRESHOLD,
            gain_threshold=settings.ALERT_GAIN_THRESHOLD,
            daily_loss_threshold=settings.ALERT_DAILY_LOSS_THRESHOLD,
            daily_gain_threshold=settings.ALERT_DAILY_GAIN_THRESHOLD,
        )
        self._settings = {
            "position_size": settings.runtime.default_position_size,
            "stop_loss_pct": settings.runtime.stop_loss_pct * 100,
            "take_profit_pct": settings.runtime.take_profit_pct * 100,
            "dry_run": settings.runtime.dry_run,
            "account": "account_1",
        }
        self._lock = threading.Lock()
        self._logs: list[dict] = []
        self._max_logs = 200

        # Setup file logging
        setup_logging("bot_manager")

    def start_bot(self, key: str, token_id: str = "") -> dict:
        """Start a bot in a background thread.

        Args:
            key: Strategy key (macd, rsi, cvd)
            token_id: Polymarket token ID. Falls back to settings, then env var.
        """
        if key not in self.bots:
            return {"error": f"Unknown bot: {key}"}

        bot = self.bots[key]
        if bot.running:
            return {"status": "already_running"}

        # Resolve token_id: parameter > settings > env
        resolved_token_id = token_id or settings.runtime.token_id
        if not resolved_token_id:
            logger.warning("No token_id configured — running in demo mode")
            resolved_token_id = "demo"

        strategy_cls = STRATEGY_MAP[key]
        strategy = strategy_cls()

        dry_run = self._settings["dry_run"]
        client = PolymarketClient(dry_run=dry_run)
        # Don't connect in dry run — no real orders
        if not dry_run:
            try:
                client.connect()
            except Exception as e:
                return {"error": f"Connection failed: {e}"}

        scaler = Scaler(alerter=self.alerter)

        trader = Trader(
            strategy=strategy,
            client=client,
            token_id=resolved_token_id,
            position_size=self._settings["position_size"],
            account_name=self._settings["account"],
            scaler=scaler,
        )

        # Bridge trader events to BotState
        def on_trade(side: str, price: float, size: float, pnl: float):
            with self._lock:
                bot.record_trade(side, price, size, pnl)
                if pnl != 0:
                    self.risk_manager.on_trade_closed(pnl)
                    strategy_name = STRATEGY_META[key]["name"]
                    pnl_str = f"+${pnl:.4f}" if pnl >= 0 else f"-${abs(pnl):.4f}"
                    self._log_event("TRADE", key, f"{side} @ {price:.4f} | PnL: {pnl_str}")
                    self.alerter.check_trade(strategy_name, side, price, size, pnl)
                    self.alerter.check_daily_pnl(self.risk_manager.daily_pnl)
                else:
                    self.risk_manager.on_trade_opened()

        trader.on_trade = on_trade

        bot.trader = trader
        bot.running = True
        bot.started_at = datetime.now(timezone.utc).isoformat()
        self._log_event("INFO", key, f"Bot demarre ({('demo' if resolved_token_id == 'demo' else 'live')})")

        is_demo = resolved_token_id == "demo"

        def _run():
            """Trading loop — demo mode or live data via ccxt."""
            logger.info("Bot %s started (%s)", key, "demo" if is_demo else resolved_token_id[:16])

            DEMO_MARKETS = [
                "US Election 2026", "Fed Rate Cut", "Bitcoin 100k",
                "Trump Approval >50%", "Gold >3500", "S&P 500 ATH",
            ]

            if is_demo:
                # Demo mode: simulated trades for dashboard preview
                random.seed(time.time() + hash(key))
                while bot.running:
                    try:
                        if random.random() < 0.5:
                            side = "BUY" if random.random() > 0.45 else "SELL"
                            price = round(0.3 + random.random() * 0.5, 4)
                            size = self._settings["position_size"]
                            pnl = round((random.random() - 0.4) * size * 0.15, 4)
                            with self._lock:
                                bot.record_trade(side, price, size, pnl)
                                self.risk_manager.on_trade_opened()
                                self.risk_manager.on_trade_closed(pnl)
                        time.sleep(2)
                    except Exception as e:
                        logger.error("Bot %s error: %s", key, e)
                        self._log_event("ERROR", key, str(e))
                        time.sleep(2)

            elif key == "copytrade":
                # Copy-trade mode: scan wallets, detect signals, execute
                interval = settings.COPYTRADE_SCAN_INTERVAL
                try:
                    while bot.running:
                        try:
                            signal = strategy.generate_signal(pd.DataFrame())
                            if signal.signal != Signal.HOLD and strategy._last_signals:
                                for sig in strategy._last_signals:
                                    # Update token_id dynamically
                                    trader.token_id = sig.token_id
                                    dummy_df = pd.DataFrame({
                                        "close": [sig.price], "open": [sig.price],
                                        "high": [sig.price], "low": [sig.price],
                                        "volume": [0], "timestamp": [pd.Timestamp.now()],
                                    })
                                    trader.execute_once(dummy_df)
                                    strategy.mark_copied(sig)
                            time.sleep(interval)
                        except Exception as e:
                            logger.error("Bot %s cycle error: %s", key, e)
                            time.sleep(interval)
                except Exception as e:
                    logger.error("Bot %s fatal error: %s", key, e)
                    self.alerter.notify_bot_error(key, str(e))

            else:
                # Live mode: fetch real data via ccxt
                interval = 30
                try:
                    downloader = OHLCVDownloader()

                    def fetch_data():
                        return downloader.fetch(symbol="BTC/USDT", timeframe="5m", days_back=1)

                    while bot.running:
                        try:
                            df = fetch_data()
                            if df is not None and not df.empty:
                                trader.execute_once(df)
                            else:
                                logger.warning("Bot %s: no data, retrying...", key)
                            time.sleep(interval)
                        except Exception as e:
                            logger.error("Bot %s cycle error: %s", key, e)
                            time.sleep(interval)
                except Exception as e:
                    logger.error("Bot %s fatal error: %s", key, e)
                    self.alerter.notify_bot_error(key, str(e))

            logger.info("Bot %s stopped", key)

        thread = threading.Thread(target=_run, daemon=True, name=f"bot-{key}")
        bot.thread = thread
        thread.start()

        return {"status": "started", "bot": key}

    def stop_bot(self, key: str) -> dict:
        if key not in self.bots:
            return {"error": f"Unknown bot: {key}"}

        bot = self.bots[key]
        if not bot.running:
            return {"status": "already_stopped"}

        bot.running = False
        if bot.thread is not None:
            bot.thread.join(timeout=10)
            bot.thread = None
        bot.started_at = None
        self._log_event("INFO", key, "Bot arrete")
        return {"status": "stopped", "bot": key}

    def kill_all(self) -> dict:
        stopped = []
        for key in self.bots:
            if self.bots[key].running:
                self.bots[key].running = False
                stopped.append(key)
        if stopped:
            self._log_event("WARN", "system", f"KILL ALL — bots arretes: {', '.join(stopped)}")
            self.alerter.notify_kill_all(stopped)
        # Join all threads after setting flags
        for key in stopped:
            bot = self.bots[key]
            if bot.thread is not None:
                bot.thread.join(timeout=10)
                bot.thread = None
            bot.started_at = None
        return {"status": "killed", "bots": stopped}

    def get_all_bots(self) -> list[dict]:
        return [bot.to_dict() for bot in self.bots.values()]

    def get_metrics(self) -> dict:
        total_pnl = sum(b.total_pnl for b in self.bots.values())
        total_trades = sum(b.total_trades for b in self.bots.values())
        best = max(self.bots.values(), key=lambda b: b.win_rate)
        best_name = STRATEGY_META[best.strategy_key]["name"]

        # Per-trade Sharpe (no annualization — trade frequency varies)
        all_pnls = []
        for b in self.bots.values():
            all_pnls.extend(
                [t["pnl"] for t in b.trades]
            )
        if len(all_pnls) > 1:
            arr = np.array(all_pnls)
            sharpe = (arr.mean() / arr.std()) if arr.std() > 0 else 0.0
        else:
            sharpe = 0.0

        return {
            "total_pnl": round(total_pnl, 2),
            "best_strategy": best_name,
            "best_win_rate": round(best.win_rate * 100, 1),
            "total_trades": total_trades,
            "sharpe_ratio": round(sharpe, 2),
        }

    def get_trades(self, limit: int = 50) -> list[dict]:
        all_trades = []
        for bot in self.bots.values():
            all_trades.extend(bot.trades)
        all_trades.sort(key=lambda t: t["time"], reverse=True)
        return all_trades[:limit]

    def get_risk(self) -> dict:
        daily_pnl = self.risk_manager.daily_pnl
        max_loss = self.risk_manager.max_daily_loss
        open_pos = self.risk_manager.open_positions
        max_pos = self.risk_manager.max_open_positions
        running_bots = [k for k, b in self.bots.items() if b.running]

        return {
            "daily_pnl": round(daily_pnl, 2),
            "max_daily_loss": max_loss,
            "daily_loss_pct": round(abs(daily_pnl) / max_loss * 100, 1) if max_loss > 0 else 0,
            "open_positions": open_pos,
            "max_positions": max_pos,
            "position_size": self._settings["position_size"],
            "running_bots": running_bots,
        }

    def get_settings(self) -> dict:
        return dict(self._settings)

    def update_settings(self, new_settings: dict) -> dict:
        for k, v in new_settings.items():
            if k in self._settings:
                self._settings[k] = v
        # Update thread-safe runtime settings (no global mutation)
        settings.runtime.update(
            dry_run=self._settings["dry_run"],
            default_position_size=self._settings["position_size"],
            stop_loss_pct=self._settings["stop_loss_pct"] / 100,
            take_profit_pct=self._settings["take_profit_pct"] / 100,
        )
        # Sync risk manager with new values
        self.risk_manager.max_position_size = settings.runtime.max_position_size
        self.risk_manager.max_daily_loss = settings.runtime.max_daily_loss
        self.risk_manager.max_open_positions = settings.runtime.max_open_positions
        self.risk_manager.stop_loss_pct = settings.runtime.stop_loss_pct
        return self._settings

    def _log_event(self, level: str, source: str, message: str) -> None:
        """Add a log entry to the in-memory buffer and file."""
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "source": source,
            "message": message,
        }
        with self._lock:
            self._logs.append(entry)
            if len(self._logs) > self._max_logs:
                self._logs = self._logs[-self._max_logs:]
        log_trade_event("system", entry)

    def get_logs(self, limit: int = 100, level: str = "") -> list[dict]:
        """Return recent logs, optionally filtered by level."""
        logs = list(self._logs)
        if level:
            logs = [l for l in logs if l["level"] == level.upper()]
        return logs[-limit:]
