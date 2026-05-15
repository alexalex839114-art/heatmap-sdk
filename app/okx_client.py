"""OKX v5 public WebSocket market client.

Subscribes to ``books`` (400-depth snapshot + updates) and ``trades`` for a
single instrument id and pipes the normalized events into an ``OrderBook``
and a ``TradeBuffer``, matching the interface used by
``BinanceMarketClient`` / ``BybitMarketClient`` / ``CoinbaseMarketClient`` /
``KrakenMarketClient``.

In this app the OKX indicator mirrors a Binance USDⓈ-M perpetual futures
symbol, so callers pass the OKX **perpetual swap** instId — e.g.
``BTC-USDT-SWAP`` for the linear USDT-margined perpetual. The same v5 URL
and ``books`` / ``trades`` channels also accept other instrument types
(spot, futures, options) without code changes.

Convention notes:

* ``books`` snapshot and update events arrive on the same channel; the
  message-level ``action`` field is ``"snapshot"`` for the initial dump and
  ``"update"`` for incrementals. Each level is encoded as
  ``[price, size, liquidatedQty, orderCount]``. A ``size == "0"`` update
  removes the level (matches Binance/Kraken/Coinbase semantics).
* ``trades.side`` reports the **taker** side, so ``side == "sell"`` means the
  seller was the aggressor (equivalent to Binance ``is_buyer_maker=True``),
  matching Kraken's convention.
* OKX delivers timestamps as millisecond-epoch *strings*, not numbers.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from contextlib import suppress
from typing import Any

import websockets
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    ConnectionClosedOK,
)

from app.order_book import OrderBook
from app.settings import OKX_PUBLIC_WS_V5_URL
from app.trade_buffer import TradeBuffer


class OkxUnsupportedInstrumentError(RuntimeError):
    """Raised when OKX rejects the subscribe because the instId is not listed.

    Distinct from generic ``RuntimeError`` so callers can surface this as
    ``indicator_status=unavailable`` (a known-gap rather than a hard error
    that should be retried). OKX returns this with ``event=error`` and a
    message like ``"Wrong URL or channel:books,instId:FOO-USDT doesn't exist"``
    (error code ``60018``).
    """


# OKX error codes that mean "instrument is not listed on this exchange".
# We treat these as a soft-fail (indicator unavailable) rather than a crash.
_OKX_UNSUPPORTED_INSTRUMENT_CODES = frozenset({"60018"})
_OKX_UNSUPPORTED_INSTRUMENT_MARKERS = (
    "doesn't exist",
    "does not exist",
)


def _is_okx_unsupported_instrument(event: dict[str, Any]) -> bool:
    code = event.get("code")
    if code is not None and str(code) in _OKX_UNSUPPORTED_INSTRUMENT_CODES:
        return True
    msg = event.get("msg")
    if isinstance(msg, str):
        lowered = msg.lower()
        for marker in _OKX_UNSUPPORTED_INSTRUMENT_MARKERS:
            if marker in lowered:
                return True
    return False


class OkxMarketClient:
    def __init__(
        self,
        inst_id: str,
        order_book: OrderBook,
        trade_buffer: TradeBuffer,
        *,
        on_trade: Callable[[dict[str, Any]], None] | None = None,
        on_book_update: Callable[[OrderBook, int | None], None] | None = None,
        ws_url: str = OKX_PUBLIC_WS_V5_URL,
    ) -> None:
        self.inst_id = inst_id.upper()
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
            name=f"okx:{self.inst_id}",
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
                raise RuntimeError("OKX stream stopped before order book sync")
            if sync_task in done:
                return
            for task in pending:
                task.cancel()
            await self.stop()
            raise TimeoutError("Timed out waiting for OKX order book sync")
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
            for message in self._build_subscribe_messages(self.inst_id):
                await ws.send(json.dumps(message))
            async for raw in ws:
                self._process_stream_event(json.loads(raw))

    @staticmethod
    def _build_subscribe_messages(inst_id: str) -> list[dict[str, Any]]:
        normalized = inst_id.upper()
        return [
            {
                "op": "subscribe",
                "args": [
                    {"channel": "books", "instId": normalized},
                    {"channel": "trades", "instId": normalized},
                ],
            }
        ]

    def _process_stream_event(self, event: dict[str, Any]) -> None:
        event_kind = event.get("event")
        if event_kind == "error":
            msg = event.get("msg") or event.get("code") or event
            if _is_okx_unsupported_instrument(event):
                raise OkxUnsupportedInstrumentError(
                    f"OKX instrument {self.inst_id} is not listed: {msg}"
                )
            raise RuntimeError(f"OKX subscribe error: {msg}")
        if event_kind in {
            "subscribe",
            "unsubscribe",
            "channel-conn-count",
            "channel-conn-count-error",
        }:
            return
        arg = event.get("arg") or {}
        channel = str(arg.get("channel", ""))
        if channel == "books":
            self._process_book_event(event)
        elif channel == "trades":
            self._process_trade_event(event)

    def _process_book_event(self, event: dict[str, Any]) -> None:
        action = str(event.get("action", "")).lower()
        for entry in event.get("data") or []:
            bids = [
                (str(level[0]), str(level[1]))
                for level in (entry.get("bids") or [])
                if len(level) >= 2
            ]
            asks = [
                (str(level[0]), str(level[1]))
                for level in (entry.get("asks") or [])
                if len(level) >= 2
            ]
            timestamp_ms = _parse_okx_ts(entry.get("ts"))
            if action == "snapshot":
                self.order_book.load_snapshot(bids, asks)
                self._synced.set()
            else:
                self.order_book.apply_delta(bids, asks)
            if self.on_book_update is not None:
                self.on_book_update(self.order_book, timestamp_ms)

    def _process_trade_event(self, event: dict[str, Any]) -> None:
        for trade in event.get("data") or []:
            inst_id = str(trade.get("instId", "") or "").upper()
            if inst_id and inst_id != self.inst_id:
                continue
            self._emit_trade(trade)

    def _emit_trade(self, trade: dict[str, Any]) -> None:
        price = trade.get("px")
        qty = trade.get("sz")
        if price is None or qty is None:
            return
        try:
            price_f = float(price)
            qty_f = float(qty)
        except (TypeError, ValueError):
            return
        timestamp_ms = _parse_okx_ts(trade.get("ts")) or 0
        # OKX trades.side is the TAKER side. side=sell => seller initiated =>
        # buyer was the maker (Binance is_buyer_maker=True).
        side = str(trade.get("side", "")).lower()
        is_buyer_maker = side == "sell"
        normalized = {
            "symbol": self.inst_id,
            "price": price_f,
            "qty": qty_f,
            "timestamp": timestamp_ms,
            "is_buyer_maker": is_buyer_maker,
        }
        self.trade_buffer.add(normalized)
        if self.on_trade is not None:
            self.on_trade(normalized)


def _parse_okx_ts(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
