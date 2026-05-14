from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from adaptive_sdk.types import ExhaustionType, Signal


@dataclass(slots=True, frozen=True)
class EntryFilterResult:
    market_state: str
    long_filter: str
    short_filter: str
    reason: str
    latest_signal_type: str | None = None
    latest_signal_confidence: float | None = None
    vpin: float = 0.0


@dataclass(slots=True, frozen=True)
class VpinRegimeState:
    market_state: str
    risky_threshold: float
    toxic_threshold: float


class AdaptiveVpinRegime:
    def __init__(
        self,
        *,
        window_ms: int = 60_000,
        min_samples: int = 20,
        risky_quantile: float = 0.70,
        toxic_quantile: float = 0.90,
        fallback_warn: float = 0.70,
        fallback_high: float = 0.92,
    ) -> None:
        self.window_ms = int(window_ms)
        self.min_samples = int(min_samples)
        self.risky_quantile = float(risky_quantile)
        self.toxic_quantile = float(toxic_quantile)
        self.fallback_warn = float(fallback_warn)
        self.fallback_high = float(fallback_high)
        self._samples: deque[tuple[int, float]] = deque()

    def observe(self, vpin: float, *, now_ms: int) -> None:
        self._evict(now_ms)
        self._samples.append((int(now_ms), float(vpin)))

    def classify(self, vpin: float, *, now_ms: int) -> VpinRegimeState:
        self._evict(now_ms)
        if len(self._samples) < self.min_samples:
            risky = self.fallback_warn
            toxic = self.fallback_high
        else:
            values = sorted(value for _, value in self._samples)
            risky = _quantile(values, self.risky_quantile)
            toxic = max(risky, _quantile(values, self.toxic_quantile))

        if vpin >= toxic:
            market_state = "TOXIC"
        elif vpin >= risky:
            market_state = "RISKY"
        else:
            market_state = "NORMAL"
        self._samples.append((int(now_ms), float(vpin)))
        return VpinRegimeState(
            market_state=market_state,
            risky_threshold=risky,
            toxic_threshold=toxic,
        )

    def _evict(self, now_ms: int) -> None:
        cutoff = int(now_ms) - self.window_ms
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    bounded_q = max(0.0, min(1.0, q))
    pos = bounded_q * (len(values) - 1)
    lower = int(pos)
    upper = min(lower + 1, len(values) - 1)
    weight = pos - lower
    return values[lower] * (1.0 - weight) + values[upper] * weight


class EntryFilterEngine:
    def __init__(
        self,
        vpin_high: float = 0.92,
        vpin_warn: float = 0.70,
        block_on_toxic_vpin: bool = True,
        vpin_regime: AdaptiveVpinRegime | None = None,
    ) -> None:
        self.vpin_high = vpin_high
        self.vpin_warn = vpin_warn
        self.block_on_toxic_vpin = block_on_toxic_vpin
        self.vpin_regime = vpin_regime

    def evaluate(
        self,
        sdk_state: Any,
        latest_signal: Signal | None,
        *,
        trade_count: int | None = None,
        min_buckets_for_vpin: int = 50,
        min_ticks_for_z: int = 200,
        now_ms: int | None = None,
    ) -> EntryFilterResult:
        vpin = float(getattr(sdk_state, "vpin", 0.0))
        if not bool(getattr(sdk_state, "is_ready", False)):
            buckets = int(getattr(sdk_state, "buckets_filled", 0))
            trades = int(trade_count or 0)
            return EntryFilterResult(
                market_state="WARMING",
                long_filter="WAIT",
                short_filter="WAIT",
                reason=(
                    f"warming {buckets}/{min_buckets_for_vpin} buckets, "
                    f"{trades}/{min_ticks_for_z} trades"
                ),
                vpin=vpin,
            )

        if self.vpin_regime is not None:
            regime = self.vpin_regime.classify(
                vpin,
                now_ms=now_ms if now_ms is not None else int(time.time() * 1000),
            )
            if regime.market_state == "TOXIC":
                if not self.block_on_toxic_vpin:
                    return EntryFilterResult(
                        market_state="TOXIC",
                        long_filter="WAIT",
                        short_filter="WAIT",
                        reason="adaptive_toxic_vpin_watch_only",
                        vpin=vpin,
                    )
                return EntryFilterResult(
                    market_state="TOXIC",
                    long_filter="BLOCKED",
                    short_filter="BLOCKED",
                    reason="adaptive_toxic_vpin",
                    vpin=vpin,
                )
            if regime.market_state == "RISKY":
                return EntryFilterResult(
                    market_state="RISKY",
                    long_filter="WAIT",
                    short_filter="WAIT",
                    reason="adaptive_elevated_vpin",
                    vpin=vpin,
                )

        if vpin >= self.vpin_high:
            if not self.block_on_toxic_vpin:
                return EntryFilterResult(
                    market_state="TOXIC",
                    long_filter="WAIT",
                    short_filter="WAIT",
                    reason="toxic_vpin_watch_only",
                    vpin=vpin,
                )
            return EntryFilterResult(
                market_state="TOXIC",
                long_filter="BLOCKED",
                short_filter="BLOCKED",
                reason="toxic_vpin",
                vpin=vpin,
            )

        if vpin >= self.vpin_warn:
            return EntryFilterResult(
                market_state="RISKY",
                long_filter="WAIT",
                short_filter="WAIT",
                reason="elevated_vpin",
                vpin=vpin,
            )

        if latest_signal is None:
            return EntryFilterResult(
                market_state="READY",
                long_filter="WAIT",
                short_filter="WAIT",
                reason="no_signal",
                vpin=vpin,
            )

        signal_type = latest_signal.exhaustion_type.name
        if latest_signal.exhaustion_type is ExhaustionType.BUY_EXHAUSTION:
            return EntryFilterResult(
                market_state="READY",
                long_filter="WAIT",
                short_filter="OK",
                reason="buy_exhaustion",
                latest_signal_type=signal_type,
                latest_signal_confidence=latest_signal.confidence,
                vpin=vpin,
            )

        if latest_signal.exhaustion_type is ExhaustionType.SELL_EXHAUSTION:
            return EntryFilterResult(
                market_state="READY",
                long_filter="OK",
                short_filter="WAIT",
                reason="sell_exhaustion",
                latest_signal_type=signal_type,
                latest_signal_confidence=latest_signal.confidence,
                vpin=vpin,
            )

        return EntryFilterResult(
            market_state="READY",
            long_filter="WAIT",
            short_filter="WAIT",
            reason="unknown_signal",
            latest_signal_type=signal_type,
            latest_signal_confidence=latest_signal.confidence,
            vpin=vpin,
        )
