from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from collections.abc import Callable
from typing import Any

import httpx
import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK

from app.order_book import OrderBook
from app.settings import (
    BINANCE_FAPI_MARKET_WS_URL,
    BINANCE_FAPI_PUBLIC_WS_URL,
    BINANCE_FAPI_REST_URL,
)
from app.trade_buffer import TradeBuffer


def apply_depth_event(book: OrderBook, payload: dict[str, Any]) -> None:
    book.apply_delta(payload.get("b", []), payload.get("a", []))


class BinanceSyncError(RuntimeError):
    pass


class BinanceMarketClient:
    def __init__(
        self,
        symbol: str,
        order_book: OrderBook,
        trade_buffer: TradeBuffer,
        *,
        on_trade: Callable[[dict[str, Any]], None] | None = None,
        on_book_update: Callable[[OrderBook, int | None], None] | None = None,
    ) -> None:
        self.symbol = symbol.upper()
        self.order_book = order_book
        self.trade_buffer = trade_buffer
        self.on_trade = on_trade
        self.on_book_update = on_book_update
        self.tick_size: float | None = None
        self.quantity_step: float | None = None
        self.min_quantity: float | None = None
        self._event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._stream_task: asyncio.Task | None = None
        self._synced = asyncio.Event()
        self._last_stream_update_id: int | None = None

    async def start(self) -> None:
        exchange_info = await self.fetch_exchange_info(self.symbol)
        self._apply_exchange_info(exchange_info)
        self._stream_task = asyncio.create_task(self._run(), name=f"binance:{self.symbol}")
        try:
            await asyncio.wait_for(self._synced.wait(), timeout=60)
        except TimeoutError as exc:
            await self.stop()
            raise BinanceSyncError("Timed out waiting for order book sync") from exc

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
        self._last_stream_update_id = None

    async def _run(self) -> None:
        depth_url = self._build_depth_stream_url(self.symbol)
        trade_url = self._build_trade_stream_url(self.symbol)
        async with (
            websockets.connect(depth_url, ping_interval=20, ping_timeout=20) as depth_ws,
            websockets.connect(trade_url, ping_interval=20, ping_timeout=20) as trade_ws,
        ):
            reader_tasks = [
                asyncio.create_task(self._reader_loop(depth_ws)),
                asyncio.create_task(self._reader_loop(trade_ws)),
            ]
            try:
                synced = False
                for _ in range(5):
                    snapshot = await self.fetch_snapshot(self.symbol)
                    await asyncio.sleep(0.25)
                    buffered_events = self._drain_queue()
                    self.order_book.load_snapshot(
                        bids=snapshot["bids"],
                        asks=snapshot["asks"],
                    )
                    synced = self._sync_from_buffer(snapshot["lastUpdateId"], buffered_events)
                    if synced:
                        break

                if not synced:
                    raise BinanceSyncError("Unable to align snapshot with depth stream")

                self._synced.set()
                while True:
                    event = await self._event_queue.get()
                    self._process_stream_event(event)
            finally:
                for task in reader_tasks:
                    task.cancel()
                for task in reader_tasks:
                    with suppress(asyncio.CancelledError):
                        await task

    async def _reader_loop(self, ws) -> None:
        async for message in ws:
            payload = json.loads(message)
            await self._event_queue.put(payload.get("data", payload))

    async def fetch_snapshot(self, symbol: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=BINANCE_FAPI_REST_URL, timeout=10.0) as client:
            response = await client.get(
                "/fapi/v1/depth",
                params={"symbol": symbol.upper(), "limit": 1000},
            )
            response.raise_for_status()
            return response.json()

    async def fetch_exchange_info(self, symbol: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=BINANCE_FAPI_REST_URL, timeout=10.0) as client:
            response = await client.get(
                "/fapi/v1/exchangeInfo",
                params={"symbol": symbol.upper()},
            )
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _build_depth_stream_url(symbol: str) -> str:
        stream_name = symbol.lower()
        return f"{BINANCE_FAPI_PUBLIC_WS_URL}?streams={stream_name}@depth@100ms"

    @staticmethod
    def _build_trade_stream_url(symbol: str) -> str:
        stream_name = symbol.lower()
        return f"{BINANCE_FAPI_MARKET_WS_URL}?streams={stream_name}@aggTrade"

    def _extract_tick_size(self, exchange_info: dict[str, Any]) -> float:
        symbol_info = self._symbol_info(exchange_info)
        for item in symbol_info.get("filters", []):
            if item.get("filterType") == "PRICE_FILTER":
                return float(item["tickSize"])
        raise RuntimeError(f"Missing PRICE_FILTER tick size for {self.symbol}")

    def _apply_exchange_info(self, exchange_info: dict[str, Any]) -> None:
        symbol_info = self._symbol_info(exchange_info)
        self.tick_size = self._extract_tick_size(exchange_info)
        self.quantity_step = None
        self.min_quantity = None
        for item in symbol_info.get("filters", []):
            if item.get("filterType") == "LOT_SIZE":
                self.quantity_step = float(item["stepSize"])
                self.min_quantity = float(item["minQty"])
                return
        raise RuntimeError(f"Missing LOT_SIZE quantity filters for {self.symbol}")

    def _symbol_info(self, exchange_info: dict[str, Any]) -> dict[str, Any]:
        for symbol_info in exchange_info.get("symbols", []):
            if symbol_info.get("symbol") != self.symbol:
                continue
            return symbol_info
        raise RuntimeError(f"Missing exchange metadata for {self.symbol}")

    def _drain_queue(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        while True:
            try:
                items.append(self._event_queue.get_nowait())
            except asyncio.QueueEmpty:
                return items

    def _sync_from_buffer(
        self, last_update_id: int, buffered_events: list[dict[str, Any]]
    ) -> bool:
        depth_events = [event for event in buffered_events if event.get("e") == "depthUpdate"]
        trade_events = [event for event in buffered_events if event.get("e") in {"trade", "aggTrade"}]

        for trade_event in trade_events:
            self._process_trade_event(trade_event)

        synced = False
        for event in depth_events:
            if event["u"] < last_update_id:
                continue
            if not synced:
                if event["U"] <= last_update_id <= event["u"]:
                    synced = True
                    self._process_depth_event(event)
                continue
            self._process_depth_event(event)

        return synced

    def _process_stream_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("e")
        if event_type == "depthUpdate":
            self._process_depth_event(event)
        elif event_type in {"trade", "aggTrade"}:
            self._process_trade_event(event)

    def _process_depth_event(self, event: dict[str, Any]) -> None:
        previous_update_id = self._last_stream_update_id
        if previous_update_id is not None and event.get("pu") != previous_update_id:
            raise BinanceSyncError("Depth stream sequence mismatch")
        apply_depth_event(self.order_book, event)
        self._last_stream_update_id = int(event["u"])
        if self.on_book_update is not None:
            self.on_book_update(self.order_book, event.get("E"))

    def _process_trade_event(self, event: dict[str, Any]) -> None:
        trade = {
            "symbol": self.symbol,
            "price": float(event["p"]),
            "qty": float(event["q"]),
            "timestamp": int(event["T"]),
            "is_buyer_maker": bool(event.get("m", False)),
        }
        self.trade_buffer.add(trade)
        if self.on_trade is not None:
            self.on_trade(trade)
