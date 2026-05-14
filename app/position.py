from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class PositionState:
    symbol: str
    amount: float
    entry_price: float
    break_even_price: float = 0.0
    unrealized_pnl: float = 0.0
    position_side: str = "BOTH"
    update_time_ms: int | None = None
    opened_at_ms: int | None = None
    realized_vol_at_entry: float = 0.0
    rv_stop_price: float | None = None
    rv_take_price: float | None = None

    @property
    def is_open(self) -> bool:
        return self.amount != 0.0

    @property
    def quantity(self) -> float:
        return abs(self.amount)

    @property
    def side(self) -> str:
        if self.amount > 0.0:
            return "LONG"
        if self.amount < 0.0:
            return "SHORT"
        return "FLAT"

    @property
    def estimated_mark_price(self) -> float | None:
        if self.amount == 0.0:
            return None
        return self.entry_price + (self.unrealized_pnl / self.amount)


def parse_account_update_positions(event: dict[str, Any]) -> dict[str, PositionState]:
    """Parse Binance USD-M ACCOUNT_UPDATE positions for v1 one-way mode.

    Hedge-mode rows are intentionally ignored in v1 because the assistant is
    only allowed to close a single one-way position.
    """
    account = event.get("a")
    if not isinstance(account, dict):
        return {}
    rows = account.get("P")
    if not isinstance(rows, list):
        return {}

    update_time_ms = _optional_int(event.get("E"))
    parsed: dict[str, PositionState] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("ps", "BOTH") != "BOTH":
            continue
        symbol = row.get("s")
        if not symbol:
            continue
        try:
            amount = float(row.get("pa", 0.0))
            entry_price = float(row.get("ep", 0.0))
            break_even = float(row.get("bep", 0.0))
            unrealized_pnl = float(row.get("up", 0.0))
        except (TypeError, ValueError):
            continue

        parsed[str(symbol).upper()] = PositionState(
            symbol=str(symbol).upper(),
            amount=amount,
            entry_price=entry_price,
            break_even_price=break_even,
            unrealized_pnl=unrealized_pnl,
            position_side="BOTH",
            update_time_ms=update_time_ms,
        )
    return parsed


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
