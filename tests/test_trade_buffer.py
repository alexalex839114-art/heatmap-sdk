from app.trade_buffer import TradeBuffer


def test_trade_buffer_drains_only_current_items():
    buf = TradeBuffer()

    buf.add({"price": 100.0, "qty": 1.0})

    assert buf.drain() == [{"price": 100.0, "qty": 1.0}]
    assert buf.drain() == []

