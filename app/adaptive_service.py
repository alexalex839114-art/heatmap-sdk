from __future__ import annotations

from typing import Any

from adaptive_sdk import (
    AdaptiveAnalyticsSDK,
    BookSnapshot,
    BucketSizePolicy,
    GlobalConfig,
    Signal,
    SymbolConfig,
    SymbolState,
    TradeTick,
)


class AdaptiveMarketService:
    """Bridge Binance market payloads into the embedded adaptive SDK."""

    def __init__(
        self,
        symbol: str,
        config: SymbolConfig | None = None,
        signal_display_ttl_ms: int = 5000,
    ) -> None:
        self.symbol = symbol.upper()
        global_config = GlobalConfig(
            default_symbol_config=config or live_scalping_symbol_config()
        )
        self._sdk = AdaptiveAnalyticsSDK(global_config)
        self._sdk.register_symbol(self.symbol)
        self.latest_signal: Signal | None = None
        self.latest_signal_seen_ms: int | None = None
        self.signal_display_ttl_ms = signal_display_ttl_ms
        self.last_obi: float = 0.0
        self.trade_count: int = 0

    def on_agg_trade(self, trade: dict[str, Any]) -> Signal | None:
        symbol = str(trade.get("symbol", self.symbol)).upper()
        if symbol != self.symbol:
            return None

        self.trade_count += 1
        tick = TradeTick(
            symbol=self.symbol,
            price=float(trade["price"]),
            quantity=float(trade["qty"]),
            is_buyer_maker=bool(trade.get("is_buyer_maker", False)),
            timestamp=_timestamp_seconds(trade.get("timestamp")),
        )
        signal = self._sdk.on_trade(tick)
        if signal is not None:
            self.latest_signal = signal
            self.latest_signal_seen_ms = int(float(trade.get("timestamp", 0.0)))
        return signal

    def on_top_of_book(
        self,
        best_bid: float,
        best_ask: float,
        bid_vol: float,
        ask_vol: float,
        timestamp_ms: int | float | None,
    ) -> None:
        snapshot = BookSnapshot(
            symbol=self.symbol,
            best_bid=float(best_bid),
            best_ask=float(best_ask),
            bid_vol=float(bid_vol),
            ask_vol=float(ask_vol),
            timestamp=_timestamp_seconds(timestamp_ms),
        )
        self._sdk.on_book_update(snapshot)
        denom = snapshot.bid_vol + snapshot.ask_vol
        if denom > 0.0:
            self.last_obi = (snapshot.bid_vol - snapshot.ask_vol) / denom

    def state(self) -> SymbolState:
        return self._sdk.get_state(self.symbol)

    def warmup_progress(self) -> dict[str, int]:
        state = self.state()
        return {
            "buckets_filled": state.buckets_filled,
            "min_buckets_for_vpin": self._sdk._contexts[self.symbol].config.min_buckets_for_vpin,
            "trade_count": self.trade_count,
            "min_ticks_for_z": self._sdk._contexts[self.symbol].config.min_ticks_for_z,
        }

    def symbol_config(self) -> SymbolConfig:
        return self._sdk._contexts[self.symbol].config

    def update_price_excursion_settings(
        self,
        *,
        min_price_excursion_bps: float,
        min_price_excursion_vol_multiplier: float,
    ) -> None:
        cfg = self.symbol_config()
        cfg.min_price_excursion_bps = max(0.0, float(min_price_excursion_bps))
        cfg.min_price_excursion_vol_multiplier = max(
            0.0,
            float(min_price_excursion_vol_multiplier),
        )

    def update_require_price_extrema_progress(self, enabled: bool) -> None:
        cfg = self.symbol_config()
        cfg.require_price_extrema_progress = bool(enabled)

    def latest_signal_for_display(self, now_ms: int) -> Signal | None:
        if self.latest_signal is None or self.latest_signal_seen_ms is None:
            return None
        if now_ms - self.latest_signal_seen_ms > self.signal_display_ttl_ms:
            return None
        return self.latest_signal


def _timestamp_seconds(value: Any) -> float:
    if value is None:
        return 0.0
    ts = float(value)
    if ts > 10_000_000_000:
        return ts / 1000.0
    return ts


def live_scalping_symbol_config() -> SymbolConfig:
    """Practical live UI preset for liquid Binance USD-M scalping.

    The SDK defaults are intentionally conservative research defaults. For a
    live dashboard, 50 ten-BTC buckets can take too long to warm up. This
    preset adapts bucket size to the last minute of traded volume and leaves
    the live entry filter to classify VPIN by the symbol's current regime.
    """
    return SymbolConfig(
        bucket_policy=BucketSizePolicy.ROLLING_DAILY,
        fixed_bucket_size=2.0,
        rolling_volume_window_s=60.0,
        target_buckets_per_window=20,
        min_bucket_size=0.01,
        bucket_size_recompute_interval_s=1.0,
        vpin_window=20,
        min_buckets_for_vpin=10,
        min_ticks_for_z=200,
        realized_vol_window_ms=60_000,
        min_price_excursion_bps=2.0,
        min_price_excursion_vol_multiplier=0.6,
        require_price_extrema_progress=True,
        vpin_mid=0.70,
        vpin_high=1.01,
        z_thresholds=(1.5, 2.0, 2.5),
    )


def gate_scalping_symbol_config() -> SymbolConfig:
    """Live UI preset tuned for Gate.io USDT-margined perpetual futures.

    Gate's per-exchange trade rate is significantly lower than Binance / OKX
    (measured ~1-2 trades/s vs ~5-7 trades/s on BTC), so the canonical 200-
    tick z-score warmup would leave the Gate indicator stuck in WARMING for
    1-2+ minutes while the other three lights have already gone READY
    (Binance/OKX reach ready in ~30s on BTC). 50 ticks is the smallest
    sample that still produces a usable z-score (variance estimate has
    ~10% relative error at n=50), and brings Gate's warmup time roughly
    in line with its peers. VPIN bucket count and everything else stays
    identical to the Binance preset so signal semantics are consistent
    across exchanges.
    """
    from dataclasses import replace
    return replace(live_scalping_symbol_config(), min_ticks_for_z=50)
