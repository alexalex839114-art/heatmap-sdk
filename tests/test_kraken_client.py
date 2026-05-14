from app.kraken_client import KrakenMarketClient
from app.order_book import OrderBook
from app.trade_buffer import TradeBuffer


def test_kraken_subscribe_messages_cover_book_and_trade():
    messages = KrakenMarketClient._build_subscribe_messages("XBT/USD", depth=25)

    channels = [msg["params"]["channel"] for msg in messages]
    assert channels == ["book", "trade"]
    assert messages[0]["params"]["symbol"] == ["XBT/USD"]
    assert messages[0]["params"]["depth"] == 25


def test_kraken_book_snapshot_loads_book_and_signals_sync():
    seen = []
    book = OrderBook()
    client = KrakenMarketClient(
        "XBT/USD",
        book,
        TradeBuffer(),
        on_book_update=lambda b, t: seen.append((b.best_bid(), b.best_ask(), t)),
    )

    client._process_stream_event(
        {
            "channel": "book",
            "type": "snapshot",
            "data": [
                {
                    "symbol": "XBT/USD",
                    "timestamp": "2024-01-01T00:00:00.000Z",
                    "bids": [{"price": 100.0, "qty": 1.0}],
                    "asks": [{"price": 101.0, "qty": 2.0}],
                    "checksum": 0,
                }
            ],
        }
    )

    assert book.best_bid() == 100.0
    assert book.best_ask() == 101.0
    assert client._synced.is_set()
    assert seen and seen[0][:2] == (100.0, 101.0)


def test_kraken_book_update_removes_level_when_quantity_zero():
    book = OrderBook()
    book.load_snapshot([("100.0", "1.0")], [("101.0", "2.0")])
    client = KrakenMarketClient("XBT/USD", book, TradeBuffer())

    client._process_stream_event(
        {
            "channel": "book",
            "type": "update",
            "data": [
                {
                    "symbol": "XBT/USD",
                    "bids": [{"price": 100.0, "qty": 0.0}],
                    "asks": [],
                }
            ],
        }
    )

    assert book.best_bid() is None


def test_kraken_trade_side_maps_taker_side_to_is_buyer_maker():
    seen = []
    client = KrakenMarketClient(
        "XBT/USD",
        OrderBook(),
        TradeBuffer(),
        on_trade=seen.append,
    )

    client._process_stream_event(
        {
            "channel": "trade",
            "type": "update",
            "data": [
                {
                    "symbol": "XBT/USD",
                    "side": "sell",
                    "price": 65001.0,
                    "qty": 0.2,
                    "ord_type": "market",
                    "trade_id": 1,
                    "timestamp": "2024-01-01T00:00:00.500Z",
                },
                {
                    "symbol": "XBT/USD",
                    "side": "buy",
                    "price": 65000.0,
                    "qty": 0.1,
                    "ord_type": "market",
                    "trade_id": 2,
                    "timestamp": "2024-01-01T00:00:00.500Z",
                },
            ],
        }
    )

    assert seen == [
        {
            "symbol": "XBT/USD",
            "price": 65001.0,
            "qty": 0.2,
            "timestamp": 1704067200500,
            "is_buyer_maker": True,
        },
        {
            "symbol": "XBT/USD",
            "price": 65000.0,
            "qty": 0.1,
            "timestamp": 1704067200500,
            "is_buyer_maker": False,
        },
    ]


def test_kraken_subscribe_failure_raises_runtime_error():
    import pytest

    client = KrakenMarketClient("TRUTH/USD", OrderBook(), TradeBuffer())

    with pytest.raises(RuntimeError, match="Kraken subscribe error"):
        client._process_stream_event(
            {
                "method": "subscribe",
                "success": False,
                "error": "Currency pair not supported TRUTH/USD",
            }
        )
