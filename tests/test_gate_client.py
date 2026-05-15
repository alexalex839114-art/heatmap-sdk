import pytest

from app.exchange_symbols import to_gate_contract
from app.gate_client import GateMarketClient, GateUnsupportedContractError
from app.order_book import OrderBook
from app.trade_buffer import TradeBuffer


def test_gate_subscribe_messages_cover_orderbook_and_trades():
    messages = GateMarketClient._build_subscribe_messages(
        "BTC_USDT", level="20", interval="0", now_unix=1700000000
    )
    assert len(messages) == 2
    channels = [m["channel"] for m in messages]
    assert "futures.order_book" in channels
    assert "futures.trades" in channels
    book_msg = next(m for m in messages if m["channel"] == "futures.order_book")
    assert book_msg["event"] == "subscribe"
    assert book_msg["payload"] == ["BTC_USDT", "20", "0"]
    assert book_msg["time"] == 1700000000
    trade_msg = next(m for m in messages if m["channel"] == "futures.trades")
    assert trade_msg["event"] == "subscribe"
    assert trade_msg["payload"] == ["BTC_USDT"]
    assert trade_msg["time"] == 1700000000


def test_gate_order_book_all_snapshot_loads_book_and_signals_sync():
    seen = []
    book = OrderBook()
    client = GateMarketClient(
        "BTC_USDT",
        book,
        TradeBuffer(),
        on_book_update=lambda b, t: seen.append((b.best_bid(), b.best_ask(), t)),
    )

    client._process_stream_event(
        {
            "time": 1704067200,
            "time_ms": 1704067200500,
            "channel": "futures.order_book",
            "event": "all",
            "result": {
                "t": 1704067200500,
                "id": 1,
                "contract": "BTC_USDT",
                "bids": [{"p": "100.0", "s": 1}],
                "asks": [{"p": "101.0", "s": 2}],
            },
        }
    )

    assert book.best_bid() == 100.0
    assert book.best_ask() == 101.0
    assert client._synced.is_set()
    assert seen and seen[0] == (100.0, 101.0, 1704067200500)


def test_gate_order_book_all_snapshot_replaces_existing_levels():
    """Every futures.order_book "all" message is a full snapshot, not an
    incremental delta — old levels not present in the new snapshot must be
    removed."""
    book = OrderBook()
    book.load_snapshot([("99.0", "5")], [("102.0", "5")])
    client = GateMarketClient("BTC_USDT", book, TradeBuffer())

    client._process_stream_event(
        {
            "channel": "futures.order_book",
            "event": "all",
            "result": {
                "t": 1704067200500,
                "id": 2,
                "contract": "BTC_USDT",
                "bids": [{"p": "100.0", "s": 1}],
                "asks": [{"p": "101.0", "s": 2}],
            },
        }
    )

    assert book.best_bid() == 100.0
    assert book.best_ask() == 101.0


def test_gate_trades_signed_size_taker_sell_is_buyer_maker():
    seen = []
    client = GateMarketClient(
        "BTC_USDT",
        OrderBook(),
        TradeBuffer(),
        on_trade=seen.append,
    )

    client._process_stream_event(
        {
            "channel": "futures.trades",
            "event": "update",
            "result": [
                {
                    "id": 1,
                    "contract": "BTC_USDT",
                    "price": "65000.0",
                    "size": -0.1,
                    "create_time_ms": 1704067200500,
                },
                {
                    "id": 2,
                    "contract": "BTC_USDT",
                    "price": "65001.0",
                    "size": 0.2,
                    "create_time_ms": 1704067200500,
                },
            ],
        }
    )

    assert seen == [
        {
            "symbol": "BTC_USDT",
            "price": 65000.0,
            "qty": 0.1,
            "timestamp": 1704067200500,
            "is_buyer_maker": True,
        },
        {
            "symbol": "BTC_USDT",
            "price": 65001.0,
            "qty": 0.2,
            "timestamp": 1704067200500,
            "is_buyer_maker": False,
        },
    ]


def test_gate_trades_with_create_time_seconds_only_converts_to_ms():
    seen = []
    client = GateMarketClient(
        "BTC_USDT",
        OrderBook(),
        TradeBuffer(),
        on_trade=seen.append,
    )

    client._process_stream_event(
        {
            "channel": "futures.trades",
            "event": "update",
            "result": [
                {
                    "id": 1,
                    "contract": "BTC_USDT",
                    "price": "65000.0",
                    "size": 1.0,
                    "create_time": 1704067200,
                },
            ],
        }
    )

    assert seen and seen[0]["timestamp"] == 1704067200 * 1000


def test_gate_trades_drop_zero_size():
    seen = []
    client = GateMarketClient(
        "BTC_USDT",
        OrderBook(),
        TradeBuffer(),
        on_trade=seen.append,
    )

    client._process_stream_event(
        {
            "channel": "futures.trades",
            "event": "update",
            "result": [
                {
                    "id": 1,
                    "contract": "BTC_USDT",
                    "price": "65000.0",
                    "size": 0.0,
                    "create_time_ms": 1704067200500,
                },
            ],
        }
    )

    assert seen == []


def test_gate_subscribe_ack_success_does_not_raise():
    client = GateMarketClient("BTC_USDT", OrderBook(), TradeBuffer())
    # An empty result without status is treated as success/ack.
    client._process_stream_event(
        {
            "channel": "futures.order_book",
            "event": "subscribe",
            "result": {"status": "success"},
        }
    )


def test_gate_subscribe_fail_with_unknown_currency_pair_raises_unsupported():
    """Gate.io rejects an unknown contract with
    ``error={"code": 2, "message": "unknown currency pair FOO_USDT"}`` and
    ``result.status="fail"``. We surface this as
    ``GateUnsupportedContractError`` so the caller can mark the indicator as
    unavailable instead of crashing the session."""
    client = GateMarketClient("FAKECOIN_USDT", OrderBook(), TradeBuffer())

    with pytest.raises(GateUnsupportedContractError) as info:
        client._process_stream_event(
            {
                "channel": "futures.order_book",
                "event": "subscribe",
                "payload": ["FAKECOIN_USDT", "20", "0"],
                "error": {
                    "code": 2,
                    "message": "unknown currency pair FAKECOIN_USDT",
                },
                "result": {"status": "fail"},
            }
        )
    assert "FAKECOIN_USDT" in str(info.value)


def test_gate_subscribe_fail_without_unsupported_marker_raises_runtime_error():
    """Generic subscribe failures (e.g. malformed payload) must NOT be
    classified as unsupported-contract; the caller should treat them as
    hard errors."""
    client = GateMarketClient("BTC_USDT", OrderBook(), TradeBuffer())

    with pytest.raises(RuntimeError, match="Gate subscribe error"):
        client._process_stream_event(
            {
                "channel": "futures.trades",
                "event": "subscribe",
                "error": {"code": 999, "message": "internal failure"},
                "result": {"status": "fail"},
            }
        )


def test_to_gate_contract_maps_binance_symbol_to_usdt_perp():
    assert to_gate_contract("BTCUSDT") == "BTC_USDT"
    assert to_gate_contract("ETHUSDT") == "ETH_USDT"
    assert to_gate_contract("IRYSUSDT") == "IRYS_USDT"


def test_to_gate_contract_passes_usdc_through_with_underscore():
    assert to_gate_contract("BTCUSDC") == "BTC_USDC"


def test_to_gate_contract_returns_none_for_invalid_symbol():
    assert to_gate_contract("") is None
    assert to_gate_contract("BTC") is None
