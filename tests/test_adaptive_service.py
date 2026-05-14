from app.adaptive_service import AdaptiveMarketService, live_scalping_symbol_config


def test_adaptive_service_registers_symbol_and_exposes_state():
    service = AdaptiveMarketService("btcusdt")

    state = service.state()

    assert service.symbol == "BTCUSDT"
    assert state.is_ready is False
    assert state.buckets_filled == 0


def test_on_agg_trade_accepts_binance_trade_shape():
    service = AdaptiveMarketService("BTCUSDT")

    signal = service.on_agg_trade(
        {
            "price": 65000.0,
            "qty": 0.1,
            "timestamp": 1700000000000,
            "is_buyer_maker": False,
        }
    )

    assert signal is None
    assert service.latest_signal is None


def test_on_agg_trade_ignores_other_symbol_when_present():
    service = AdaptiveMarketService("BTCUSDT")

    service.on_agg_trade(
        {
            "symbol": "ETHUSDT",
            "price": 3200.0,
            "qty": 1.0,
            "timestamp": 1700000000000,
            "is_buyer_maker": False,
        }
    )

    assert service.state().buy_exhaustion_z == 0.0
    assert service.state().sell_exhaustion_z == 0.0


def test_top_of_book_updates_obi_without_signal():
    service = AdaptiveMarketService("BTCUSDT")

    service.on_top_of_book(
        best_bid=100.0,
        best_ask=100.1,
        bid_vol=30.0,
        ask_vol=10.0,
        timestamp_ms=1700000000000,
    )

    assert service.last_obi == 0.5


def test_live_scalping_config_is_less_conservative_than_sdk_defaults():
    cfg = live_scalping_symbol_config()

    assert cfg.rolling_volume_window_s == 60.0
    assert cfg.target_buckets_per_window == 20
    assert cfg.bucket_size_recompute_interval_s == 1.0
    assert cfg.vpin_window == 20
    assert cfg.min_buckets_for_vpin == 10
    assert cfg.realized_vol_window_ms == 60_000
    assert cfg.min_price_excursion_bps == 2.0
    assert cfg.min_price_excursion_vol_multiplier == 0.6
    assert cfg.vpin_mid == 0.70
    assert cfg.vpin_high == 1.01


def test_adaptive_market_service_updates_price_excursion_settings():
    service = AdaptiveMarketService("BTCUSDT")

    service.update_price_excursion_settings(
        min_price_excursion_bps=4.0,
        min_price_excursion_vol_multiplier=1.2,
    )

    cfg = service.symbol_config()
    assert cfg.min_price_excursion_bps == 4.0
    assert cfg.min_price_excursion_vol_multiplier == 1.2


def test_latest_signal_for_display_is_held_for_ttl():
    service = AdaptiveMarketService("BTCUSDT", signal_display_ttl_ms=3000)
    service.latest_signal = object()
    service.latest_signal_seen_ms = 1000

    assert service.latest_signal_for_display(now_ms=3500) is service.latest_signal
    assert service.latest_signal_for_display(now_ms=4500) is None
