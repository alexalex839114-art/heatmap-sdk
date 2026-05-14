from dataclasses import dataclass

from adaptive_sdk.types import ExhaustionType, MetricsSnapshot, Signal
from app.entry_filter import AdaptiveVpinRegime, EntryFilterEngine


@dataclass(slots=True)
class DummyState:
    is_ready: bool
    vpin: float
    sell_exhaustion_z: float = 0.0
    buy_exhaustion_z: float = 0.0
    pending_signals_count: int = 0
    buckets_filled: int = 0


def _signal(exhaustion_type: ExhaustionType, confidence: float = 0.8) -> Signal:
    return Signal(
        signal_id="sig",
        symbol="BTCUSDT",
        timestamp=1.0,
        exhaustion_type=exhaustion_type,
        confidence=confidence,
        arm_id=1,
        metrics=MetricsSnapshot(
            vpin=0.2,
            z_score_buy_flow=2.0,
            z_score_sell_flow=1.0,
            obi=0.0,
            bucket_size=10.0,
            buckets_filled=50,
        ),
    )


def test_entry_filter_warming_until_sdk_ready():
    result = EntryFilterEngine().evaluate(
        DummyState(False, 0.0, buckets_filled=12),
        None,
        trade_count=80,
    )

    assert result.market_state == "WARMING"
    assert result.long_filter == "WAIT"
    assert result.short_filter == "WAIT"
    assert result.reason == "warming 12/50 buckets, 80/200 trades"


def test_entry_filter_blocks_toxic_market():
    result = EntryFilterEngine(vpin_high=0.92).evaluate(DummyState(True, 0.93), None)

    assert result.market_state == "TOXIC"
    assert result.long_filter == "BLOCKED"
    assert result.short_filter == "BLOCKED"
    assert result.reason == "toxic_vpin"


def test_entry_filter_can_warn_instead_of_block_when_vpin_gate_disabled():
    result = EntryFilterEngine(
        vpin_high=0.92,
        vpin_warn=0.70,
        block_on_toxic_vpin=False,
    ).evaluate(DummyState(True, 1.0), None)

    assert result.market_state == "TOXIC"
    assert result.long_filter == "WAIT"
    assert result.short_filter == "WAIT"
    assert result.reason == "toxic_vpin_watch_only"


def test_entry_filter_warns_but_does_not_block_mid_toxicity():
    result = EntryFilterEngine(vpin_high=0.92, vpin_warn=0.70).evaluate(
        DummyState(True, 0.80),
        None,
    )

    assert result.market_state == "RISKY"
    assert result.long_filter == "WAIT"
    assert result.short_filter == "WAIT"
    assert result.reason == "elevated_vpin"


def test_adaptive_vpin_regime_uses_only_one_minute_window():
    regime = AdaptiveVpinRegime(
        window_ms=60_000,
        min_samples=3,
        risky_quantile=0.5,
        toxic_quantile=0.8,
    )
    regime.observe(0.95, now_ms=1_000)
    regime.observe(0.20, now_ms=62_000)
    regime.observe(0.25, now_ms=63_000)
    regime.observe(0.30, now_ms=64_000)

    state = regime.classify(0.35, now_ms=65_000)

    assert state.market_state == "TOXIC"
    assert state.toxic_threshold < 0.95


def test_entry_filter_uses_adaptive_vpin_regime():
    regime = AdaptiveVpinRegime(
        window_ms=60_000,
        min_samples=3,
        risky_quantile=0.5,
        toxic_quantile=0.8,
    )
    regime.observe(0.20, now_ms=1_000)
    regime.observe(0.25, now_ms=2_000)
    regime.observe(0.30, now_ms=3_000)

    result = EntryFilterEngine(
        block_on_toxic_vpin=False,
        vpin_regime=regime,
    ).evaluate(DummyState(True, 0.35), None, now_ms=4_000)

    assert result.market_state == "TOXIC"
    assert result.long_filter == "WAIT"
    assert result.short_filter == "WAIT"
    assert result.reason == "adaptive_toxic_vpin_watch_only"


def test_buy_exhaustion_allows_short_consideration():
    result = EntryFilterEngine().evaluate(
        DummyState(True, 0.2),
        _signal(ExhaustionType.BUY_EXHAUSTION),
    )

    assert result.market_state == "READY"
    assert result.long_filter == "WAIT"
    assert result.short_filter == "OK"
    assert result.latest_signal_type == "BUY_EXHAUSTION"


def test_sell_exhaustion_allows_long_consideration():
    result = EntryFilterEngine().evaluate(
        DummyState(True, 0.2),
        _signal(ExhaustionType.SELL_EXHAUSTION),
    )

    assert result.market_state == "READY"
    assert result.long_filter == "OK"
    assert result.short_filter == "WAIT"
    assert result.latest_signal_type == "SELL_EXHAUSTION"


def test_ready_without_signal_waits():
    result = EntryFilterEngine().evaluate(DummyState(True, 0.2), None)

    assert result.market_state == "READY"
    assert result.long_filter == "WAIT"
    assert result.short_filter == "WAIT"
    assert result.reason == "no_signal"
