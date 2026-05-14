import hmac
import hashlib
from urllib.parse import parse_qs

import httpx
import pytest

from app.assistant_config import BinanceAccountSettings
from app.binance_account import BinanceAccountClient, BinanceAPIError


def _client(handler):
    transport = httpx.MockTransport(handler)
    settings = BinanceAccountSettings(
        api_key="key",
        api_secret="secret",
        base_url="https://example.test",
    )
    return BinanceAccountClient(settings, transport=transport, now_ms=lambda: 1234567890)


def _client_with_clock(handler, clock):
    transport = httpx.MockTransport(handler)
    settings = BinanceAccountSettings(
        api_key="key",
        api_secret="secret",
        base_url="https://example.test",
    )
    return BinanceAccountClient(settings, transport=transport, now_ms=clock)


@pytest.mark.asyncio
async def test_start_listen_key_sends_api_key_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["api_key"] = request.headers.get("X-MBX-APIKEY")
        return httpx.Response(200, json={"listenKey": "abc"})

    client = _client(handler)

    listen_key = await client.start_listen_key()

    assert listen_key == "abc"
    assert seen["path"] == "/fapi/v1/listenKey"
    assert seen["api_key"] == "key"


@pytest.mark.asyncio
async def test_signed_get_adds_timestamp_and_signature():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json=[])

    client = _client(handler)

    await client.signed_get("/fapi/v3/positionRisk", {"symbol": "BTCUSDT"})

    parsed = parse_qs(seen["query"])
    assert parsed["symbol"] == ["BTCUSDT"]
    assert parsed["recvWindow"] == ["10000"]
    assert parsed["timestamp"] == ["1234567890"]
    unsigned = "symbol=BTCUSDT&recvWindow=10000&timestamp=1234567890"
    expected = hmac.new(b"secret", unsigned.encode(), hashlib.sha256).hexdigest()
    assert parsed["signature"] == [expected]


@pytest.mark.asyncio
async def test_signed_get_adds_recv_window_to_signed_query():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json=[])

    client = _client(handler)

    await client.signed_get("/fapi/v3/positionRisk", {"symbol": "BTCUSDT"})

    parsed = parse_qs(seen["query"])
    assert parsed["recvWindow"] == ["10000"]


@pytest.mark.asyncio
async def test_signed_get_uses_binance_server_time_offset():
    seen = {}
    ticks = iter([1000, 1100, 1200])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/fapi/v1/time":
            return httpx.Response(200, json={"serverTime": 5100})
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json=[])

    client = _client_with_clock(handler, lambda: next(ticks))

    await client.signed_get("/fapi/v3/positionRisk", {"symbol": "BTCUSDT"})

    parsed = parse_qs(seen["query"])
    assert parsed["timestamp"] == ["5250"]


@pytest.mark.asyncio
async def test_signed_get_raises_binance_error_with_response_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"code": -1021, "msg": "Timestamp for this request is outside recvWindow."},
        )

    client = _client(handler)

    with pytest.raises(BinanceAPIError) as exc:
        await client.signed_get("/fapi/v3/positionRisk", {"symbol": "BTCUSDT"})

    assert exc.value.status_code == 400
    assert exc.value.code == -1021
    assert "outside recvWindow" in str(exc.value)


@pytest.mark.asyncio
async def test_cancel_all_open_orders_sends_signed_delete_for_symbol():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json={"code": 200, "msg": "done"})

    client = _client(handler)

    result = await client.cancel_all_open_orders("btcusdt")

    parsed = parse_qs(seen["query"])
    assert result["code"] == 200
    assert seen["method"] == "DELETE"
    assert seen["path"] == "/fapi/v1/allOpenOrders"
    assert parsed["symbol"] == ["BTCUSDT"]
    assert parsed["timestamp"] == ["1234567890"]


@pytest.mark.asyncio
async def test_fetch_position_normalizes_one_way_position():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.01",
                    "entryPrice": "65000.0",
                    "breakEvenPrice": "65010.0",
                    "unRealizedProfit": "4.2",
                    "positionSide": "BOTH",
                    "updateTime": 123,
                }
            ],
        )

    client = _client(handler)

    position = await client.fetch_position("BTCUSDT")

    assert position is not None
    assert position.side == "LONG"
    assert position.quantity == 0.01
    assert position.unrealized_pnl == 4.2


@pytest.mark.asyncio
async def test_fetch_position_returns_none_when_symbol_missing():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    client = _client(handler)

    assert await client.fetch_position("BTCUSDT") is None
