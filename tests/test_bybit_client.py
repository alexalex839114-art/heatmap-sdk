import asyncio

import pytest

from app.bybit_client import BybitMarketClient
from app.order_book import OrderBook
from app.trade_buffer import TradeBuffer


def test_bybit_subscribe_message_uses_orderbook_and_public_trade_topics():
    message = BybitMarketClient._build_subscribe_message("BTCUSDT", depth=50)

    assert message == {
        "op": "subscribe",
        "args": ["orderbook.50.BTCUSDT", "publicTrade.BTCUSDT"],
    }


def test_bybit_orderbook_snapshot_replaces_book_and_calls_callback():
    seen = []
    book = OrderBook()
    client = BybitMarketClient(
        "BTCUSDT",
        book,
        TradeBuffer(),
        on_book_update=lambda book, event_time: seen.append(
            (book.best_bid(), book.best_ask(), event_time)
        ),
    )

    client._process_orderbook_event(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "snapshot",
            "ts": 1700000000001,
            "data": {
                "b": [["100.0", "1.0"]],
                "a": [["101.0", "2.0"]],
            },
        }
    )

    assert book.best_bid() == 100.0
    assert book.best_ask() == 101.0
    assert seen == [(100.0, 101.0, 1700000000001)]


def test_bybit_orderbook_delta_updates_book():
    book = OrderBook()
    book.load_snapshot(bids=[("100.0", "1.0")], asks=[("101.0", "2.0")])
    client = BybitMarketClient("BTCUSDT", book, TradeBuffer())

    client._process_orderbook_event(
        {
            "topic": "orderbook.50.BTCUSDT",
            "type": "delta",
            "ts": 1700000000002,
            "data": {
                "b": [["100.5", "3.0"]],
                "a": [["101.0", "0"]],
            },
        }
    )

    assert book.best_bid() == 100.5
    assert book.best_ask() is None


@pytest.mark.asyncio
async def test_bybit_start_surfaces_stream_failure_before_sync():
    client = BybitMarketClient("BTCUSDT", OrderBook(), TradeBuffer())

    async def failing_run():
        await asyncio.sleep(0)
        raise RuntimeError("bybit unavailable")

    client._run = failing_run

    with pytest.raises(RuntimeError, match="bybit unavailable"):
        await client.start(sync_timeout=0.1)


@pytest.mark.asyncio
async def test_bybit_start_seeds_recent_trades_after_orderbook_sync():
    client = BybitMarketClient("BTCUSDT", OrderBook(), TradeBuffer())
    seeded = []
    keep_running = asyncio.Event()

    async def synced_run():
        client._synced.set()
        await keep_running.wait()

    async def seed_recent_trades():
        seeded.append(True)

    client._run = synced_run
    client._seed_recent_trades = seed_recent_trades

    await client.start(sync_timeout=0.1)

    assert client._stream_task is not None
    assert not client._stream_task.done()
    assert client._stream_task.cancelling() == 0

    await client.stop()

    assert seeded == [True]


def test_bybit_recent_trade_payload_reuses_public_trade_parser():
    seen = []
    client = BybitMarketClient(
        "BTCUSDT",
        OrderBook(),
        TradeBuffer(),
        on_trade=seen.append,
    )

    client._process_recent_trade_payload(
        {
            "retCode": 0,
            "result": {
                "list": [
                    {
                        "time": "1700000000124",
                        "symbol": "BTCUSDT",
                        "side": "Sell",
                        "size": "0.2",
                        "price": "65001.0",
                    },
                    {
                        "time": "1700000000123",
                        "symbol": "BTCUSDT",
                        "side": "Buy",
                        "size": "0.1",
                        "price": "65000.0",
                    },
                ],
            },
        }
    )

    assert seen == [
        {
            "symbol": "BTCUSDT",
            "price": 65000.0,
            "qty": 0.1,
            "timestamp": 1700000000123,
            "is_buyer_maker": False,
        },
        {
            "symbol": "BTCUSDT",
            "price": 65001.0,
            "qty": 0.2,
            "timestamp": 1700000000124,
            "is_buyer_maker": True,
        },
    ]


@pytest.mark.parametrize(
    ("side", "is_buyer_maker"),
    [
        ("Buy", False),
        ("Sell", True),
    ],
)
def test_bybit_public_trade_maps_aggressor_side(side, is_buyer_maker):
    seen = []
    client = BybitMarketClient(
        "BTCUSDT",
        OrderBook(),
        TradeBuffer(),
        on_trade=seen.append,
    )

    client._process_public_trade_event(
        {
            "topic": "publicTrade.BTCUSDT",
            "data": [
                {
                    "T": 1700000000123,
                    "s": "BTCUSDT",
                    "S": side,
                    "v": "0.25",
                    "p": "65000.5",
                }
            ],
        }
    )

    assert seen == [
        {
            "symbol": "BTCUSDT",
            "price": 65000.5,
            "qty": 0.25,
            "timestamp": 1700000000123,
            "is_buyer_maker": is_buyer_maker,
        }
    ]
