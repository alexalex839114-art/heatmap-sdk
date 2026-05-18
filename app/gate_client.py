"""Gate.io v4 public WebSocket market client for USDT-margined perpetual futures.

Subscribes to the legacy snapshot channels ``futures.order_book`` and
``futures.trades`` for a single contract (e.g. ``BTC_USDT``) and pipes the
normalized events into an ``OrderBook`` and a ``TradeBuffer``, matching the
interface used by ``BinanceMarketClient`` / ``BybitMarketClient`` /
``OkxMarketClient``.

The host app is driven by Binance USDⓈ-M perpetual futures, so the Gate
indicator must read the matching market — the Gate USDT-margined linear
perpetual contract — not spot. Gate's USDT futures WebSocket is served from
a dedicated endpoint (``wss://fx-ws.gateio.ws/v4/ws/usdt``) and uses the
``<BASE>_<QUOTE>`` underscore convention (``BTC_USDT``, ``ETH_USDT``,
``IRYS_USDT``...).

Convention notes:

* The subscribe protocol is JSON over text frames with the shape
  ``{"time": <unix>, "channel": <name>, "event": "subscribe", "payload": [...]}``.
  Gate replies with the same envelope, ``event="subscribe"`` and either
  ``result.status=="success"`` (ack) or ``result.status=="fail"`` plus an
  ``error`` object (``{"code": 2, "message": "unknown currency pair X_USDT"}``
  when the contract is not listed).
* ``futures.order_book`` pushes *full* snapshots of the top N levels at the
  requested interval (every event has ``event="all"`` and a complete
  ``asks`` / ``bids`` array). We treat every snapshot as a fresh load — no
  delta state to maintain, no REST bootstrap required. This is simpler and
  more robust than the incremental ``futures.order_book_update`` channel.
* ``futures.trades`` pushes batches of recent trades; the ``size`` field is
  **signed**: positive means the buyer was the taker (aggressor on the ask),
  negative means the seller was the taker (aggressor on the bid). We map
  ``size < 0 -> is_buyer_maker=True`` to match Binance/OKX/Kraken semantics
  and emit ``abs(size)`` as the quantity. Sizes are denominated in contract
  units (Gate's per-contract multiplier is constant per contract, so the
  values are internally consistent for VPIN/BVC even without rescaling).
* Gate timestamps come as integer ``create_time_ms`` (trades) or ``t``
  (order book) in epoch milliseconds.
"""

from __future__ import annotations

import asyncio
import json
import time
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
from app.settings import (
    GATE_FUTURES_USDT_PUBLIC_WS_URL,
    GATE_ORDER_BOOK_INTERVAL,
    GATE_ORDER_BOOK_LEVEL,
)
from app.trade_buffer import TradeBuffer


class GateUnsupportedContractError(RuntimeError):
    """Raised when Gate.io rejects the subscribe because the contract is not listed.

    Distinct from generic ``RuntimeError`` so callers can surface this as
    ``indicator_status=unavailable`` (a known-gap rather than a hard error
    that should be retried). Gate returns this as an ``event="subscribe"``
    reply with ``result.status="fail"`` and an ``error`` object such as
    ``{"code": 2, "message": "unknown currency pair FOO_USDT"}``.
    """


# Gate.io error codes that mean "contract is not listed on this exchange".
# We treat these as a soft-fail (indicator unavailable) rather than a crash.
_GATE_UNSUPPORTED_CONTRACT_CODES = frozenset({"2"})
_GATE_UNSUPPORTED_CONTRACT_MARKERS = (
    "unknown currency pair",
    "invalid argument",  # Gate occasionally surfaces this for bad contracts
    "contract not found",
)


def _is_gate_unsupported_contract(event: dict[str, Any]) -> bool:
    """Detect a Gate.io subscribe-fail caused by an unlisted contract."""
    error = event.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        if code is not None and str(code) in _GATE_UNSUPPORTED_CONTRACT_CODES:
            return True
        message = error.get("message")
        if isinstance(message, str):
            lowered = message.lower()
            for marker in _GATE_UNSUPPORTED_CONTRACT_MARKERS:
                if marker in lowered:
                    return True
    return False


class GateMarketClient:
    def __init__(
        self,
        contract: str,
        order_book: OrderBook,
        trade_buffer: TradeBuffer,
        *,
        on_trade: Callable[[dict[str, Any]], None] | None = None,
        on_book_update: Callable[[OrderBook, int | None], None] | None = None,
        ws_url: str = GATE_FUTURES_USDT_PUBLIC_WS_URL,
        order_book_level: str = GATE_ORDER_BOOK_LEVEL,
        order_book_interval: str = GATE_ORDER_BOOK_INTERVAL,
    ) -> None:
        self.contract = contract.upper()
        self.order_book = order_book
        self.trade_buffer = trade_buffer
        self.on_trade = on_trade
        self.on_book_update = on_book_update
        self.ws_url = ws_url
        self.order_book_level = order_book_level
        self.order_book_interval = order_book_interval
        self._stream_task: asyncio.Task | None = None
        self._synced = asyncio.Event()

    async def start(self, sync_timeout: float = 30.0) -> None:
        self._stream_task = asyncio.create_task(
            self._run(),
            name=f"gate:{self.contract}",
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
                raise RuntimeError("Gate stream stopped before order book sync")
            if sync_task in done:
                return
            for task in pending:
                task.cancel()
            await self.stop()
            raise TimeoutError("Timed out waiting for Gate order book sync")
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
            additional_headers={"X-Gate-Size-Decimal": "1"},
        ) as ws:
            for message in self._build_subscribe_messages(
                self.contract,
                level=self.order_book_level,
                interval=self.order_book_interval,
            ):
                await ws.send(json.dumps(message))
            async for raw in ws:
                self._process_stream_event(json.loads(raw))

    @staticmethod
    def _build_subscribe_messages(
        contract: str,
        *,
        level: str = GATE_ORDER_BOOK_LEVEL,
        interval: str = GATE_ORDER_BOOK_INTERVAL,
        now_unix: int | None = None,
    ) -> list[dict[str, Any]]:
        normalized = contract.upper()
        t = int(now_unix) if now_unix is not None else int(time.time())
        return [
            {
                "time": t,
                "channel": "futures.order_book",
                "event": "subscribe",
                "payload": [normalized, level, interval],
            },
            {
                "time": t,
                "channel": "futures.trades",
                "event": "subscribe",
                "payload": [normalized],
            },
        ]

    def _process_stream_event(self, event: dict[str, Any]) -> None:
        event_kind = event.get("event")
        channel = str(event.get("channel", "") or "")
        if event_kind == "subscribe":
            result = event.get("result") or {}
            status = str(result.get("status", "") or "").lower()
            if status == "fail" or event.get("error"):
                if _is_gate_unsupported_contract(event):
                    err = event.get("error") or {}
                    message = err.get("message") or err.get("code") or event
                    raise GateUnsupportedContractError(
                        f"Gate contract {self.contract} is not listed: {message}"
                    )
                raise RuntimeError(
                    f"Gate subscribe error on {channel}: "
                    f"{event.get('error') or result}"
                )
            return
        if event_kind in {"unsubscribe", "ping", "pong"}:
            return
        if channel == "futures.order_book" and event_kind == "all":
            self._process_book_snapshot(event)
        elif channel == "futures.trades" and event_kind == "update":
            self._process_trades(event)

    def _process_book_snapshot(self, event: dict[str, Any]) -> None:
        result = event.get("result") or {}
        contract = str(result.get("contract", "") or "").upper()
        if contract and contract != self.contract:
            return
        bids_raw = result.get("bids") or []
        asks_raw = result.get("asks") or []
        bids = [
            (str(level.get("p")), str(level.get("s")))
            for level in bids_raw
            if isinstance(level, dict) and level.get("p") is not None
        ]
        asks = [
            (str(level.get("p")), str(level.get("s")))
            for level in asks_raw
            if isinstance(level, dict) and level.get("p") is not None
        ]
        timestamp_ms = _coerce_int(result.get("t"))
        # Every futures.order_book "all" message is a full top-N snapshot,
        # not an incremental update, so we always replace the local book.
        self.order_book.load_snapshot(bids, asks)
        self._synced.set()
        if self.on_book_update is not None:
            self.on_book_update(self.order_book, timestamp_ms)

    def _process_trades(self, event: dict[str, Any]) -> None:
        data = event.get("result")
        if not isinstance(data, list):
            return
        for trade in data:
            if not isinstance(trade, dict):
                continue
            contract = str(trade.get("contract", "") or "").upper()
            if contract and contract != self.contract:
                continue
            self._emit_trade(trade)

    def _emit_trade(self, trade: dict[str, Any]) -> None:
        price = trade.get("price")
        size = trade.get("size")
        if price is None or size is None:
            return
        try:
            price_f = float(price)
            size_f = float(size)
        except (TypeError, ValueError):
            return
        timestamp_ms = _coerce_int(trade.get("create_time_ms"))
        if timestamp_ms is None:
            timestamp_ms = _coerce_int(trade.get("create_time"))
            if timestamp_ms is not None:
                timestamp_ms *= 1000
        # Gate trades.size is signed: positive = taker buy (buyer aggressor),
        # negative = taker sell (seller aggressor). Map to Binance's
        # is_buyer_maker semantics: taker sell => buyer was maker => True.
        is_buyer_maker = size_f < 0.0
        qty = abs(size_f)
        if qty == 0.0:
            return
        normalized = {
            "symbol": self.contract,
            "price": price_f,
            "qty": qty,
            "timestamp": timestamp_ms or 0,
            "is_buyer_maker": is_buyer_maker,
        }
        self.trade_buffer.add(normalized)
        if self.on_trade is not None:
            self.on_trade(normalized)


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
