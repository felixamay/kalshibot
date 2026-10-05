"""Authenticated Kalshi order calls. Market-data reads stay on the read-only client."""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import uuid4

from app.services.kalshi.client import KalshiReadOnlyClient
from app.services.trading.strategy import OrderReceipt, OrderRequest

logger = logging.getLogger(__name__)


class KalshiTradingClient:
    """Places and reads orders with the same signing the read client already uses."""

    def __init__(self, reader: KalshiReadOnlyClient) -> None:
        self.reader = reader
        self.connected = False

    def configured(self) -> bool:
        return bool(self.reader.settings.kalshi_api_key_id and self.reader._private_key)

    async def refresh_connection(self) -> bool:
        if not self.configured():
            self.connected = False
            return False
        try:
            await self._send("GET", "/portfolio/balance")
            self.connected = True
        except Exception:
            self.connected = False
            logger.warning("Kalshi trading API is disconnected")
        return self.connected

    async def submit(self, order: OrderRequest) -> OrderReceipt:
        if not self.connected:
            return OrderReceipt(ok=False, error="Trading API is disconnected.")
        side = order.side.lower()
        body: dict[str, Any] = {
            "ticker": order.ticker,
            "client_order_id": str(uuid4()),
            "action": order.action,
            "side": side,
            "count": int(order.quantity),
            "type": "limit",
        }
        price = int(round(order.price))
        if side == "yes":
            body["yes_price"] = price
        else:
            body["no_price"] = price
        try:
            payload = await self._send("POST", "/portfolio/orders", body)
        except Exception:
            logger.warning("Kalshi order was not accepted")
            return OrderReceipt(ok=False, error="Order was not accepted")
        return _receipt(payload, requested_price=order.price)

    async def fetch(self, order_id: str) -> OrderReceipt:
        if not self.connected or not order_id:
            return OrderReceipt(ok=False, error="Trading API is disconnected.")
        try:
            payload = await self._send("GET", f"/portfolio/orders/{order_id}")
        except Exception:
            return OrderReceipt(ok=False, error="Order status is unavailable")
        return _receipt(payload)

    async def _send(self, method: str, path: str, body: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        from urllib.parse import urlparse

        client = await self.reader._get_client()
        relative = path if path.startswith("/") else f"/{path}"
        base_path = urlparse(self.reader.base_url).path.rstrip("/")
        sign_path = f"{base_path}{relative}"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        headers.update(self.reader._auth_headers(method, sign_path))
        response = await client.request(method, f"{self.reader.base_url}{relative}", json=body, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}


def _receipt(payload: dict[str, Any], requested_price: float = 0) -> OrderReceipt:
    order = payload.get("order") if isinstance(payload.get("order"), dict) else payload
    order_id = str(order.get("order_id") or order.get("id") or "")
    filled = _num(order.get("fill_count_fp", order.get("fill_count", 0)))
    remaining = _num(order.get("remaining_count_fp", order.get("remaining_count", 0)))
    fees = _num(order.get("taker_fees_dollars", order.get("fees", 0)))
    cost = _num(order.get("taker_fill_cost_dollars", order.get("maker_fill_cost_dollars", 0)))
    average = (cost / filled * 100.0) if filled > 0 and cost > 0 else None
    if average is None and filled > 0:
        raw = order.get("yes_price") if order.get("side") == "yes" else order.get("no_price")
        if raw is not None:
            average = _num(raw)
    return OrderReceipt(
        ok=True,
        order_id=order_id,
        filled_qty=filled,
        average_price=average,
        remaining_qty=remaining,
        fees=fees,
        error="",
    )


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
