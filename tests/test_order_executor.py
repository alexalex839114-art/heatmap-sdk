import pytest
import logging

from app.order_executor import (
    OrderExecutor,
    build_market_close_order,
    build_market_open_order,
)
from app.position import PositionState


def test_long_position_closes_with_sell_market_reduce_only():
    pos = PositionState(symbol="BTCUSDT", amount=0.01, entry_price=65000)

    order = build_market_close_order(pos, client_order_id="close-test")

    assert order["symbol"] == "BTCUSDT"
    assert order["side"] == "SELL"
    assert order["type"] == "MARKET"
    assert order["quantity"] == "0.01"
    assert order["reduceOnly"] == "true"
    assert order["newClientOrderId"] == "close-test"


def test_short_position_closes_with_buy_market_reduce_only():
    pos = PositionState(symbol="BTCUSDT", amount=-0.02, entry_price=65000)

    order = build_market_close_order(pos, client_order_id="close-test")

    assert order["side"] == "BUY"
    assert order["quantity"] == "0.02"


def test_flat_position_cannot_build_close_order():
    pos = PositionState(symbol="BTCUSDT", amount=0.0, entry_price=0.0)

    with pytest.raises(ValueError, match="Cannot close a flat position"):
        build_market_close_order(pos, client_order_id="close-test")


def test_long_entry_opens_with_buy_market_order_sized_by_usdt():
    order = build_market_open_order(
        "BTCUSDT",
        side="LONG",
        notional_usdt=13.0,
        mark_price=65000.0,
        client_order_id="open-test",
    )

    assert order["symbol"] == "BTCUSDT"
    assert order["side"] == "BUY"
    assert order["type"] == "MARKET"
    assert order["quantity"] == "0.0002"
    assert "reduceOnly" not in order
    assert order["newClientOrderId"] == "open-test"


def test_entry_quantity_rejects_values_below_min_quantity_after_rounding():
    with pytest.raises(ValueError, match="below exchange minimum"):
        build_market_open_order(
            "BTCUSDT",
            side="LONG",
            notional_usdt=10.0,
            mark_price=65000.0,
            client_order_id="open-test",
            quantity_step=0.001,
            min_quantity=0.001,
        )


def test_short_entry_opens_with_sell_market_order():
    order = build_market_open_order(
        "BTCUSDT",
        side="SHORT",
        notional_usdt=13.0,
        mark_price=65000.0,
        client_order_id="open-test",
    )

    assert order["side"] == "SELL"
    assert order["quantity"] == "0.0002"


class FakeAccountClient:
    def __init__(self):
        self.requests = []

    async def signed_post(self, path, params):
        self.requests.append((path, params))
        return {"status": "FILLED", "clientOrderId": params["newClientOrderId"]}

    async def cancel_all_open_orders(self, symbol):
        self.requests.append(("/fapi/v1/allOpenOrders", {"symbol": symbol}))
        return {"code": 200, "msg": "done"}


@pytest.mark.asyncio
async def test_executor_sends_exactly_one_close_request():
    account = FakeAccountClient()
    executor = OrderExecutor(account, client_order_id_factory=lambda: "close-1")
    pos = PositionState(symbol="BTCUSDT", amount=0.01, entry_price=65000)

    result = await executor.close_position(pos)

    assert result["status"] == "FILLED"
    assert len(account.requests) == 1
    assert account.requests[0][0] == "/fapi/v1/order"
    assert account.requests[0][1]["side"] == "SELL"


@pytest.mark.asyncio
async def test_executor_blocks_duplicate_close_while_pending():
    account = FakeAccountClient()
    executor = OrderExecutor(account, client_order_id_factory=lambda: "close-1")
    executor.mark_pending_for_test()
    pos = PositionState(symbol="BTCUSDT", amount=0.01, entry_price=65000)

    with pytest.raises(RuntimeError, match="Close order already pending"):
        await executor.close_position(pos)

    assert account.requests == []


@pytest.mark.asyncio
async def test_executor_sends_open_request_and_blocks_duplicate_open():
    account = FakeAccountClient()
    executor = OrderExecutor(account, client_order_id_factory=lambda: "open-1")

    result = await executor.open_position(
        "BTCUSDT",
        side="LONG",
        notional_usdt=13.0,
        mark_price=65000.0,
    )

    assert result["status"] == "FILLED"
    assert len(account.requests) == 1
    assert account.requests[0][0] == "/fapi/v1/order"
    assert account.requests[0][1]["side"] == "BUY"

    with pytest.raises(RuntimeError, match="Open order already pending"):
        await executor.open_position(
            "BTCUSDT",
            side="LONG",
            notional_usdt=13.0,
            mark_price=65000.0,
        )


@pytest.mark.asyncio
async def test_executor_logs_open_order_attempt_and_result(caplog):
    account = FakeAccountClient()
    executor = OrderExecutor(account, client_order_id_factory=lambda: "open-1")

    with caplog.at_level(logging.INFO, logger="app.order_executor"):
        await executor.open_position(
            "BTCUSDT",
            side="LONG",
            notional_usdt=13.0,
            mark_price=65000.0,
        )

    messages = [record.getMessage() for record in caplog.records]
    assert any("Submitting MARKET entry" in message for message in messages)
    assert any("Entry order accepted" in message for message in messages)


@pytest.mark.asyncio
async def test_executor_cancels_all_open_orders_for_symbol():
    account = FakeAccountClient()
    executor = OrderExecutor(account)

    result = await executor.cancel_all_open_orders("btcusdt")

    assert result["code"] == 200
    assert account.requests == [("/fapi/v1/allOpenOrders", {"symbol": "BTCUSDT"})]
