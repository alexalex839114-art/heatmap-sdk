import pytest

from app.exchange_symbols import to_okx_inst_id
from app.okx_client import OkxMarketClient
from app.order_book import OrderBook
from app.trade_buffer import TradeBuffer


def test_okx_subscribe_message_carries_books_and_trades():
    messages = OkxMarketClient._build_subscribe_messages("BTC-USDT")
    assert len(messages) == 1
    payload = messages[0]
    assert payload["op"] == "subscribe"
    channels = {arg["channel"] for arg in payload["args"]}
    assert channels == {"books", "trades"}
    for arg in payload["args"]:
        assert arg["instId"] == "BTC-USDT"


def test_okx_books_snapshot_loads_book_and_signals_sync():
    seen = []
    book = OrderBook()
    client = OkxMarketClient(
        "BTC-USDT",
        book,
        TradeBuffer(),
        on_book_update=lambda b, t: seen.append((b.best_bid(), b.best_ask(), t)),
    )

    client._process_stream_event(
        {
            "arg": {"channel": "books", "instId": "BTC-USDT"},
            "action": "snapshot",
            "data": [
                {
                    "bids": [["100.0", "1.0", "0", "1"]],
                    "asks": [["101.0", "2.0", "0", "1"]],
                    "ts": "1704067200000",
                }
            ],
        }
    )

    assert book.best_bid() == 100.0
    assert book.best_ask() == 101.0
    assert client._synced.is_set()
    assert seen and seen[0] == (100.0, 101.0, 1704067200000)


def test_okx_books_update_removes_level_when_size_zero():
    book = OrderBook()
    book.load_snapshot([("100.0", "1.0")], [("101.0", "2.0")])
    client = OkxMarketClient("BTC-USDT", book, TradeBuffer())

    client._process_stream_event(
        {
            "arg": {"channel": "books", "instId": "BTC-USDT"},
            "action": "update",
            "data": [
                {
                    "bids": [["100.0", "0", "0", "0"]],
                    "asks": [],
                    "ts": "1704067200000",
                }
            ],
        }
    )

    assert book.best_bid() is None
    assert book.best_ask() == 101.0


def test_okx_trades_treat_taker_sell_as_seller_aggressor():
    seen = []
    client = OkxMarketClient(
        "BTC-USDT",
        OrderBook(),
        TradeBuffer(),
        on_trade=seen.append,
    )

    client._process_stream_event(
        {
            "arg": {"channel": "trades", "instId": "BTC-USDT"},
            "data": [
                {
                    "instId": "BTC-USDT",
                    "tradeId": "1",
                    "px": "65000.0",
                    "sz": "0.1",
                    "side": "sell",
                    "ts": "1704067200500",
                },
                {
                    "instId": "BTC-USDT",
                    "tradeId": "2",
                    "px": "65001.0",
                    "sz": "0.2",
                    "side": "buy",
                    "ts": "1704067200500",
                },
            ],
        }
    )

    assert seen == [
        {
            "symbol": "BTC-USDT",
            "price": 65000.0,
            "qty": 0.1,
            "timestamp": 1704067200500,
            "is_buyer_maker": True,
        },
        {
            "symbol": "BTC-USDT",
            "price": 65001.0,
            "qty": 0.2,
            "timestamp": 1704067200500,
            "is_buyer_maker": False,
        },
    ]


def test_okx_error_event_raises_runtime_error():
    client = OkxMarketClient("BTC-USDT", OrderBook(), TradeBuffer())

    with pytest.raises(RuntimeError, match="OKX subscribe error"):
        client._process_stream_event(
            {
                "event": "error",
                "code": "60012",
                "msg": "Invalid request",
            }
        )


def test_okx_subscribe_ack_is_ignored():
    client = OkxMarketClient("BTC-USDT", OrderBook(), TradeBuffer())

    # Does not raise and does not flip the synced flag.
    client._process_stream_event(
        {
            "event": "subscribe",
            "arg": {"channel": "books", "instId": "BTC-USDT"},
        }
    )
    assert not client._synced.is_set()


def test_okx_trades_filter_out_other_instruments():
    seen = []
    client = OkxMarketClient(
        "BTC-USDT",
        OrderBook(),
        TradeBuffer(),
        on_trade=seen.append,
    )

    client._process_stream_event(
        {
            "arg": {"channel": "trades", "instId": "ETH-USDT"},
            "data": [
                {
                    "instId": "ETH-USDT",
                    "px": "3000",
                    "sz": "1",
                    "side": "buy",
                    "ts": "1",
                }
            ],
        }
    )

    assert seen == []


def test_to_okx_inst_id_preserves_quote():
    assert to_okx_inst_id("BTCUSDT") == "BTC-USDT"
    assert to_okx_inst_id("ETHUSDC") == "ETH-USDC"
    assert to_okx_inst_id("SOLUSD") == "SOL-USD"
    assert to_okx_inst_id("XYZ") is None
