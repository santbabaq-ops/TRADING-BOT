"""Wallet scanner — discover and score profitable Polymarket traders."""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"


@dataclass
class WalletScore:
    """Score of a discovered wallet."""
    address: str
    win_rate: float = 0.0
    profit_factor: float = 0.0
    trade_count: int = 0
    avg_pnl: float = 0.0
    last_trade_at: str = ""
    composite_score: float = 0.0


@dataclass
class CopySignal:
    """A trade to copy from a top wallet."""
    wallet: str
    token_id: str
    market_slug: str
    side: str  # "BUY" or "SELL"
    price: float
    wallet_score: float
    timestamp: str = ""


class WalletScanner:
    """Discover profitable wallets on Polymarket and monitor their trades."""

    def __init__(
        self,
        min_trades: int = 20,
        min_win_rate: float = 0.55,
        top_n: int = 6,
        rescore_interval: int = 3600,
    ):
        self.min_trades = min_trades
        self.min_win_rate = min_win_rate
        self.top_n = top_n
        self.rescore_interval = rescore_interval

        self._top_wallets: list[WalletScore] = []
        self._last_rescore: float = 0
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def _get(self, url: str, params: dict = None, timeout: int = 15) -> Optional[list | dict]:
        """HTTP GET with error handling and backoff."""
        try:
            resp = self._session.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                logger.warning("Rate limited — backing off 60s")
                time.sleep(60)
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("API request failed (%s): %s", url, e)
            return None

    # --- Discovery & Scoring ---

    def fetch_recent_trades(self, limit: int = 500) -> list[dict]:
        """Fetch recent trades from Polymarket Gamma API."""
        data = self._get(f"{GAMMA_API}/trades", params={"limit": limit})
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return []

    def discover_wallets(self) -> dict[str, list[dict]]:
        """Group recent trades by wallet address."""
        trades = self.fetch_recent_trades(limit=500)
        wallets: dict[str, list[dict]] = {}
        for trade in trades:
            # Trades may have "maker" or "taker" fields
            for role in ("maker", "taker"):
                addr = trade.get(role, "")
                if addr:
                    wallets.setdefault(addr, []).append(trade)
        logger.info("Discovered %d wallets from %d trades", len(wallets), len(trades))
        return wallets

    def score_wallet(self, address: str, trades: list[dict]) -> WalletScore:
        """Compute win rate and profit factor for a wallet."""
        if not trades:
            return WalletScore(address=address)

        wins = 0
        losses = 0
        gross_profit = 0.0
        gross_loss = 0.0

        for trade in trades:
            # Determine PnL from trade data
            pnl = 0.0
            price = float(trade.get("price", 0.5))
            side = trade.get("side", "").upper()
            size = float(trade.get("size", 0))

            # For binary markets: BUY at low price is profitable if outcome is YES
            # Use price deviation from 0.5 as a proxy for realized PnL
            if side == "BUY":
                pnl = (0.5 - price) * size  # profit if bought below 0.5
            elif side == "SELL":
                pnl = (price - 0.5) * size  # profit if sold above 0.5

            if pnl > 0:
                wins += 1
                gross_profit += pnl
            else:
                losses += 1
                gross_loss += abs(pnl)

        total = wins + losses
        win_rate = wins / total if total > 0 else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        avg_pnl = (gross_profit - gross_loss) / total if total > 0 else 0.0

        # Composite score: weighted combination
        pf_normalized = min(profit_factor / 3.0, 1.0)  # cap at 3.0
        composite = win_rate * 0.6 + pf_normalized * 0.4

        last_trade = trades[0].get("timestamp", "") if trades else ""

        return WalletScore(
            address=address,
            win_rate=win_rate,
            profit_factor=profit_factor,
            trade_count=total,
            avg_pnl=avg_pnl,
            last_trade_at=last_trade,
            composite_score=round(composite, 3),
        )

    def get_top_wallets(self, force_refresh: bool = False) -> list[WalletScore]:
        """Get top N wallets, cached for rescore_interval seconds."""
        now = time.time()
        if not force_refresh and self._top_wallets and (now - self._last_rescore) < self.rescore_interval:
            return self._top_wallets

        logger.info("Rescoring wallets...")
        wallets = self.discover_wallets()

        scores = []
        for addr, trades in wallets.items():
            score = self.score_wallet(addr, trades)
            if score.trade_count >= self.min_trades and score.win_rate >= self.min_win_rate:
                scores.append(score)

        scores.sort(key=lambda s: s.composite_score, reverse=True)
        self._top_wallets = scores[:self.top_n]
        self._last_rescore = now

        logger.info(
            "Top wallets: %d/%d passed filters (min %d trades, %.0f%% win rate)",
            len(self._top_wallets), len(wallets), self.min_trades, self.min_win_rate * 100,
        )
        for w in self._top_wallets:
            logger.info(
                "  %s... score=%.3f wr=%.1f%% pf=%.2f trades=%d",
                w.address[:10], w.composite_score, w.win_rate * 100, w.profit_factor, w.trade_count,
            )

        return self._top_wallets

    # --- Signal Monitoring ---

    def get_wallet_latest_trades(self, wallet: str, limit: int = 10) -> list[dict]:
        """Fetch latest trades for a specific wallet."""
        data = self._get(f"{GAMMA_API}/trades", params={"maker": wallet, "limit": limit})
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "data" in data:
            return data["data"]
        return []

    def detect_new_positions(self, known_positions: dict) -> list[CopySignal]:
        """Detect new positions from top wallets that we haven't copied yet."""
        top_wallets = self.get_top_wallets()
        if not top_wallets:
            return []

        signals = []
        for wallet in top_wallets:
            trades = self.get_wallet_latest_trades(wallet.address, limit=5)
            for trade in trades:
                token_id = trade.get("asset_id", trade.get("token_id", ""))
                if not token_id:
                    continue

                # Build a unique key for this position
                side = trade.get("side", "").upper()
                position_key = f"{wallet.address}:{token_id}:{side}"

                if position_key in known_positions:
                    continue  # Already copied

                price = float(trade.get("price", 0))
                market_slug = trade.get("market", trade.get("market_slug", "unknown"))

                if side in ("BUY", "SELL") and price > 0:
                    signals.append(CopySignal(
                        wallet=wallet.address,
                        token_id=token_id,
                        market_slug=market_slug,
                        side=side,
                        price=price,
                        wallet_score=wallet.composite_score,
                        timestamp=trade.get("timestamp", ""),
                    ))

        if signals:
            logger.info("Detected %d new copy signals", len(signals))
        return signals
