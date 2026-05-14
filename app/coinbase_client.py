"""Coinbase Advanced Trade public WebSocket client.

Subscribes to ``level2`` and ``market_trades`` for a single product and pipes
the normalized events into an ``OrderBook`` and a ``TradeBuffer``, matching the
interface used by ``BinanceMarketClient`` and ``BybitMarketClient``.

Convention notes:

* ``level2`` messages arrive with ``events[].updates[]`` where each update has
  ``side`` of ``"bid"`` or ``"offer"``, ``price_level`` and ``new_quantity``.
  ``new_quantity == 0`` means the level should be removed.
* ``market_trades`` messages report ``side`` as the **maker** side, so
  ``side == "BUY"`` means the seller initiated (`is_buyer_maker=True`),
  and ``side == "SELL"`` means the buyer initiated. This is inverted vs.
  Bybit's `publicTrade` feed, which reports the taker side.
* Coinbase closes channels after 60-90 s of silence unless ``heartbeats`` is
  subscribed alongside, so we include it.
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
from app.settings import COINBASE_ADVANCED_WS_URL
from app.trade_buffer import TradeBuffer


class CoinbaseMarketClient:
    def __init__(
        self,
        product_id: str,
        order_book: OrderBook,
        trade_buffer: TradeBuffer,
        *,
        on_trade: Callable[[dict[str, Any]], None] | None = None,
        on_book_update: Callable[[OrderBook, int | None], None] | None = None,
        ws_url: str = COINBASE_ADVANCED_WS_URL,
    ) -> None:
        self.product_id = product_id.upper()
        self.order_book = order_book
        self.trade_buffer = trade_buffer
        self.on_trade = on_trade
        self.on_book_update = on_book_update
        self.ws_url = ws_url
        self._stream_task: asyncio.Task | None = None
        self._synced = asyncio.Event()

    async def start(self, sync_timeout: float = 30.0) -> None:
        self._stream_task = asyncio.create_task(
            self._run(),
            name=f"coinbase:{self.product_id}",
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
                raise RuntimeError("Coinbase stream stopped before order book sync")
            if sync_task in done:
                return
            for task in pending:
                task.cancel()
            await self.stop()
            raise TimeoutError("Timed out waiting for Coinbase order book sync")
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
            for message in self._build_subscribe_messages(self.product_id):
                await ws.send(json.dumps(message))
            async for raw in ws:
                self._process_stream_event(json.loads(raw))

    @staticmethod
    def _build_subscribe_messages(product_id: str) -> list[dict[str, Any]]:
        return [
            {
                "type": "subscribe",
                "channel": "heartbeats",
            },
            {
                "type": "subscribe",
                "channel": "level2",
                "product_ids": [product_id],
            },
            {
                "type": "subscribe",
                "channel": "market_trades",
                "product_ids": [product_id],
            },
        ]

    def _process_stream_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("type")
        if event_type == "error":
            raise RuntimeError(
                "Coinbase subscribe error: "
                + str(event.get("message") or event.get("reason") or event)
            )
        channel = str(event.get("channel", ""))
        if channel == "subscriptions":
            # Coinbase responds to an unknown product_id with a subscriptions
            # ack that carries an empty events[0].subscriptions payload.
            events_list = event.get("events") or []
            first = events_list[0] if events_list else {}
            subs = first.get("subscriptions") if isinstance(first, dict) else None
            if isinstance(subs, dict) and not any(subs.values()):
                raise RuntimeError(
                    f"Coinbase rejected subscription for {self.product_id}"
                )
            return
        if channel == "l2_data":
            self._process_level2_event(event)
        elif channel == "market_trades":
            self._process_market_trades_event(event)

    def _process_level2_event(self, event: dict[str, Any]) -> None:
        event_time_ms = _parse_rfc3339_ms(event.get("timestamp"))
        for inner in event.get("events") or []:
            event_type = str(inner.get("type", "")).lower()
            product_id = str(inner.get("product_id", "") or "").upper()
            if product_id and product_id != self.product_id:
                continue
            bids, asks = self._split_updates(inner.get("updates") or [])
            if event_type == "snapshot":
                self.order_book.load_snapshot(bids, asks)
                self._synced.set()
            else:
                self.order_book.apply_delta(bids, asks)
            if self.on_book_update is not None:
                self.on_book_update(self.order_book, event_time_ms)

    def _process_market_trades_event(self, event: dict[str, Any]) -> None:
        for inner in event.get("events") or []:
            for trade in inner.get("trades") or []:
                product_id = str(trade.get("product_id", "") or "").upper()
                if product_id and product_id != self.product_id:
                    continue
                self._emit_trade(trade)

    @staticmethod
    def _split_updates(
        updates: list[dict[str, Any]],
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        bids: list[tuple[str, str]] = []
        asks: list[tuple[str, str]] = []
        for update in updates:
            side = str(update.get("side", "")).lower()
            price = update.get("price_level")
            qty = update.get("new_quantity")
            if price is None or qty is None:
                continue
            level = (str(price), str(qty))
            if side == "bid":
                bids.append(level)
            elif side in {"offer", "ask"}:
                asks.append(level)
        return bids, asks

    def _emit_trade(self, trade: dict[str, Any]) -> None:
        price = trade.get("price")
        qty = trade.get("size")
        if price is None or qty is None:
            return
        try:
            price_f = float(price)
            qty_f = float(qty)
        except (TypeError, ValueError):
            return
        timestamp_ms = _parse_rfc3339_ms(trade.get("time")) or 0
        # Coinbase market_trades.side is the MAKER side, so side=BUY means the
        # seller was the aggressor (equivalent to Binance is_buyer_maker=True).
        side = str(trade.get("side", "")).upper()
        is_buyer_maker = side == "BUY"
        normalized = {
            "symbol": self.product_id,
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
    # Python <3.11 cannot parse the trailing 'Z'; normalize to '+00:00'.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
