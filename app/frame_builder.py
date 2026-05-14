from __future__ import annotations

import math
import time
from dataclasses import dataclass

from app.order_book import OrderBook


@dataclass
class FrameTrade:
    price: float
    qty: float
    y: int
    is_buyer_maker: bool = False


@dataclass
class Frame:
    timestamp: int
    column: list[int]
    mid_price: float
    best_bid: float | None
    best_ask: float | None
    trades: list[FrameTrade]


class FrameBuilder:
    def __init__(
        self,
        height: int,
        tick_size: float,
        aggregation: int = 1,
        visible_levels: int | None = None,
        buffer_levels: int = 128,
        recenter_margin_levels: int = 32,
    ) -> None:
        self.height = height
        self.tick_size = tick_size
        self.aggregation = max(1, int(aggregation))
        self.visible_levels = visible_levels or height
        self.buffer_levels = max(0, int(buffer_levels))
        self.recenter_margin_levels = max(0, int(recenter_margin_levels))
        self.display_step = self.tick_size * self.aggregation if self.tick_size > 0 else 1.0
        self.total_levels = self.visible_levels + (2 * self.buffer_levels)
        self._origin_price: float | None = None

    def build(self, book: OrderBook, trades: list[dict]) -> Frame:
        best_bid = book.best_bid()
        best_ask = book.best_ask()
        if best_bid is None or best_ask is None:
            return Frame(
                timestamp=time.time_ns(),
                column=[0] * self.height,
                mid_price=0.0,
                best_bid=best_bid,
                best_ask=best_ask,
                trades=[],
            )

        mid_price = (best_bid + best_ask) / 2
        self._ensure_grid_anchor(mid_price)
        bucket_volumes = [0.0] * self.total_levels

        for price, volume in book.iter_bids() + book.iter_asks():
            index = self._bucket_index_for_price(price)
            if index is None:
                continue
            bucket_volumes[index] += volume

        full_column = self._normalize_column(bucket_volumes)
        visible_bottom_up = full_column[
            self.buffer_levels : self.buffer_levels + self.visible_levels
        ]
        column = list(reversed(visible_bottom_up))
        if len(column) < self.height:
            column.extend([0] * (self.height - len(column)))
        elif len(column) > self.height:
            column = column[: self.height]

        mapped_trades: list[FrameTrade] = []
        for trade in trades:
            price = float(trade["price"])
            bucket_index = self._bucket_index_for_price(price)
            if bucket_index is None:
                continue
            visible_index = bucket_index - self.buffer_levels
            if visible_index < 0 or visible_index >= self.visible_levels:
                continue
            y = min(self.height - 1, self.visible_levels - 1 - visible_index)
            mapped_trades.append(
                FrameTrade(
                    price=price,
                    qty=float(trade["qty"]),
                    y=y,
                    is_buyer_maker=bool(trade.get("is_buyer_maker", False)),
                )
            )

        return Frame(
            timestamp=time.time_ns(),
            column=column,
            mid_price=mid_price,
            best_bid=best_bid,
            best_ask=best_ask,
            trades=mapped_trades,
        )

    def _ensure_grid_anchor(self, mid_price: float) -> None:
        if self._origin_price is None:
            self._recenter(mid_price)
            return

        visible_index = self._visible_index_for_price(mid_price)
        lower_threshold = int(self.visible_levels * 0.25)
        upper_threshold = int(self.visible_levels * 0.75)
        if (
            visible_index is None
            or visible_index < lower_threshold
            or visible_index >= upper_threshold
        ):
            self._recenter(mid_price)

    def _recenter(self, mid_price: float) -> None:
        center_index = self.total_levels // 2
        quantized_mid = round(mid_price / self.display_step) * self.display_step
        self._origin_price = round(
            quantized_mid - (center_index * self.display_step),
            12,
        )

    def _bucket_index_for_price(self, price: float) -> int | None:
        if self._origin_price is None:
            return None
        offset = (price - self._origin_price) / self.display_step
        index = int(math.floor(offset + 1e-9))
        if index < 0 or index >= self.total_levels:
            return None
        return index

    def _visible_index_for_price(self, price: float) -> int | None:
        bucket_index = self._bucket_index_for_price(price)
        if bucket_index is None:
            return None
        visible_index = bucket_index - self.buffer_levels
        if visible_index < 0 or visible_index >= self.visible_levels:
            return None
        return visible_index

    @staticmethod
    def _normalize_column(bucket_volumes: list[float]) -> list[int]:
        transformed = [math.log1p(value) for value in bucket_volumes]
        max_value = max(transformed, default=0.0)
        if max_value == 0:
            return [0] * len(bucket_volumes)
        return [int(round(255 * (value / max_value))) for value in transformed]
