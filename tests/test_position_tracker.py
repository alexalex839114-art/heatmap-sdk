from app.position import PositionState
from app.position_tracker import PositionTracker


def test_tracker_preserves_open_time_while_position_remains_open():
    tracker = PositionTracker("BTCUSDT")

    first = tracker.update(
        PositionState(symbol="BTCUSDT", amount=0.01, entry_price=65000),
        now_ms=1000,
    )
    second = tracker.update(
        PositionState(symbol="BTCUSDT", amount=0.02, entry_price=65010),
        now_ms=2000,
    )

    assert first.opened_at_ms == 1000
    assert second.opened_at_ms == 1000
    assert second.quantity == 0.02


def test_tracker_captures_rv_stop_and_take_when_position_opens():
    tracker = PositionTracker("BTCUSDT")

    current = tracker.update(
        PositionState(symbol="BTCUSDT", amount=0.1, entry_price=100.0),
        now_ms=1000,
        realized_vol=0.01,
        stop_rv_multiplier=1.0,
        take_rv_multiplier=1.5,
    )

    assert current.realized_vol_at_entry == 0.01
    assert current.rv_stop_price == 99.0
    assert current.rv_take_price == 101.5


def test_tracker_preserves_rv_levels_while_position_remains_open():
    tracker = PositionTracker("BTCUSDT")

    tracker.update(
        PositionState(symbol="BTCUSDT", amount=-0.1, entry_price=100.0),
        now_ms=1000,
        realized_vol=0.01,
        stop_rv_multiplier=1.0,
        take_rv_multiplier=2.0,
    )
    current = tracker.update(
        PositionState(symbol="BTCUSDT", amount=-0.1, entry_price=100.0),
        now_ms=2000,
        realized_vol=0.03,
        stop_rv_multiplier=3.0,
        take_rv_multiplier=4.0,
    )

    assert current.realized_vol_at_entry == 0.01
    assert current.rv_stop_price == 101.0
    assert current.rv_take_price == 98.0


def test_tracker_resets_when_position_goes_flat():
    tracker = PositionTracker("BTCUSDT")

    tracker.update(PositionState(symbol="BTCUSDT", amount=0.01, entry_price=65000), now_ms=1000)
    flat = tracker.update(PositionState(symbol="BTCUSDT", amount=0.0, entry_price=0.0), now_ms=2000)

    assert flat.side == "FLAT"
    assert flat.opened_at_ms is None
    assert tracker.current is None


def test_tracker_ignores_other_symbols():
    tracker = PositionTracker("BTCUSDT")

    result = tracker.update(
        PositionState(symbol="ETHUSDT", amount=1.0, entry_price=3000),
        now_ms=1000,
    )

    assert result is None
    assert tracker.current is None
