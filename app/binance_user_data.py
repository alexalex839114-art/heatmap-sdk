"""Binance USD-M Futures user data WebSocket client.

Subscribes to the per-user listen key stream and surfaces parsed
`PositionState` snapshots from `ACCOUNT_UPDATE` events. Keeps the listen key
alive in the background and reconnects with exponential backoff if the
WebSocket drops.

Scope:

* Only `ACCOUNT_UPDATE` is parsed (the assistant tracks positions; orders are
  observed indirectly through position changes plus `OrderExecutor` state).
* Only one-way positions (`ps == "BOTH"`) are considered, matching the rest
  of the application.
* The client owns the listen key lifecycle: it requests a key on `start()`,
  renews it on a fixed cadence, and revokes it on `stop()`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import websockets
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    ConnectionClosedOK,
)

from app.binance_account import BinanceAccountClient
from app.position import PositionState, parse_account_update_positions
from app.settings import (
    BINANCE_FAPI_USER_WS_URL,
    LISTEN_KEY_KEEPALIVE_INTERVAL_MS,
)


logger = logging.getLogger(__name__)


AccountUpdateCallback = Callable[[dict[str, PositionState], dict[str, Any]], Awaitable[None]]


class BinanceUserDataClient:
    """Manages the Binance USD-M Futures user data stream.

    The client is intentionally narrow: it does **not** know about the heatmap
    service or any FastAPI WebSocket. It only knows how to:

    1. obtain and renew a listen key,
    2. open a WebSocket to ``<BINANCE_FAPI_USER_WS_URL>/<listenKey>``,
    3. invoke ``on_account_update`` whenever a parseable ``ACCOUNT_UPDATE``
       arrives.

    Higher layers translate the parsed positions into trading-state updates.
    """

    def __init__(
        self,
        account_client: BinanceAccountClient,
        on_account_update: AccountUpdateCallback,
        *,
        ws_url: str = BINANCE_FAPI_USER_WS_URL,
        keepalive_interval_ms: int = LISTEN_KEY_KEEPALIVE_INTERVAL_MS,
        ws_connect: Callable[..., Any] | None = None,
    ) -> None:
        self._account_client = account_client
        self._on_account_update = on_account_update
        self._ws_url = ws_url.rstrip("/")
        self._keepalive_interval_s = max(0.01, keepalive_interval_ms / 1000)
        self._ws_connect = ws_connect or websockets.connect

        self._listen_key: str | None = None
        self._stream_task: asyncio.Task | None = None
        self._keepalive_task: asyncio.Task | None = None
        self._stopping = False
        # Tracks whether a listen key was ever requested in this lifetime,
        # so we can always issue a DELETE on stop() even after reconnect
        # cycles have cleared self._listen_key.
        self._has_active_listen_key = False
        self.connected: asyncio.Event = asyncio.Event()
        self.last_account_update_ms: int | None = None
        self.last_error: str | None = None

    @property
    def is_connected(self) -> bool:
        return self.connected.is_set()

    async def start(self) -> None:
        """Acquire a listen key and start the stream / keepalive loops.

        Raises whatever ``start_listen_key`` raises (e.g. ``BinanceAPIError``)
        so the caller can decide whether to continue without user data.
        """
        if self._stream_task is not None:
            return
        self._stopping = False
        self._listen_key = await self._account_client.start_listen_key()
        self._has_active_listen_key = True
        self._stream_task = asyncio.create_task(
            self._run_stream(),
            name="binance-user-data-stream",
        )
        self._keepalive_task = asyncio.create_task(
            self._run_keepalive(),
            name="binance-user-data-keepalive",
        )

    async def stop(self) -> None:
        self._stopping = True
        self.connected.clear()
        for task in (self._stream_task, self._keepalive_task):
            if task is None:
                continue
            task.cancel()
            with suppress(
                asyncio.CancelledError,
                ConnectionClosed,
                ConnectionClosedError,
                ConnectionClosedOK,
                Exception,
            ):
                await task
        self._stream_task = None
        self._keepalive_task = None
        if self._has_active_listen_key:
            with suppress(Exception):
                await self._account_client.close_listen_key()
            self._has_active_listen_key = False
            self._listen_key = None

    async def _run_stream(self) -> None:
        backoff_seconds = 1.0
        while not self._stopping:
            if self._listen_key is None:
                try:
                    self._listen_key = await self._account_client.start_listen_key()
                    self._has_active_listen_key = True
                except Exception as exc:
                    self.last_error = f"listen_key: {exc}"
                    logger.warning("Failed to obtain listen key: %s", exc)
                    await asyncio.sleep(min(backoff_seconds, 30.0))
                    backoff_seconds = min(backoff_seconds * 2, 30.0)
                    continue
            stream_url = f"{self._ws_url}/{self._listen_key}"
            try:
                async with self._ws_connect(
                    stream_url,
                    ping_interval=20,
                    ping_timeout=20,
                    open_timeout=30,
                    max_size=4 * 1024 * 1024,
                ) as ws:
                    self.connected.set()
                    backoff_seconds = 1.0
                    self.last_error = None
                    async for raw in ws:
                        try:
                            event = json.loads(raw)
                        except (TypeError, ValueError):
                            continue
                        await self._dispatch_event(event)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — websocket layer raises broadly.
                self.last_error = str(exc) or type(exc).__name__
                logger.warning("Binance user data stream dropped: %s", exc)
            finally:
                self.connected.clear()
            if self._stopping:
                return
            # On disconnect, force a fresh listen key on the next attempt:
            # Binance recycles listen keys aggressively after disconnects.
            self._listen_key = None
            await asyncio.sleep(min(backoff_seconds, 30.0))
            backoff_seconds = min(backoff_seconds * 2, 30.0)

    async def _run_keepalive(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(self._keepalive_interval_s)
                if self._stopping or self._listen_key is None:
                    continue
                try:
                    await self._account_client.keepalive_listen_key()
                except Exception as exc:  # noqa: BLE001
                    self.last_error = f"keepalive: {exc}"
                    logger.warning("listen key keepalive failed: %s", exc)
            except asyncio.CancelledError:
                raise

    async def _dispatch_event(self, event: dict[str, Any]) -> None:
        if not isinstance(event, dict):
            return
        event_type = event.get("e")
        if event_type != "ACCOUNT_UPDATE":
            return
        positions = parse_account_update_positions(event)
        update_time = event.get("E")
        if isinstance(update_time, (int, float)):
            self.last_account_update_ms = int(update_time)
        try:
            await self._on_account_update(positions, event)
        except Exception:  # noqa: BLE001
            logger.exception("user data callback raised")
