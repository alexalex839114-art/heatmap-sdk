from app.coinbase_client import CoinbaseMarketClient
from app.order_book import OrderBook
from app.trade_buffer import TradeBuffer


def test_coinbase_subscribe_messages_include_heartbeats_level2_trades():
    messages = CoinbaseMarketClient._build_subscribe_messages("BTC-USD")

    channels = [msg["channel"] for msg in messages]
    assert channels == ["heartbeats", "level2", "market_trades"]
    assert messages[1]["product_ids"] == ["BTC-USD"]
    assert messages[2]["product_ids"] == ["BTC-USD"]


def test_coinbase_level2_snapshot_loads_book_and_signals_sync():
    seen = []
    book = OrderBook()
    client = CoinbaseMarketClient(
        "BTC-USD",
        book,
        TradeBuffer(),
        on_book_update=lambda b, t: seen.append((b.best_bid(), b.best_ask(), t)),
    )

    client._process_stream_event(
        {
            "channel": "l2_data",
            "timestamp": "2024-01-01T00:00:00.000Z",
            "events": [
                {
                    "type": "snapshot",
                    "product_id": "BTC-USD",
                    "updates": [
                        {"side": "bid", "price_level": "100.0", "new_quantity": "1.0"},
                        {"side": "offer", "price_level": "101.0", "new_quantity": "2.0"},
                    ],
                }
            ],
        }
    )

    assert book.best_bid() == 100.0
    assert book.best_ask() == 101.0
    assert client._synced.is_set()
    assert seen and seen[0][:2] == (100.0, 101.0)


def test_coinbase_level2_update_removes_level_when_quantity_zero():
    book = OrderBook()
    book.load_snapshot([("100.0", "1.0")], [("101.0", "2.0")])
    client = CoinbaseMarketClient("BTC-USD", book, TradeBuffer())

    client._process_stream_event(
        {
            "channel": "l2_data",
            "events": [
                {
                    "type": "update",
                    "product_id": "BTC-USD",
                    "updates": [
                        {"side": "bid", "price_level": "100.0", "new_quantity": "0"},
                    ],
                }
            ],
        }
    )

    assert book.best_bid() is None


def test_coinbase_market_trades_treat_maker_buy_as_seller_aggressor():
    seen = []
    client = CoinbaseMarketClient(
        "BTC-USD",
        OrderBook(),
        TradeBuffer(),
        on_trade=seen.append,
    )

    client._process_stream_event(
        {
            "channel": "market_trades",
            "events": [
                {
                    "type": "update",
                    "trades": [
                        {
                            "trade_id": "1",
                            "product_id": "BTC-USD",
                            "price": "65000.0",
                            "size": "0.1",
                            "side": "BUY",
                            "time": "2024-01-01T00:00:00.500Z",
                        },
                        {
                            "trade_id": "2",
                            "product_id": "BTC-USD",
                            "price": "65001.0",
                            "size": "0.2",
                            "side": "SELL",
                            "time": "2024-01-01T00:00:00.500Z",
                        },
                    ],
                }
            ],
        }
    )

    assert seen == [
        {
            "symbol": "BTC-USD",
            "price": 65000.0,
            "qty": 0.1,
            "timestamp": 1704067200500,
            "is_buyer_maker": True,
        },
        {
            "symbol": "BTC-USD",
            "price": 65001.0,
            "qty": 0.2,
            "timestamp": 1704067200500,
            "is_buyer_maker": False,
        },
    ]


def test_coinbase_error_message_raises_runtime_error():
    import pytest

    client = CoinbaseMarketClient("TRUTH-USD", OrderBook(), TradeBuffer())

    with pytest.raises(RuntimeError, match="Coinbase subscribe error"):
        client._process_stream_event(
            {
                "type": "error",
                "message": "Unknown product TRUTH-USD",
            }
        )


def test_coinbase_empty_subscriptions_ack_raises():
    import pytest

    client = CoinbaseMarketClient("TRUTH-USD", OrderBook(), TradeBuffer())

    with pytest.raises(RuntimeError, match="rejected subscription"):
        client._process_stream_event(
            {
                "channel": "subscriptions",
                "events": [{"subscriptions": {}}],
            }
        )


def test_coinbase_valid_subscriptions_ack_is_ignored():
    client = CoinbaseMarketClient("BTC-USD", OrderBook(), TradeBuffer())

    # Does not raise.
    client._process_stream_event(
        {
            "channel": "subscriptions",
            "events": [{"subscriptions": {"level2": ["BTC-USD"]}}],
        }
    )
