import pytest

from app.exchange_symbols import to_okx_inst_id
from app.okx_client import OkxMarketClient, OkxUnsupportedInstrumentError
from app.order_book import OrderBook
from app.trade_buffer import TradeBuffer


def test_okx_subscribe_message_carries_books_and_trades():
    messages = OkxMarketClient._build_subscribe_messages("BTC-USDT-SWAP")
    assert len(messages) == 1
    payload = messages[0]
    assert payload["op"] == "subscribe"
    channels = {arg["channel"] for arg in payload["args"]}
    assert channels == {"books", "trades"}
    for arg in payload["args"]:
        assert arg["instId"] == "BTC-USDT-SWAP"


def test_okx_books_snapshot_loads_book_and_signals_sync():
    seen = []
    book = OrderBook()
    client = OkxMarketClient(
        "BTC-USDT-SWAP",
        book,
        TradeBuffer(),
        on_book_update=lambda b, t: seen.append((b.best_bid(), b.best_ask(), t)),
    )

    client._process_stream_event(
        {
            "arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
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
    client = OkxMarketClient("BTC-USDT-SWAP", book, TradeBuffer())

    client._process_stream_event(
        {
            "arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
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
        "BTC-USDT-SWAP",
        OrderBook(),
        TradeBuffer(),
        on_trade=seen.append,
    )

    client._process_stream_event(
        {
            "arg": {"channel": "trades", "instId": "BTC-USDT-SWAP"},
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "tradeId": "1",
                    "px": "65000.0",
                    "sz": "0.1",
                    "side": "sell",
                    "ts": "1704067200500",
                },
                {
                    "instId": "BTC-USDT-SWAP",
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
            "symbol": "BTC-USDT-SWAP",
            "price": 65000.0,
            "qty": 0.1,
            "timestamp": 1704067200500,
            "is_buyer_maker": True,
        },
        {
            "symbol": "BTC-USDT-SWAP",
            "price": 65001.0,
            "qty": 0.2,
            "timestamp": 1704067200500,
            "is_buyer_maker": False,
        },
    ]


def test_okx_error_event_raises_runtime_error():
    client = OkxMarketClient("BTC-USDT-SWAP", OrderBook(), TradeBuffer())

    with pytest.raises(RuntimeError, match="OKX subscribe error"):
        client._process_stream_event(
            {
                "event": "error",
                "code": "60012",
                "msg": "Invalid request",
            }
        )


def test_okx_unsupported_instrument_error_for_60018_code():
    """OKX returns code 60018 when the instId is not listed; surface that as
    a distinct exception so the caller can mark the indicator as unavailable
    rather than treating it as a retryable error."""
    client = OkxMarketClient("IRYS-USDT-SWAP", OrderBook(), TradeBuffer())

    with pytest.raises(OkxUnsupportedInstrumentError) as info:
        client._process_stream_event(
            {
                "event": "error",
                "code": "60018",
                "msg": (
                    "Wrong URL or channel:books,instId:IRYS-USDT-SWAP doesn't "
                    "exist. Please use the correct URL, channel and parameters "
                    "referring to API document."
                ),
            }
        )
    assert "IRYS-USDT-SWAP" in str(info.value)


def test_okx_unsupported_instrument_error_for_message_marker():
    """Defence in depth: even if OKX changes the numeric code, the human
    message has historically contained ``doesn't exist`` for missing instIds;
    pattern-match on that so we still detect it."""
    client = OkxMarketClient("FAKE-USDT-SWAP", OrderBook(), TradeBuffer())

    with pytest.raises(OkxUnsupportedInstrumentError):
        client._process_stream_event(
            {
                "event": "error",
                "code": "99999",
                "msg": "Channel books, instId FAKE-USDT-SWAP doesn't exist",
            }
        )


def test_okx_unsupported_error_subclass_of_runtime_error():
    """``OkxUnsupportedInstrumentError`` is a ``RuntimeError`` so existing
    ``except Exception`` / ``except RuntimeError`` callers still catch it,
    but ``except OkxUnsupportedInstrumentError`` callers can distinguish it
    from generic subscribe failures."""
    assert issubclass(OkxUnsupportedInstrumentError, RuntimeError)


def test_okx_subscribe_ack_is_ignored():
    client = OkxMarketClient("BTC-USDT-SWAP", OrderBook(), TradeBuffer())

    # Does not raise and does not flip the synced flag.
    client._process_stream_event(
        {
            "event": "subscribe",
            "arg": {"channel": "books", "instId": "BTC-USDT-SWAP"},
        }
    )
    assert not client._synced.is_set()


def test_okx_trades_filter_out_other_instruments():
    seen = []
    client = OkxMarketClient(
        "BTC-USDT-SWAP",
        OrderBook(),
        TradeBuffer(),
        on_trade=seen.append,
    )

    client._process_stream_event(
        {
            "arg": {"channel": "trades", "instId": "ETH-USDT-SWAP"},
            "data": [
                {
                    "instId": "ETH-USDT-SWAP",
                    "px": "3000",
                    "sz": "1",
                    "side": "buy",
                    "ts": "1",
                }
            ],
        }
    )

    assert seen == []


def test_to_okx_inst_id_maps_to_perpetual_swap():
    """to_okx_inst_id maps Binance USDⓈ-M futures symbols to the matching OKX
    perpetual swap instrument id (USDT-margined linear perp), not spot."""
    assert to_okx_inst_id("BTCUSDT") == "BTC-USDT-SWAP"
    assert to_okx_inst_id("ETHUSDT") == "ETH-USDT-SWAP"
    # USDC-quoted Binance perps map to OKX USDC perps where listed.
    assert to_okx_inst_id("BTCUSDC") == "BTC-USDC-SWAP"
    # USD-quoted symbols on Binance futures don't exist today, but map
    # cleanly to the closest OKX USDT perp.
    assert to_okx_inst_id("SOLUSD") == "SOL-USDT-SWAP"
    assert to_okx_inst_id("XYZ") is None
