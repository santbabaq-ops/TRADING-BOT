"""Download OHLCV data from exchanges via ccxt."""

import logging
from datetime import datetime, timedelta

import ccxt
import pandas as pd

logger = logging.getLogger(__name__)


class OHLCVDownloader:
    """Download historical candle data for backtesting."""

    def __init__(self, exchange_id: str = "binance"):
        self.exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})

    def fetch(
        self,
        symbol: str,
        timeframe: str = "5m",
        days_back: int = 30,
        limit: int = 1000,
    ) -> pd.DataFrame:
        """Fetch OHLCV candles and return as DataFrame.

        Args:
            symbol: Trading pair, e.g. "BTC/USDT"
            timeframe: Candle interval, e.g. "1m", "5m", "1h"
            days_back: How many days of history to fetch
            limit: Max candles per API call
        """
        since = int((datetime.now(tz=None) - timedelta(days=days_back)).timestamp() * 1000)
        all_candles = []

        while True:
            candles = self.exchange.fetch_ohlcv(
                symbol, timeframe=timeframe, since=since, limit=limit
            )
            if not candles:
                break
            all_candles.extend(candles)
            since = candles[-1][0] + 1  # next ms after last candle
            if len(candles) < limit:
                break

        if not all_candles:
            logger.warning("No candles returned for %s", symbol)
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
        logger.info("Fetched %d candles for %s (%s)", len(df), symbol, timeframe)
        return df
