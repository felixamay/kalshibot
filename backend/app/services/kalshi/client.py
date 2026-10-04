"""Kalshi REST API client — READ-ONLY. Never places, modifies, or cancels orders."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from pathlib import Path
from typing import Any, Optional

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


class KalshiReadOnlyClient:
    """
    Production Kalshi Trade API client with STRICT read-only enforcement.

    ABSOLUTE SAFETY: This client has NO methods to create, amend, or cancel orders.
    There is no place_order, buy, sell, or portfolio mutation API.
    """

    FORBIDDEN_PATH_PREFIXES = (
        "/portfolio/orders",
        "/portfolio/order_groups",
        "/exchange/orders",
    )
    FORBIDDEN_METHODS_ON_PORTFOLIO = {"POST", "PUT", "PATCH", "DELETE"}

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.base_url = self.settings.kalshi_api_base_url.rstrip("/")
        self._private_key: Optional[rsa.RSAPrivateKey | ed25519.Ed25519PrivateKey] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._load_private_key()

    def _load_private_key(self) -> None:
        pem = self.settings.kalshi_private_key_pem.strip()
        path = self.settings.kalshi_private_key_path.strip()
        raw: Optional[bytes] = None
        if pem:
            raw = pem.encode("utf-8") if isinstance(pem, str) else pem
        elif path and Path(path).exists():
            raw = Path(path).read_bytes()
        if not raw:
            logger.warning("Kalshi private key not configured — public endpoints only")
            return
        try:
            self._private_key = serialization.load_pem_private_key(raw, password=None)
            logger.info("Kalshi private key loaded for authenticated READ requests")
        except Exception as exc:
            logger.error("Failed to load Kalshi private key: %s", exc)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    def _sign(self, timestamp_ms: str, method: str, path: str) -> str:
        if not self._private_key:
            raise RuntimeError("Private key required for authenticated requests")
        # Kalshi signing: timestamp + method + path (without query string)
        path_no_query = path.split("?")[0]
        message = f"{timestamp_ms}{method.upper()}{path_no_query}".encode("utf-8")
        # Kalshi: RSA-PSS SHA-256, or raw Ed25519 (docs.kalshi.com API keys)
        if isinstance(self._private_key, ed25519.Ed25519PrivateKey):
            signature = self._private_key.sign(message)
        else:
            signature = self._private_key.sign(
                message,
                padding.PSS(
                    mgf=padding.MGF1(hashes.SHA256()),
                    salt_length=padding.PSS.DIGEST_LENGTH,
                ),
                hashes.SHA256(),
            )
        return base64.b64encode(signature).decode("utf-8")

    def _auth_headers(self, method: str, full_path: str) -> dict[str, str]:
        if not self.settings.kalshi_api_key_id or not self._private_key:
            return {}
        ts = str(int(time.time() * 1000))
        # Path for signing includes /trade-api/v2 prefix as Kalshi expects
        sign_path = full_path
        if not sign_path.startswith("/trade-api"):
            # Extract path from URL path portion
            from urllib.parse import urlparse

            parsed = urlparse(full_path if full_path.startswith("http") else f"https://x{full_path}")
            sign_path = parsed.path
            if "/trade-api" not in sign_path:
                # base_url already contains /trade-api/v2; request paths are relative
                relative = full_path if full_path.startswith("/") else f"/{full_path}"
                # Reconstruct what Kalshi docs specify
                base_path = "/trade-api/v2"
                sign_path = base_path + relative
        sig = self._sign(ts, method, sign_path)
        return {
            "KALSHI-ACCESS-KEY": self.settings.kalshi_api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "Content-Type": "application/json",
        }

    def _assert_read_only(self, method: str, path: str) -> None:
        method_u = method.upper()
        path_l = path.lower()
        if method_u != "GET":
            # Only allow GET — absolute ban on mutations
            raise PermissionError(
                f"BLOCKED: KalshiWriteAttempt method={method_u} path={path}. "
                "This application NEVER places, modifies, or cancels orders."
            )
        for forbidden in self.FORBIDDEN_PATH_PREFIXES:
            if forbidden in path_l and method_u in self.FORBIDDEN_METHODS_ON_PORTFOLIO:
                raise PermissionError(f"BLOCKED forbidden path: {path}")

    async def request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        self._assert_read_only(method, path)
        client = await self._get_client()
        url = f"{self.base_url}{path if path.startswith('/') else '/' + path}"
        headers: dict[str, str] = {"Accept": "application/json"}
        if authenticated or self.settings.kalshi_use_auth:
            try:
                # Sign relative path as used by Kalshi
                rel = path if path.startswith("/") else f"/{path}"
                sign_path = rel
                # Kalshi expects full API path in signature
                if not sign_path.startswith("/trade-api"):
                    # Derive from base_url
                    from urllib.parse import urlparse

                    base_path = urlparse(self.base_url).path.rstrip("/")
                    sign_path = f"{base_path}{rel}"
                if self._private_key and self.settings.kalshi_api_key_id:
                    ts = str(int(time.time() * 1000))
                    sig = self._sign(ts, method, sign_path)
                    headers.update(
                        {
                            "KALSHI-ACCESS-KEY": self.settings.kalshi_api_key_id,
                            "KALSHI-ACCESS-TIMESTAMP": ts,
                            "KALSHI-ACCESS-SIGNATURE": sig,
                        }
                    )
            except Exception as exc:
                logger.debug("Auth header skipped: %s", exc)

        response = await client.request(method, url, params=params, headers=headers)
        response.raise_for_status()
        return response.json()

    async def get_exchange_status(self) -> dict[str, Any]:
        return await self.request("GET", "/exchange/status", authenticated=False)

    async def get_markets(
        self,
        *,
        status: str = "open",
        series_ticker: Optional[str] = None,
        event_ticker: Optional[str] = None,
        limit: int = 200,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "status": status, "mve_filter": "exclude"}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if cursor:
            params["cursor"] = cursor
        return await self.request("GET", "/markets", params=params, authenticated=False)

    async def get_market(self, ticker: str) -> dict[str, Any]:
        return await self.request("GET", f"/markets/{ticker}", authenticated=False)

    async def get_orderbook(self, ticker: str, depth: int = 10) -> dict[str, Any]:
        return await self.request(
            "GET",
            f"/markets/{ticker}/orderbook",
            params={"depth": depth},
            authenticated=False,
        )

    async def get_trades(self, ticker: str, limit: int = 100) -> dict[str, Any]:
        return await self.request(
            "GET",
            "/markets/trades",
            params={"ticker": ticker, "limit": limit},
            authenticated=False,
        )

    async def get_events(
        self,
        *,
        series_ticker: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
        with_nested_markets: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": limit, "with_nested_markets": with_nested_markets}
        if series_ticker:
            params["series_ticker"] = series_ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor
        return await self.request("GET", "/events", params=params, authenticated=False)

    async def get_series_list(self) -> dict[str, Any]:
        return await self.request("GET", "/series", authenticated=False)

    async def search_tennis_markets(self) -> list[dict[str, Any]]:
        """Exhaustive paginated reconciliation; no series/page/result caps."""
        from app.services.kalshi.tennis_detector import TennisMarketDetector
        detector = TennisMarketDetector()
        health = {"events_checked": 0, "markets_checked": 0, "tennis_markets_found": 0,
                  "last_refresh_ms": time.time() * 1000, "complete": False, "errors": []}
        self.discovery_health = health
        series = {}
        try:
            data = await self.get_series_list()
            series = {s.get("ticker"): s for s in data.get("series", [])}
        except Exception as exc:
            health["errors"].append(f"Series: {type(exc).__name__}")
        events = {}
        cursor = None
        while True:
            data = await self.get_events(status="open", limit=200, cursor=cursor)
            for event in data.get("events", []):
                events[event.get("event_ticker")] = event
            cursor = data.get("cursor")
            if not cursor:
                break
            await asyncio.sleep(0.15)
        health["events_checked"] = len(events)
        found = {}
        cursor = None
        while True:
            data = await self.get_markets(status="open", limit=1000, cursor=cursor)
            for market in data.get("markets", []):
                health["markets_checked"] += 1
                event = events.get(market.get("event_ticker"), {})
                info = series.get(event.get("series_ticker") or market.get("series_ticker"), {})
                if detector.detects(market, event, info):
                    found[market["ticker"]] = {**market, "_event": event, "_series": info}
            cursor = data.get("cursor")
            if not cursor:
                break
            await asyncio.sleep(0.15)
        for event in events.values():
            for market in event.get("markets", []):
                if detector.detects(market, event, series.get(event.get("series_ticker"), {})):
                    found[market["ticker"]] = {**market, "_event": event}
        health.update(tennis_markets_found=len(found), complete=True, last_refresh_ms=time.time() * 1000)
        logger.info("KALSHI EVENTS RECEIVED: %s", health["events_checked"])
        logger.info("KALSHI MARKETS RECEIVED: %s", health["markets_checked"])
        logger.info("TENNIS MARKETS CLASSIFIED: %s", len(found))
        return list(found.values())

    async def list_tennis_milestones(self) -> dict[str, dict[str, str]]:
        """Event ticker to the Kalshi match milestone. Status live means the match is in progress."""
        minimum = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 36 * 3600))
        cursor = None
        index: dict[str, dict[str, str]] = {}
        for _ in range(15):
            params: dict[str, Any] = {"limit": 200, "category": "Sports", "minimum_start_date": minimum}
            if cursor:
                params["cursor"] = cursor
            data = await self.request("GET", "/milestones", params=params, authenticated=False)
            for row in data.get("milestones") or []:
                if "tennis" not in str(row.get("type") or ""):
                    continue
                details = row.get("details") or {}
                status = str(details.get("status") or "")
                start = str(row.get("start_date") or "")
                tickers = [details.get("main_game_event_ticker")]
                tickers.extend(row.get("primary_event_tickers") or [])
                tickers.extend(row.get("related_event_tickers") or [])
                for ticker in tickers:
                    if not ticker:
                        continue
                    current = index.get(ticker)
                    if current is None or _milestone_rank(status) > _milestone_rank(current["status"]) or (
                        _milestone_rank(status) == _milestone_rank(current["status"]) and start > current["start"]
                    ):
                        index[ticker] = {"status": status, "start": start}
            cursor = data.get("cursor")
            if not cursor:
                break
            await asyncio.sleep(0.1)
        return index


def _milestone_rank(status: str) -> int:
    if str(status).lower() in {"live", "inprogress", "in_progress"}:
        return 2
    return 1


# Explicit guard: ensure module never defines write helpers
assert not hasattr(KalshiReadOnlyClient, "place_order")
assert not hasattr(KalshiReadOnlyClient, "create_order")
assert not hasattr(KalshiReadOnlyClient, "cancel_order")
