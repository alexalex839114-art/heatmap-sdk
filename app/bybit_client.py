from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import suppress
from typing import Any

import websockets
import httpx
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK

from app.order_book import OrderBook
from app.settings import BYBIT_LINEAR_PUBLIC_WS_URL, BYBIT_REST_URL
from app.trade_buffer import TradeBuffer


class BybitMarketClient:
    def __init__(
        self,
        symbol: str,
        order_book: OrderBook,
        trade_buffer: TradeBuffer,
        *,
        on_trade: Callable[[dict[str, Any]], None] | None = None,
        on_book_update: Callable[[OrderBook, int | None], None] | None = None,
        depth: int = 50,
        rest_url: str = BYBIT_REST_URL,
        rest_transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.order_book = order_book
        self.trade_buffer = trade_buffer
        self.on_trade = on_trade
        self.on_book_update = on_book_update
        self.depth = int(depth)
        self.rest_url = rest_url
        self._rest_transport = rest_transport
        self.tick_size: float | None = None
        self._stream_task: asyncio.Task | None = None
        self._synced = asyncio.Event()

    async def start(self, sync_timeout: float = 30.0) -> None:
        self._stream_task = asyncio.create_task(self._run(), name=f"bybit:{self.symbol}")
        sync_task = asyncio.create_task(self._synced.wait())
        try:
            done, pending = await asyncio.wait(
                {self._stream_task, sync_task},
                timeout=sync_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if self._stream_task in done:
                try:
                    await self._stream_task
                finally:
                    self._stream_task = None
                    self._synced.clear()
                raise RuntimeError("Bybit stream stopped before order book sync")
            if sync_task in done:
                await self._seed_recent_trades()
                return

            for task in pending:
                task.cancel()
            await self.stop()
            raise TimeoutError("Timed out waiting for Bybit order book sync")
        finally:
            sync_task.cancel()
            with suppress(asyncio.CancelledError):
                await sync_task

    async def stop(self) -> None:
        if self._stream_task is None:
            return
        self._stream_task.cancel()
        with suppress(
            asyncio.CancelledError,
            TimeoutError,
            ConnectionClosed,
            ConnectionClosedError,
            ConnectionClosedOK,
        ):
            await self._stream_task
        self._stream_task = None
        self._synced.clear()

    async def _run(self) -> None:
        async with websockets.connect(
            BYBIT_LINEAR_PUBLIC_WS_URL,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            await ws.send(json.dumps(self._build_subscribe_message(self.symbol, self.depth)))
            async for message in ws:
                self._process_stream_event(json.loads(message))

    @staticmethod
    def _build_subscribe_message(symbol: str, depth: int = 50) -> dict[str, Any]:
        normalized_symbol = symbol.upper()
        return {
            "op": "subscribe",
            "args": [
                f"orderbook.{int(depth)}.{normalized_symbol}",
                f"publicTrade.{normalized_symbol}",
            ],
        }

    def _process_stream_event(self, event: dict[str, Any]) -> None:
        topic = str(event.get("topic", ""))
        if topic.startswith("orderbook."):
            self._process_orderbook_event(event)
        elif topic.startswith("publicTrade."):
            self._process_public_trade_event(event)

    def _process_orderbook_event(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        bids = data.get("b", [])
        asks = data.get("a", [])
        if event.get("type") == "snapshot":
            self.order_book.load_snapshot(bids, asks)
            self._synced.set()
        else:
            self.order_book.apply_delta(bids, asks)
        if self.on_book_update is not None:
            self.on_book_update(self.order_book, event.get("ts") or data.get("cts"))

    def _process_public_trade_event(self, event: dict[str, Any]) -> None:
        for item in event.get("data") or []:
            self._emit_trade(
                symbol=str(item.get("s", self.symbol)),
                price=item["p"],
                qty=item["v"],
                timestamp=item["T"],
                side=item.get("S", ""),
            )

    async def _seed_recent_trades(self) -> None:
        try:
            async with httpx.AsyncClient(
                base_url=self.rest_url,
                timeout=12.0,
                transport=self._rest_transport,
            ) as client:
                response = await client.get(
                    "/v5/market/recent-trade",
                    params={
                        "category": "linear",
                        "symbol": self.symbol,
                        "limit": 200,
                    },
                )
                response.raise_for_status()
                self._process_recent_trade_payload(response.json())
        except Exception:
            return

    def _process_recent_trade_payload(self, payload: dict[str, Any]) -> None:
        rows = payload.get("result", {}).get("list", [])
        if not isinstance(rows, list):
            return
        for item in reversed(rows):
            self._emit_trade(
                symbol=str(item.get("symbol", self.symbol)),
                price=item["price"],
                qty=item["size"],
                timestamp=item["time"],
                side=item.get("side", ""),
            )

    def _emit_trade(
        self,
        *,
        symbol: str,
        price: Any,
        qty: Any,
        timestamp: Any,
        side: Any,
    ) -> None:
        trade = {
            "symbol": symbol.upper(),
            "price": float(price),
            "qty": float(qty),
            "timestamp": int(timestamp),
            "is_buyer_maker": str(side) == "Sell",
        }
        self.trade_buffer.add(trade)
        if self.on_trade is not None:
            self.on_trade(trade)
