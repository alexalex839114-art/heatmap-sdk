"""Unit tests for BinanceUserDataClient.

Exercise listen-key lifecycle, ACCOUNT_UPDATE dispatching and reconnect
backoff with an in-memory WebSocket stub. No real network calls.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any, Callable

import pytest

from app.binance_user_data import BinanceUserDataClient


class _StubWebSocket:
    def __init__(
        self,
        messages: list[Any] | None = None,
        *,
        raise_after: Callable[[], Exception] | None = None,
    ) -> None:
        self._messages = deque(messages or [])
        self._raise_after = raise_after
        self.closed = False

    async def __aenter__(self) -> "_StubWebSocket":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.closed = True

    def __aiter__(self) -> "_StubWebSocket":
        return self

    async def __anext__(self) -> str:
        if self._messages:
            return self._messages.popleft()
        if self._raise_after is not None:
            raise self._raise_after()
        # No more messages and no raise instruction: park indefinitely.
        await asyncio.Event().wait()
        raise StopAsyncIteration


class FakeAccountClient:
    def __init__(self, listen_keys: list[str] | None = None) -> None:
        self._listen_keys = deque(listen_keys or ["lk-1", "lk-2", "lk-3"])
        self.keepalive_calls = 0
        self.close_calls = 0
        self.start_calls = 0
        self.start_should_raise: Exception | None = None

    async def start_listen_key(self) -> str:
        self.start_calls += 1
        if self.start_should_raise is not None:
            raise self.start_should_raise
        if not self._listen_keys:
            return "lk-fallback"
        return self._listen_keys.popleft()

    async def keepalive_listen_key(self) -> None:
        self.keepalive_calls += 1

    async def close_listen_key(self) -> None:
        self.close_calls += 1


@pytest.mark.asyncio
async def test_dispatch_invokes_callback_on_account_update():
    account = FakeAccountClient()
    received = []

    async def callback(positions, event):
        received.append((positions, event))

    connect_url: dict[str, str] = {}

    def fake_connect(url, **_kwargs):
        connect_url["url"] = url
        return _StubWebSocket(
            messages=[
                json.dumps(
                    {
                        "e": "ACCOUNT_UPDATE",
                        "E": 1_700_000_000_000,
                        "a": {
                            "P": [
                                {
                                    "s": "BTCUSDT",
                                    "pa": "0.5",
                                    "ep": "65000",
                                    "bep": "65010",
                                    "up": "1.0",
                                    "ps": "BOTH",
                                }
                            ]
                        },
                    }
                )
            ],
            raise_after=lambda: RuntimeError("stop test"),
        )

    client = BinanceUserDataClient(
        account,  # type: ignore[arg-type]
        on_account_update=callback,
        ws_connect=fake_connect,
    )
    await client.start()
    # Allow the stream task to consume the queued message.
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.01)
    await client.stop()

    assert connect_url["url"].endswith("/lk-1")
    assert len(received) == 1
    positions, _event = received[0]
    assert "BTCUSDT" in positions
    assert positions["BTCUSDT"].side == "LONG"
    assert client.last_account_update_ms == 1_700_000_000_000
    assert account.close_calls == 1


@pytest.mark.asyncio
async def test_dispatch_ignores_unrelated_events():
    account = FakeAccountClient()
    received: list[Any] = []

    async def callback(positions, event):
        received.append((positions, event))

    def fake_connect(url, **_kwargs):
        return _StubWebSocket(
            messages=[
                json.dumps({"e": "ORDER_TRADE_UPDATE", "o": {}}),
                json.dumps({"e": "MARGIN_CALL"}),
                "not-json",
            ],
            raise_after=lambda: RuntimeError("done"),
        )

    client = BinanceUserDataClient(
        account,  # type: ignore[arg-type]
        on_account_update=callback,
        ws_connect=fake_connect,
    )
    await client.start()
    await asyncio.sleep(0.05)
    await client.stop()

    assert received == []


@pytest.mark.asyncio
async def test_start_failure_propagates_and_does_not_leak_tasks():
    account = FakeAccountClient()
    account.start_should_raise = RuntimeError("listen-key denied")

    async def callback(positions, event):
        pass

    client = BinanceUserDataClient(
        account,  # type: ignore[arg-type]
        on_account_update=callback,
        ws_connect=lambda *a, **kw: _StubWebSocket(),
    )

    with pytest.raises(RuntimeError, match="listen-key denied"):
        await client.start()

    assert client._stream_task is None
    assert client._keepalive_task is None


@pytest.mark.asyncio
async def test_stop_revokes_listen_key_once():
    account = FakeAccountClient()

    async def callback(positions, event):
        pass

    def fake_connect(url, **_kwargs):
        return _StubWebSocket(raise_after=lambda: RuntimeError("stop test"))

    client = BinanceUserDataClient(
        account,  # type: ignore[arg-type]
        on_account_update=callback,
        ws_connect=fake_connect,
    )
    await client.start()
    await client.stop()
    await client.stop()  # idempotent

    assert account.close_calls == 1


@pytest.mark.asyncio
async def test_keepalive_runs_in_background():
    account = FakeAccountClient()

    async def callback(positions, event):
        pass

    def fake_connect(url, **_kwargs):
        # Stay connected so the keepalive task sees a non-None listen key.
        return _StubWebSocket()

    client = BinanceUserDataClient(
        account,  # type: ignore[arg-type]
        on_account_update=callback,
        # Sub-second keepalive so the test stays fast.
        keepalive_interval_ms=10,
        ws_connect=fake_connect,
    )
    await client.start()
    # Wait long enough for at least one keepalive tick.
    await asyncio.sleep(0.1)
    await client.stop()

    assert account.keepalive_calls >= 1
