from __future__ import annotations

from typing import Iterable


PriceLevelInput = Iterable[tuple[str, str]] | list[tuple[str, str]]


class OrderBook:
    def __init__(self) -> None:
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

    def load_snapshot(self, bids: PriceLevelInput, asks: PriceLevelInput) -> None:
        self.bids = self._normalize_levels(bids)
        self.asks = self._normalize_levels(asks)

    def apply_delta(self, bids: PriceLevelInput, asks: PriceLevelInput) -> None:
        self._apply_side(self.bids, bids)
        self._apply_side(self.asks, asks)

    def best_bid(self) -> float | None:
        return max(self.bids) if self.bids else None

    def best_ask(self) -> float | None:
        return min(self.asks) if self.asks else None

    def iter_bids(self) -> list[tuple[float, float]]:
        return sorted(self.bids.items(), key=lambda item: item[0], reverse=True)

    def iter_asks(self) -> list[tuple[float, float]]:
        return sorted(self.asks.items(), key=lambda item: item[0])

    @staticmethod
    def _normalize_levels(levels: PriceLevelInput) -> dict[float, float]:
        normalized: dict[float, float] = {}
        for price_text, qty_text in levels:
            price = float(price_text)
            qty = float(qty_text)
            if qty > 0:
                normalized[price] = qty
        return normalized

    @staticmethod
    def _apply_side(side: dict[float, float], levels: PriceLevelInput) -> None:
        for price_text, qty_text in levels:
            price = float(price_text)
            qty = float(qty_text)
            if qty == 0:
                side.pop(price, None)
            else:
                side[price] = qty
