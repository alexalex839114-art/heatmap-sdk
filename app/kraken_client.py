"""Kraken public WebSocket v2 market client.

Subscribes to ``book`` (depth=25 snapshot+updates) and ``trade`` for a single
pair. Kraken uses slash-separated pairs like ``BTC/USD`` (``XBT/USD`` for
Bitcoin).

Convention notes:

* ``book`` snapshot and updates carry ``bids`` and ``asks`` as arrays of
  ``{price, qty}``. A ``qty == 0`` update removes the level (matches Binance).
* ``trade.side`` reports the **taker** side, so ``side == "sell"`` means the
  seller was the aggressor (equivalent to Binance ``is_buyer_maker=True``).
  ``side == "buy"`` means the buyer was the aggressor.
* The feed streams a snapshot as a separate message type ``type == "snapshot"``
  before update messages with ``type == "update"``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

import websockets
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    ConnectionClosedOK,
)

from app.order_book import OrderBook
from app.settings import KRAKEN_PUBLIC_WS_V2_URL
from app.trade_buffer import TradeBuffer


class KrakenMarketClient:
    def __init__(
        self,
        pair: str,
        order_book: OrderBook,
        trade_buffer: TradeBuffer,
        *,
        on_trade: Callable[[dict[str, Any]], None] | None = None,
        on_book_update: Callable[[OrderBook, int | None], None] | None = None,
        depth: int = 25,
        ws_url: str = KRAKEN_PUBLIC_WS_V2_URL,
    ) -> None:
        self.pair = pair.upper()
        self.order_book = order_book
        self.trade_buffer = trade_buffer
        self.on_trade = on_trade
        self.on_book_update = on_book_update
        self.depth = int(depth)
        self.ws_url = ws_url
        self._stream_task: asyncio.Task | None = None
        self._synced = asyncio.Event()

    async def start(self, sync_timeout: float = 30.0) -> None:
        self._stream_task = asyncio.create_task(
            self._run(),
            name=f"kraken:{self.pair}",
        )
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
                raise RuntimeError("Kraken stream stopped before order book sync")
            if sync_task in done:
                return
            for task in pending:
                task.cancel()
            await self.stop()
            raise TimeoutError("Timed out waiting for Kraken order book sync")
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
            self.ws_url,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=30,
            max_size=16 * 1024 * 1024,
        ) as ws:
            for message in self._build_subscribe_messages(self.pair, self.depth):
                await ws.send(json.dumps(message))
            async for raw in ws:
                self._process_stream_event(json.loads(raw))

    @staticmethod
    def _build_subscribe_messages(pair: str, depth: int) -> list[dict[str, Any]]:
        normalized = pair.upper()
        return [
            {
                "method": "subscribe",
                "params": {
                    "channel": "book",
                    "symbol": [normalized],
                    "depth": int(depth),
                    "snapshot": True,
                },
            },
            {
                "method": "subscribe",
                "params": {
                    "channel": "trade",
                    "symbol": [normalized],
                    "snapshot": False,
                },
            },
        ]

    def _process_stream_event(self, event: dict[str, Any]) -> None:
        if event.get("method") == "subscribe" and event.get("success") is False:
            error = event.get("error") or event.get("result") or event
            raise RuntimeError(f"Kraken subscribe error: {error}")
        channel = str(event.get("channel", ""))
        if channel == "book":
            self._process_book_event(event)
        elif channel == "trade":
            self._process_trade_event(event)
        # status/heartbeat/ack messages are ignored

    def _process_book_event(self, event: dict[str, Any]) -> None:
        message_type = str(event.get("type", "")).lower()
        for entry in event.get("data") or []:
            symbol = str(entry.get("symbol", "") or "").upper()
            if symbol and symbol != self.pair:
                continue
            bids = [
                (str(level.get("price")), str(level.get("qty")))
                for level in (entry.get("bids") or [])
                if level.get("price") is not None and level.get("qty") is not None
            ]
            asks = [
                (str(level.get("price")), str(level.get("qty")))
                for level in (entry.get("asks") or [])
                if level.get("price") is not None and level.get("qty") is not None
            ]
            timestamp_ms = _parse_rfc3339_ms(entry.get("timestamp"))
            if message_type == "snapshot":
                self.order_book.load_snapshot(bids, asks)
                self._synced.set()
            else:
                self.order_book.apply_delta(bids, asks)
            if self.on_book_update is not None:
                self.on_book_update(self.order_book, timestamp_ms)

    def _process_trade_event(self, event: dict[str, Any]) -> None:
        for trade in event.get("data") or []:
            symbol = str(trade.get("symbol", "") or "").upper()
            if symbol and symbol != self.pair:
                continue
            self._emit_trade(trade)

    def _emit_trade(self, trade: dict[str, Any]) -> None:
        price = trade.get("price")
        qty = trade.get("qty")
        if price is None or qty is None:
            return
        try:
            price_f = float(price)
            qty_f = float(qty)
        except (TypeError, ValueError):
            return
        timestamp_ms = _parse_rfc3339_ms(trade.get("timestamp")) or 0
        # Kraken trade.side reports the TAKER side. side=sell means the seller
        # initiated (Binance is_buyer_maker=True).
        side = str(trade.get("side", "")).lower()
        is_buyer_maker = side == "sell"
        normalized = {
            "symbol": self.pair,
            "price": price_f,
            "qty": qty_f,
            "timestamp": timestamp_ms,
            "is_buyer_maker": is_buyer_maker,
        }
        self.trade_buffer.add(normalized)
        if self.on_trade is not None:
            self.on_trade(normalized)


def _parse_rfc3339_ms(value: Any) -> int | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
