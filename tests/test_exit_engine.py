from adaptive_sdk.types import ExhaustionType, MetricsSnapshot, Signal

from app.assistant_config import AssistantRiskSettings
from app.exit_engine import ExitEngine
from app.position import PositionState


class DummySdkState:
    def __init__(self, vpin: float) -> None:
        self.vpin = vpin


def _signal(exhaustion_type: ExhaustionType, confidence: float = 0.9) -> Signal:
    return Signal(
        signal_id="sig",
        symbol="BTCUSDT",
        timestamp=1.0,
        exhaustion_type=exhaustion_type,
        confidence=confidence,
        arm_id=1,
        metrics=MetricsSnapshot(
            vpin=0.2,
            z_score_buy_flow=1.0,
            z_score_sell_flow=1.0,
            obi=0.0,
            bucket_size=10.0,
            buckets_filled=50,
        ),
    )


def test_flat_position_does_not_close():
    engine = ExitEngine()
    pos = PositionState(symbol="BTCUSDT", amount=0.0, entry_price=0.0)

    result = engine.evaluate(
        position=pos,
        sdk_state=None,
        latest_signal=None,
        settings=AssistantRiskSettings(auto_exit_enabled=True),
        now_ms=1000,
    )

    assert result.state == "NO_POSITION"
    assert result.should_close is False


def test_max_loss_triggers_immediate_exit():
    engine = ExitEngine()
    pos = PositionState(symbol="BTCUSDT", amount=0.01, entry_price=65000, unrealized_pnl=-6.0)

    result = engine.evaluate(
        position=pos,
        sdk_state=None,
        latest_signal=None,
        settings=AssistantRiskSettings(auto_exit_enabled=True, max_loss_usdt=5.0),
        now_ms=1000,
    )

    assert result.should_close is True
    assert result.reason == "max_loss"
    assert result.state == "EXIT_ARMED"


def test_auto_exit_off_blocks_close_but_reports_reason():
    engine = ExitEngine()
    pos = PositionState(symbol="BTCUSDT", amount=0.01, entry_price=65000, unrealized_pnl=-6.0)

    result = engine.evaluate(
        position=pos,
        sdk_state=None,
        latest_signal=None,
        settings=AssistantRiskSettings(auto_exit_enabled=False, max_loss_usdt=5.0),
        now_ms=1000,
    )

    assert result.should_close is False
    assert result.reason == "max_loss"
    assert result.state == "EXIT_ARMED"


def test_toxic_vpin_triggers_exit_when_enabled():
    engine = ExitEngine(vpin_high=0.5)
    pos = PositionState(symbol="BTCUSDT", amount=0.01, entry_price=65000)

    result = engine.evaluate(
        position=pos,
        sdk_state=DummySdkState(vpin=0.6),
        latest_signal=None,
        settings=AssistantRiskSettings(auto_exit_enabled=True, toxic_vpin_exit_enabled=True),
        now_ms=1000,
    )

    assert result.should_close is True
    assert result.reason == "toxic_vpin"


def test_rv_stop_loss_closes_long():
    engine = ExitEngine()
    pos = PositionState(
        symbol="BTCUSDT",
        amount=0.1,
        entry_price=100.0,
        unrealized_pnl=-1.1,
        rv_stop_price=99.0,
        rv_take_price=101.5,
    )

    result = engine.evaluate(
        position=pos,
        sdk_state=None,
        latest_signal=None,
        settings=AssistantRiskSettings(auto_exit_enabled=True),
        now_ms=1000,
    )

    assert result.should_close is True
    assert result.reason == "rv_stop_loss"


def test_rv_take_profit_closes_short():
    engine = ExitEngine()
    pos = PositionState(
        symbol="BTCUSDT",
        amount=-0.1,
        entry_price=100.0,
        unrealized_pnl=1.1,
        rv_stop_price=101.0,
        rv_take_price=99.0,
    )

    result = engine.evaluate(
        position=pos,
        sdk_state=None,
        latest_signal=None,
        settings=AssistantRiskSettings(auto_exit_enabled=True),
        now_ms=1000,
    )

    assert result.should_close is True
    assert result.reason == "rv_take_profit"


def test_opposite_signal_closes_long_on_buy_exhaustion():
    engine = ExitEngine(high_confidence=0.75)
    pos = PositionState(symbol="BTCUSDT", amount=0.01, entry_price=65000)

    result = engine.evaluate(
        position=pos,
        sdk_state=None,
        latest_signal=_signal(ExhaustionType.BUY_EXHAUSTION, confidence=0.9),
        settings=AssistantRiskSettings(auto_exit_enabled=True),
        now_ms=1000,
    )

    assert result.should_close is True
    assert result.reason == "opposite_signal_high_confidence"


def test_opposite_signal_closes_short_on_sell_exhaustion():
    engine = ExitEngine(high_confidence=0.75)
    pos = PositionState(symbol="BTCUSDT", amount=-0.01, entry_price=65000)

    result = engine.evaluate(
        position=pos,
        sdk_state=None,
        latest_signal=_signal(ExhaustionType.SELL_EXHAUSTION, confidence=0.9),
        settings=AssistantRiskSettings(auto_exit_enabled=True),
        now_ms=1000,
    )

    assert result.should_close is True
    assert result.reason == "opposite_signal_high_confidence"


def test_max_holding_time_requires_confirmation():
    engine = ExitEngine()
    pos = PositionState(
        symbol="BTCUSDT",
        amount=0.01,
        entry_price=65000,
        opened_at_ms=1_000,
    )
    settings = AssistantRiskSettings(
        auto_exit_enabled=True,
        max_holding_time_sec=1.0,
        confirmation_ms=500,
    )

    first = engine.evaluate(
        position=pos,
        sdk_state=None,
        latest_signal=None,
        settings=settings,
        now_ms=2_100,
    )
    second = engine.evaluate(
        position=pos,
        sdk_state=None,
        latest_signal=None,
        settings=settings,
        now_ms=2_700,
    )

    assert first.state == "WARNING"
    assert first.should_close is False
    assert second.state == "EXIT_ARMED"
    assert second.should_close is True
    assert second.reason == "max_holding_time"
