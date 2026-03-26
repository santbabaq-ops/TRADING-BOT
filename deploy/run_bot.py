"""Launch a single trading bot."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from config.accounts import ACCOUNTS
from data.polymarket_client import PolymarketClient
from data.downloader import OHLCVDownloader
from bot.trader import Trader
from strategies.macd_strategy import MACDStrategy
from strategies.rsi_mean_reversion import RSIMeanReversionStrategy
from strategies.cvd_strategy import CVDStrategy
from incubation.logger import setup_logging

STRATEGIES = {
    "macd": MACDStrategy,
    "rsi": RSIMeanReversionStrategy,
    "cvd": CVDStrategy,
}


def main():
    parser = argparse.ArgumentParser(description="Polymarket Trading Bot")
    parser.add_argument("--strategy", choices=STRATEGIES.keys(), required=True)
    parser.add_argument("--account", default="account_1", help="Account name from config")
    parser.add_argument("--size", type=float, default=1.0, help="Position size in USD")
    parser.add_argument("--token-id", required=True, help="Polymarket token ID to trade")
    args = parser.parse_args()

    logger = setup_logging(args.strategy)
    logger.info("Launching bot: %s on account %s", args.strategy, args.account)

    # Setup account
    account = ACCOUNTS.get(args.account, ACCOUNTS["account_1"])
    client = PolymarketClient(
        private_key=account["private_key"],
        funder_address=account["funder_address"],
    )

    if not settings.runtime.dry_run:
        client.connect()
        logger.info("Connected to Polymarket (LIVE mode)")
    else:
        logger.info("Running in DRY RUN mode — no real orders")

    # Load strategy
    strategy = STRATEGIES[args.strategy]()

    # Setup data fetcher for live mode
    downloader = OHLCVDownloader()

    def fetch_data():
        return downloader.fetch(symbol="BTC/USDT", timeframe="5m", days_back=1)

    # Run trader with live data fetcher
    trader = Trader(
        strategy=strategy,
        client=client,
        token_id=args.token_id,
        position_size=args.size,
        account_name=args.account,
    )
    trader.run_loop(data_fetcher=fetch_data, interval_seconds=300)


if __name__ == "__main__":
    main()
