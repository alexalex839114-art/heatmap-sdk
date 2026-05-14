import asyncio

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close

from app.binance_client import BinanceMarketClient
from app.order_book import OrderBook
from app.trade_buffer import TradeBuffer


def test_extract_tick_size_from_exchange_info_symbol_filters():
    payload = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                ],
            }
        ]
    }
    client = BinanceMarketClient("BTCUSDT", OrderBook(), TradeBuffer())

    assert client._extract_tick_size(payload) == 0.10


def test_apply_exchange_info_stores_tick_and_lot_size_filters():
    payload = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {
                        "filterType": "LOT_SIZE",
                        "minQty": "0.001",
                        "stepSize": "0.001",
                    },
                ],
            }
        ]
    }
    client = BinanceMarketClient("BTCUSDT", OrderBook(), TradeBuffer())

    client._apply_exchange_info(payload)

    assert client.tick_size == 0.10
    assert client.quantity_step == 0.001
    assert client.min_quantity == 0.001


def test_extract_tick_size_raises_when_price_filter_missing():
    payload = {"symbols": [{"symbol": "BTCUSDT", "filters": []}]}
    client = BinanceMarketClient("BTCUSDT", OrderBook(), TradeBuffer())

    with pytest.raises(RuntimeError):
        client._extract_tick_size(payload)


def test_stream_urls_use_new_public_and_market_endpoints():
    depth_url = BinanceMarketClient._build_depth_stream_url("BTCUSDT")
    trade_url = BinanceMarketClient._build_trade_stream_url("BTCUSDT")

    assert depth_url == "wss://fstream.binance.com/public/stream?streams=btcusdt@depth@100ms"
    assert trade_url == "wss://fstream.binance.com/market/stream?streams=btcusdt@aggTrade"


def test_trade_callback_receives_normalized_agg_trade():
    seen = []
    client = BinanceMarketClient(
        "BTCUSDT",
        OrderBook(),
        TradeBuffer(),
        on_trade=seen.append,
    )

    client._process_trade_event(
        {
            "e": "trade",
            "p": "65000.5",
            "q": "0.25",
            "T": 1700000000000,
            "m": True,
        }
    )

    assert seen == [
        {
            "symbol": "BTCUSDT",
            "price": 65000.5,
            "qty": 0.25,
            "timestamp": 1700000000000,
            "is_buyer_maker": True,
        }
    ]


def test_book_callback_runs_after_depth_event():
    seen = []
    book = OrderBook()
    book.load_snapshot(bids=[("100.0", "1.0")], asks=[("101.0", "1.0")])
    client = BinanceMarketClient(
        "BTCUSDT",
        book,
        TradeBuffer(),
        on_book_update=lambda book, event_time: seen.append(
            (book.best_bid(), book.best_ask(), event_time)
        ),
    )

    client._process_depth_event(
        {
            "U": 1,
            "u": 2,
            "b": [["100.5", "2.0"]],
            "a": [["101.0", "0"]],
            "E": 1700000000123,
        }
    )

    assert seen == [(100.5, None, 1700000000123)]


@pytest.mark.asyncio
async def test_stop_swallows_transport_close_errors_during_shutdown():
    client = BinanceMarketClient("BTCUSDT", OrderBook(), TradeBuffer())

    async def failing_stream():
        raise ConnectionClosedError(None, Close(1006, "abnormal shutdown"))

    client._stream_task = asyncio.create_task(failing_stream())
    await asyncio.sleep(0)

    await client.stop()


@pytest.mark.asyncio
async def test_stop_swallows_handshake_timeout_errors_during_shutdown():
    client = BinanceMarketClient("BTCUSDT", OrderBook(), TradeBuffer())

    async def failing_stream():
        raise TimeoutError("timed out during opening handshake")

    client._stream_task = asyncio.create_task(failing_stream())
    await asyncio.sleep(0)

    await client.stop()
