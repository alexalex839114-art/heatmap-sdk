from __future__ import annotations

from dataclasses import replace

from app.position import PositionState


class PositionTracker:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol.upper()
        self.current: PositionState | None = None

    def update(
        self,
        position: PositionState | None,
        now_ms: int,
        *,
        realized_vol: float = 0.0,
        stop_rv_multiplier: float = 1.0,
        take_rv_multiplier: float = 1.5,
    ) -> PositionState | None:
        if position is None:
            self.current = None
            return None
        if position.symbol != self.symbol:
            return self.current
        if not position.is_open:
            flat = replace(position, opened_at_ms=None)
            self.current = None
            return flat

        opened_at_ms = self.current.opened_at_ms if self.current is not None else None
        if opened_at_ms is None:
            opened_at_ms = now_ms
        if self.current is None:
            rv_levels = _rv_levels(
                position,
                realized_vol=realized_vol,
                stop_rv_multiplier=stop_rv_multiplier,
                take_rv_multiplier=take_rv_multiplier,
            )
            current = replace(
                position,
                opened_at_ms=opened_at_ms,
                realized_vol_at_entry=max(0.0, float(realized_vol)),
                rv_stop_price=rv_levels[0],
                rv_take_price=rv_levels[1],
            )
        else:
            current = replace(
                position,
                opened_at_ms=opened_at_ms,
                realized_vol_at_entry=self.current.realized_vol_at_entry,
                rv_stop_price=self.current.rv_stop_price,
                rv_take_price=self.current.rv_take_price,
            )
        self.current = current
        return current


def _rv_levels(
    position: PositionState,
    *,
    realized_vol: float,
    stop_rv_multiplier: float,
    take_rv_multiplier: float,
) -> tuple[float | None, float | None]:
    rv_distance = position.entry_price * max(0.0, float(realized_vol))
    if rv_distance <= 0.0:
        return None, None

    stop_distance = rv_distance * max(0.0, float(stop_rv_multiplier))
    take_distance = rv_distance * max(0.0, float(take_rv_multiplier))
    stop_price: float | None = None
    take_price: float | None = None

    if position.side == "LONG":
        if stop_distance > 0.0:
            stop_price = position.entry_price - stop_distance
        if take_distance > 0.0:
            take_price = position.entry_price + take_distance
    elif position.side == "SHORT":
        if stop_distance > 0.0:
            stop_price = position.entry_price + stop_distance
        if take_distance > 0.0:
            take_price = position.entry_price - take_distance

    return stop_price, take_price
