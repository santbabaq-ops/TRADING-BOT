"""Polymarket CLOB API wrapper."""

import logging
from typing import Optional

from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from config import settings

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Thin wrapper around py-clob-client with safety checks."""

    def __init__(self, private_key: str = "", funder_address: str = "", dry_run: bool | None = None):
        self.private_key = private_key or settings.PRIVATE_KEY
        self.funder_address = funder_address or settings.FUNDER_ADDRESS
        self.dry_run = dry_run if dry_run is not None else settings.runtime.dry_run
        self._client: Optional[ClobClient] = None

    def connect(self) -> None:
        """Initialize and authenticate the CLOB client."""
        if not self.private_key:
            raise ValueError("POLYMARKET_PRIVATE_KEY not set — check your .env")

        self._client = ClobClient(
            host=settings.POLYMARKET_HOST,
            key=self.private_key,
            chain_id=settings.CHAIN_ID,
            funder=self.funder_address,
            signature_type=settings.SIGNATURE_TYPE,
        )
        creds = self._client.create_or_derive_api_creds()
        self._client.set_api_creds(creds)
        logger.info("Connected to Polymarket CLOB")

    @property
    def client(self) -> ClobClient:
        if self._client is None:
            raise RuntimeError("Client not connected — call connect() first")
        return self._client

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_limit_order(
        self, token_id: str, side: str, price: float, size: float
    ) -> dict:
        """Place a limit order. Returns API response."""
        if self.dry_run:
            logger.info("[DRY RUN] %s %.2f @ %.4f on %s", side, size, price, token_id[:16])
            return {"status": "dry_run"}

        order_side = BUY if side.upper() == "BUY" else SELL
        order_args = OrderArgs(
            price=price,
            size=size,
            side=order_side,
            token_id=token_id,
        )
        signed = self.client.create_order(order_args)
        response = self.client.post_order(signed, OrderType.GTC)
        logger.info("Order placed: %s %.2f @ %.4f -> %s", side, size, price, response)
        return response

    def cancel_all_orders(self, token_id: str) -> list[dict]:
        """Cancel all open orders for a given token."""
        if self.dry_run:
            logger.info("[DRY RUN] Cancel all orders for %s", token_id[:16])
            return []

        open_orders = self.client.get_orders(asset_id=token_id)
        results = []
        for order in open_orders:
            resp = self.client.cancel(order_id=order["id"])
            results.append(resp)
            logger.info("Cancelled order %s", order["id"])
        return results

    def get_orderbook(self, token_id: str) -> dict:
        """Get current order book for a token."""
        return self.client.get_order_book(token_id)

    def get_price(self, token_id: str) -> Optional[float]:
        """Get best bid/ask midpoint price."""
        book = self.get_orderbook(token_id)
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return None
        best_bid = float(bids[0]["price"])
        best_ask = float(asks[0]["price"])
        return (best_bid + best_ask) / 2
