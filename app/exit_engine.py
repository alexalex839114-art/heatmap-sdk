from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adaptive_sdk.types import ExhaustionType, Signal

from app.assistant_config import AssistantRiskSettings
from app.position import PositionState


@dataclass(slots=True, frozen=True)
class ExitDecision:
    state: str
    should_close: bool
    reason: str | None = None
    hard_exit: bool = False


class ExitEngine:
    def __init__(self, vpin_high: float = 0.50, high_confidence: float = 0.75) -> None:
        self.vpin_high = vpin_high
        self.high_confidence = high_confidence
        self._soft_reason: str | None = None
        self._soft_started_ms: int | None = None

    def evaluate(
        self,
        position: PositionState,
        sdk_state: Any,
        latest_signal: Signal | None,
        settings: AssistantRiskSettings,
        now_ms: int,
        confluence_exit_reason: str | None = None,
    ) -> ExitDecision:
        if not position.is_open:
            self._clear_soft()
            return ExitDecision(state="NO_POSITION", should_close=False)

        # Confluence-driven exit takes priority over the legacy per-Binance
        # hard-exit rules. When the multi-exchange traffic light no longer
        # backs the open position, close immediately at market regardless of
        # auto_exit_enabled — the caller already gated this by
        # auto_trade_enabled, mirroring the entry toggle the user controls.
        if confluence_exit_reason is not None:
            self._clear_soft()
            return ExitDecision(
                state="EXIT_ARMED",
                should_close=True,
                reason=confluence_exit_reason,
                hard_exit=True,
            )

        hard_reason = self._hard_exit_reason(position, sdk_state, latest_signal, settings)
        if hard_reason is not None:
            self._clear_soft()
            return ExitDecision(
                state="EXIT_ARMED",
                should_close=settings.auto_exit_enabled,
                reason=hard_reason,
                hard_exit=True,
            )

        soft_reason = self._soft_exit_reason(position, settings, now_ms)
        if soft_reason is None:
            self._clear_soft()
            return ExitDecision(state="TRACKING", should_close=False)

        if self._soft_reason != soft_reason:
            self._soft_reason = soft_reason
            self._soft_started_ms = now_ms
            return ExitDecision(state="WARNING", should_close=False, reason=soft_reason)

        elapsed = now_ms - (self._soft_started_ms or now_ms)
        if elapsed >= settings.confirmation_ms:
            return ExitDecision(
                state="EXIT_ARMED",
                should_close=settings.auto_exit_enabled,
                reason=soft_reason,
                hard_exit=False,
            )
        return ExitDecision(state="WARNING", should_close=False, reason=soft_reason)

    def _hard_exit_reason(
        self,
        position: PositionState,
        sdk_state: Any,
        latest_signal: Signal | None,
        settings: AssistantRiskSettings,
    ) -> str | None:
        if position.unrealized_pnl <= -abs(settings.max_loss_usdt):
            return "max_loss"

        rv_reason = _rv_exit_reason(position)
        if rv_reason is not None:
            return rv_reason

        vpin = float(getattr(sdk_state, "vpin", 0.0)) if sdk_state is not None else 0.0
        if settings.toxic_vpin_exit_enabled and vpin >= self.vpin_high:
            return "toxic_vpin"

        if (
            settings.opposite_signal_exit_enabled
            and latest_signal is not None
            and latest_signal.confidence >= self.high_confidence
            and _is_opposite_signal(position, latest_signal)
        ):
            return "opposite_signal_high_confidence"

        return None

    @staticmethod
    def _soft_exit_reason(
        position: PositionState,
        settings: AssistantRiskSettings,
        now_ms: int,
    ) -> str | None:
        if position.opened_at_ms is None:
            return None
        holding_ms = now_ms - position.opened_at_ms
        if holding_ms >= settings.max_holding_time_sec * 1000.0:
            return "max_holding_time"
        return None

    def _clear_soft(self) -> None:
        self._soft_reason = None
        self._soft_started_ms = None


def _is_opposite_signal(position: PositionState, signal: Signal) -> bool:
    if position.side == "LONG":
        return signal.exhaustion_type is ExhaustionType.BUY_EXHAUSTION
    if position.side == "SHORT":
        return signal.exhaustion_type is ExhaustionType.SELL_EXHAUSTION
    return False


def _rv_exit_reason(position: PositionState) -> str | None:
    mark_price = position.estimated_mark_price
    if mark_price is None:
        return None

    if position.side == "LONG":
        if position.rv_stop_price is not None and mark_price <= position.rv_stop_price:
            return "rv_stop_loss"
        if position.rv_take_price is not None and mark_price >= position.rv_take_price:
            return "rv_take_profit"
    if position.side == "SHORT":
        if position.rv_stop_price is not None and mark_price >= position.rv_stop_price:
            return "rv_stop_loss"
        if position.rv_take_price is not None and mark_price <= position.rv_take_price:
            return "rv_take_profit"
    return None
