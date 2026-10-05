"""Apply each user's saved strategy to a live Kalshi quote."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.services.trading.strategy import OrderRequest, Quote, StrategyBook

logger = logging.getLogger(__name__)


class StrategyRunner:
    def __init__(self, book: StrategyBook, gateway: Any, session_factory: Any = None) -> None:
        self.book = book
        self.gateway = gateway
        self.session_factory = session_factory
        self._busy: set[str] = set()
        self._persist_lock = asyncio.Lock()

    def on_quote(self, quote: Quote) -> None:
        """Schedule the decision off the market-data lock."""
        if quote.ticker in self._busy:
            return
        self._busy.add(quote.ticker)
        task = asyncio.create_task(self._run(quote))
        task.add_done_callback(lambda _task: self._busy.discard(quote.ticker))

    async def evaluate_now(self, quote: Quote) -> list[OrderRequest]:
        self.book.trading_connected = bool(getattr(self.gateway, "connected", False))
        sent: list[OrderRequest] = []
        for user_id in list(self.book.settings):
            request = self.book.evaluate(user_id, quote)
            if request is None:
                continue
            sent.append(request)
            receipt = await self.gateway.submit(request)
            self.book.apply_receipt(
                request.position_id,
                receipt,
                closing=request.action == "sell",
                now_ms=quote.now_ms,
            )
        return sent

    async def _run(self, quote: Quote) -> None:
        try:
            await self.evaluate_now(quote)
            await self.persist()
        except Exception:
            logger.exception("Strategy quote failed")

    async def persist(self) -> None:
        if self.session_factory is None:
            return
        from app.services.trading.store import save_book

        try:
            async with self._persist_lock:
                await save_book(self.session_factory, self.book)
        except Exception:
            logger.exception("Could not persist strategy state")
