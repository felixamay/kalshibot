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
        params: dict[str, Any] = {"limit": limit, "status": status}
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
        """Discover open tennis-related markets via series keywords and title heuristics."""
        keywords = [k.strip().lower() for k in self.settings.tennis_series_keywords.split(",") if k.strip()]
        found: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Match-winner contracts, including challenger, ITF, and doubles.
        # Tour scoreboards hide the matches that are actually on court.
        preferred_series = (
            "KXWTAMATCH",
            "KXATPMATCH",
            "KXWTACHALLENGERMATCH",
            "KXATPCHALLENGERMATCH",
            "KXITFMATCH",
            "KXITFWMATCH",
            "KXITFDOUBLES",
            "KXITFWDOUBLES",
            "KXATPDOUBLES",
            "KXWTADOUBLES",
            "KXATPCHALLENGERDOUBLES",
            "KXMIXEDDOUBLESMATCH",
            "KXTENNISMATCH",
        )

        try:
            series_resp = await self.get_series_list()
            series_list = series_resp.get("series", series_resp.get("series_list", []))
            tennis_series: list[str] = []
            for s in series_list:
                ticker = (s.get("ticker") or s.get("series_ticker") or "").upper()
                title = (s.get("title") or s.get("frequency") or "").lower()
                cat = (s.get("category") or "").lower()
                blob = f"{ticker} {title} {cat}"
                if any(k in blob.lower() for k in keywords) or "tennis" in blob:
                    tennis_series.append(s.get("ticker") or s.get("series_ticker"))

            # Preferred first, then others (cap to avoid rate limits)
            ordered: list[str] = []
            for p in preferred_series:
                if p not in ordered:
                    ordered.append(p)
            for st in tennis_series:
                if st and st not in ordered:
                    ordered.append(st)

            for st in ordered[:24]:
                if not st:
                    continue
                try:
                    await asyncio.sleep(0.15)
                    data = await self.get_markets(status="open", series_ticker=st, limit=200)
                    for m in data.get("markets", []):
                        t = m.get("ticker")
                        if t and t not in seen:
                            seen.add(t)
                            found.append(m)
                except Exception as exc:
                    logger.debug("series %s markets failed: %s", st, exc)
        except Exception as exc:
            logger.warning("Series list fetch failed: %s — falling back to market scan", exc)

        # Broader open market scan with tennis heuristics if still thin
        if len(found) < 5:
            cursor = None
            pages = 0
            while pages < 4:
                try:
                    await asyncio.sleep(0.2)
                    data = await self.get_markets(status="open", limit=200, cursor=cursor)
                except Exception as exc:
                    logger.error("Market scan failed: %s", exc)
                    break
                markets = data.get("markets", [])
                if not markets:
                    break
                for m in markets:
                    blob = " ".join(
                        str(m.get(k) or "")
                        for k in ("ticker", "title", "subtitle", "yes_sub_title", "no_sub_title", "event_ticker")
                    ).lower()
                    if any(
                        k in blob
                        for k in (
                            "tennis",
                            "atp",
                            "wta",
                            "open match",
                            "set winner",
                            "match winner",
                        )
                    ) or any(k in blob for k in keywords):
                        t = m.get("ticker")
                        if t and t not in seen:
                            seen.add(t)
                            found.append(m)
                cursor = data.get("cursor")
                pages += 1
                if not cursor:
                    break

        # Match-winner contracts only. Futures and round markets are not a live game.
        match_prefixes = (
            "KXWTAMATCH",
            "KXATPMATCH",
            "KXWTACHALLENGERMATCH",
            "KXATPCHALLENGERMATCH",
            "KXITFMATCH",
            "KXITFWMATCH",
            "KXITFDOUBLES",
            "KXITFWDOUBLES",
            "KXATPDOUBLES",
            "KXWTADOUBLES",
            "KXATPCHALLENGERDOUBLES",
            "KXMIXEDDOUBLESMATCH",
        )
        match_only = [
            m
            for m in found
            if (m.get("ticker") or "").upper().startswith(match_prefixes)
        ]
        if match_only:
            found = match_only

        logger.info("Discovered %d tennis-related Kalshi markets", len(found))
        return found[:400]


# Explicit guard: ensure module never defines write helpers
assert not hasattr(KalshiReadOnlyClient, "place_order")
assert not hasattr(KalshiReadOnlyClient, "create_order")
assert not hasattr(KalshiReadOnlyClient, "cancel_order")
