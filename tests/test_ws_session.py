import asyncio

import pytest
from types import SimpleNamespace

from adaptive_sdk.types import ExhaustionType, MetricsSnapshot, Signal
from app.ws_session import (
    BrowserSession,
    LiveHeatmapService,
    TOXIC_DIRECTION_MIN_SIGNED_VPIN,
    _confluence_entry_side,
    _confluence_exit_reason,
    _entry_result_payload,
    _toxic_direction,
)
from app.assistant_config import AssistantRiskSettings
from app.adaptive_service import AdaptiveMarketService
from app.position import PositionState
from app.position_tracker import PositionTracker
from app.settings import FRAME_INTERVAL_MS, POSITION_REST_FALLBACK_INTERVAL_MS


def test_start_heatmap_requires_synced_connection():
    session = BrowserSession()

    with pytest.raises(RuntimeError):
        session.start_heatmap()


def test_stop_heatmap_changes_state_to_stopped():
    session = BrowserSession()
    session.mark_synced("BTCUSDT")
    session.start_heatmap()
    session.stop_heatmap()

    assert session.state == "stopped"


@pytest.mark.asyncio
async def test_disconnect_command_returns_disconnected_state():
    """`{type: "disconnect"}` from the UI must transition the session back to
    ``disconnected`` and clear the active symbol so the Stop WS button gives
    the user a clean reset path."""
    session = BrowserSession()
    session.mark_synced("BTCUSDT")
    session.compression = 4
    assert session.state == "live_ready"

    event = await session.handle_command({"type": "disconnect"})

    assert event["type"] == "status"
    assert event["state"] == "disconnected"
    assert event["symbol"] is None
    assert session.state == "disconnected"
    assert session.symbol is None
    assert session.compression == 1


@pytest.mark.asyncio
async def test_set_assistant_settings_updates_risk_settings():
    session = BrowserSession()

    event = await session.handle_command(
        {
            "type": "set_assistant_settings",
            "settings": {
                "max_loss_usdt": 2.5,
                "max_holding_time_sec": 12,
                "confirmation_ms": 250,
                "opposite_signal_exit_enabled": False,
                "toxic_vpin_exit_enabled": True,
                "min_price_excursion_bps": 3.5,
                "min_price_excursion_vol_multiplier": 0.8,
                "stop_rv_multiplier": 1.1,
                "take_rv_multiplier": 1.9,
            },
        }
    )

    assert event["type"] == "assistant_status"
    assert session.assistant_settings.max_loss_usdt == 2.5
    assert session.assistant_settings.max_holding_time_sec == 12.0
    assert session.assistant_settings.confirmation_ms == 250
    assert session.assistant_settings.opposite_signal_exit_enabled is False
    assert session.assistant_settings.min_price_excursion_bps == 3.5
    assert session.assistant_settings.min_price_excursion_vol_multiplier == 0.8
    assert event["min_price_excursion_bps"] == 3.5
    assert event["min_price_excursion_vol_multiplier"] == 0.8
    assert session.assistant_settings.stop_rv_multiplier == 1.1
    assert session.assistant_settings.take_rv_multiplier == 1.9


def test_assistant_status_includes_rv_exit_multipliers():
    session = BrowserSession()
    session.assistant_settings = AssistantRiskSettings(
        stop_rv_multiplier=1.2,
        take_rv_multiplier=2.1,
    )

    event = session.assistant_status_event()

    assert event["stop_rv_multiplier"] == 1.2
    assert event["take_rv_multiplier"] == 2.1


def test_assistant_status_includes_auto_trade_settings():
    session = BrowserSession()
    session.assistant_settings = AssistantRiskSettings(
        auto_trade_enabled=True,
        trade_notional_usdt=12.5,
    )

    event = session.assistant_status_event()

    assert event["auto_trade_enabled"] is True
    assert event["trade_notional_usdt"] == 12.5


def test_trading_status_event_includes_lifecycle_and_cooldown():
    service = LiveHeatmapService()
    service.trading_state = "COOLDOWN"
    service.cooldown_until_ms = 41_000

    event = service.trading_status_event(now_ms=11_000)

    assert event["type"] == "trading_status"
    assert event["state"] == "COOLDOWN"
    assert event["enabled"] is False
    assert event["cooldown_remaining_ms"] == 30_000


def test_market_settings_are_applied_to_active_sdk():
    service = LiveHeatmapService()
    service.adaptive_market = AdaptiveMarketService("BTCUSDT")
    service.bybit_adaptive_market = AdaptiveMarketService("BTCUSDT")
    service.session.assistant_settings = AssistantRiskSettings(
        min_price_excursion_bps=4.0,
        min_price_excursion_vol_multiplier=1.2,
    )

    service._apply_assistant_market_settings()

    cfg = service.adaptive_market.symbol_config()
    assert cfg.min_price_excursion_bps == 4.0
    assert cfg.min_price_excursion_vol_multiplier == 1.2
    bybit_cfg = service.bybit_adaptive_market.symbol_config()
    assert bybit_cfg.min_price_excursion_bps == 4.0
    assert bybit_cfg.min_price_excursion_vol_multiplier == 1.2


@pytest.mark.asyncio
async def test_auto_exit_commands_toggle_session_setting():
    session = BrowserSession()

    enabled = await session.handle_command({"type": "enable_auto_exit"})
    disabled = await session.handle_command({"type": "disable_auto_exit"})

    assert enabled["auto_exit_enabled"] is True
    assert disabled["auto_exit_enabled"] is False
    assert session.assistant_settings.auto_exit_enabled is False


@pytest.mark.asyncio
async def test_auto_trade_commands_toggle_session_setting():
    session = BrowserSession()

    enabled = await session.handle_command({"type": "enable_auto_trade"})
    disabled = await session.handle_command({"type": "disable_auto_trade"})

    assert enabled["auto_trade_enabled"] is True
    assert disabled["auto_trade_enabled"] is False
    assert session.assistant_settings.auto_trade_enabled is False


@pytest.mark.asyncio
async def test_connect_command_sets_connecting_status():
    session = BrowserSession()

    event = await session.handle_command(
        {"type": "connect", "symbol": "BTCUSDT", "compression": 2}
    )

    assert event["type"] == "status"
    assert event["state"] == "connecting"
    assert event["symbol"] == "BTCUSDT"
    assert event["compression"] == 2
    assert session.compression == 2


@pytest.mark.asyncio
async def test_connect_uses_client_tick_size_and_requested_aggregation(monkeypatch):
    service = LiveHeatmapService()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.tick_size = 0.1

        async def start(self):
            return None

        async def stop(self):
            return None

    monkeypatch.setattr("app.ws_session.BinanceMarketClient", FakeClient)
    monkeypatch.setattr("app.ws_session.BybitMarketClient", FakeClient)

    await service._connect_symbol("BTCUSDT", compression=3)

    assert service.frame_builder.tick_size == 0.1
    assert service.frame_builder.aggregation == 3


@pytest.mark.asyncio
async def test_connect_wires_adaptive_market_callbacks(monkeypatch):
    service = LiveHeatmapService()

    class FakeClient:
        def __init__(self, *args, **kwargs):
            self.tick_size = 0.1
            self.on_trade = kwargs.get("on_trade")
            self.on_book_update = kwargs.get("on_book_update")

        async def start(self):
            return None

        async def stop(self):
            return None

    monkeypatch.setattr("app.ws_session.BinanceMarketClient", FakeClient)
    monkeypatch.setattr("app.ws_session.BybitMarketClient", FakeClient)

    await service._connect_symbol("BTCUSDT", compression=1)

    assert service.adaptive_market is not None
    assert service.entry_filter is not None
    assert service.exit_engine is not None
    assert service.client.on_trade is not None
    assert service.client.on_book_update is not None
    assert service.bybit_client is not None
    assert service.bybit_adaptive_market is not None
    assert service.bybit_entry_filter is not None
    assert service.bybit_client.on_trade is not None
    assert service.bybit_client.on_book_update is not None


@pytest.mark.asyncio
async def test_bybit_trade_updates_only_bybit_sdk():
    service = LiveHeatmapService()
    service.adaptive_market = AdaptiveMarketService("BTCUSDT")
    service.bybit_adaptive_market = AdaptiveMarketService("BTCUSDT")

    service._on_bybit_market_trade(
        {
            "symbol": "BTCUSDT",
            "price": 65000.0,
            "qty": 0.1,
            "timestamp": 1700000000000,
            "is_buyer_maker": False,
        }
    )

    assert service.adaptive_market.trade_count == 0
    assert service.bybit_adaptive_market.trade_count == 1


@pytest.mark.asyncio
async def test_refresh_position_updates_tracker():
    service = LiveHeatmapService()
    service.position_tracker = PositionTracker("BTCUSDT")

    class FakeAccount:
        async def fetch_position(self, symbol):
            return PositionState(
                symbol=symbol,
                amount=0.01,
                entry_price=65000,
                unrealized_pnl=1.2,
            )

    service.account_client = FakeAccount()

    position = await service._refresh_position("BTCUSDT", now_ms=1000)

    assert position is not None
    assert position.side == "LONG"
    assert position.opened_at_ms == 1000
    assert service.current_position == position


@pytest.mark.asyncio
async def test_refresh_position_discards_stale_rest_after_ws_push():
    """REST poll that suspends across a newer user-data ACCOUNT_UPDATE must
    not overwrite the fresher WS state (Devin Review race fix)."""
    service = LiveHeatmapService()
    service.position_tracker = PositionTracker("BTCUSDT")
    # Pre-seed an "open" position as if it had already been pushed via WS.
    service.current_position = PositionState(
        symbol="BTCUSDT",
        amount=0.01,
        entry_price=65000.0,
    )
    service.position_tracker.current = service.current_position

    async def noop(payload):
        return None

    service.broadcast = noop

    class FakeAccount:
        def __init__(self):
            self.calls = 0

        async def fetch_position(self, symbol):
            self.calls += 1
            # While the REST call is "in flight", simulate a newer WS push
            # that already moved the position to flat.
            service._last_ws_position_update_ms = 2_000
            service.current_position = None
            service.position_tracker.current = None
            # Return stale REST data still showing the old open position.
            return PositionState(
                symbol=symbol,
                amount=0.01,
                entry_price=65000.0,
                update_time_ms=1_500,
            )

    service.account_client = FakeAccount()

    result = await service._refresh_position("BTCUSDT", now_ms=2_500)

    # REST result must be discarded; position must remain flat as WS dictated.
    assert service.current_position is None
    # Returned value reflects the current (WS-driven) state, not the stale REST.
    assert result is None


@pytest.mark.asyncio
async def test_refresh_position_discards_rest_with_older_update_time():
    """REST row whose own updateTime predates the last WS push is dropped
    even when no concurrent task interleaving occurred."""
    service = LiveHeatmapService()
    service.position_tracker = PositionTracker("BTCUSDT")
    service._last_ws_position_update_ms = 5_000
    # Current position reflects the latest WS event (flat).
    service.current_position = None

    async def noop(payload):
        return None

    service.broadcast = noop

    class FakeAccount:
        async def fetch_position(self, symbol):
            return PositionState(
                symbol=symbol,
                amount=0.02,
                entry_price=65000.0,
                update_time_ms=3_000,
            )

    service.account_client = FakeAccount()

    result = await service._refresh_position("BTCUSDT", now_ms=6_000)

    assert result is None
    assert service.current_position is None


@pytest.mark.asyncio
async def test_user_data_account_update_advances_ws_timestamp():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.position_tracker = PositionTracker("BTCUSDT")

    async def noop(payload):
        return None

    service.broadcast = noop

    from app.position import parse_account_update_positions

    event = {
        "e": "ACCOUNT_UPDATE",
        "E": 1_700_000_000_000,
        "a": {
            "P": [
                {
                    "s": "BTCUSDT",
                    "pa": "0.01",
                    "ep": "65000.0",
                    "bep": "65010.0",
                    "up": "0.0",
                    "ps": "BOTH",
                }
            ]
        },
    }
    await service._on_user_data_account_update(
        parse_account_update_positions(event), event
    )
    assert service._last_ws_position_update_ms == 1_700_000_000_000

    # An older event must not rewind the watermark.
    older = dict(event)
    older["E"] = 1_600_000_000_000
    await service._on_user_data_account_update(
        parse_account_update_positions(older), older
    )
    assert service._last_ws_position_update_ms == 1_700_000_000_000


@pytest.mark.asyncio
async def test_exit_evaluation_closes_position_once_on_hard_exit():
    service = LiveHeatmapService()
    service.session.assistant_settings = AssistantRiskSettings(
        auto_exit_enabled=True,
        max_loss_usdt=5.0,
    )
    service.current_position = PositionState(
        symbol="BTCUSDT",
        amount=0.01,
        entry_price=65000,
        unrealized_pnl=-6.0,
    )

    class FakeExecutor:
        def __init__(self):
            self.calls = []
            self.close_pending = False

        async def close_position(self, position):
            self.calls.append(position)
            self.close_pending = True
            return {"status": "NEW"}

    service.order_executor = FakeExecutor()

    decision = await service._evaluate_exit(now_ms=1000)

    assert decision.should_close is True
    assert decision.reason == "max_loss"
    assert len(service.order_executor.calls) == 1


@pytest.mark.asyncio
async def test_exit_evaluation_broadcasts_exit_and_order_status():
    service = LiveHeatmapService()
    service.session.assistant_settings = AssistantRiskSettings(
        auto_exit_enabled=True,
        max_loss_usdt=5.0,
    )
    service.current_position = PositionState(
        symbol="BTCUSDT",
        amount=0.01,
        entry_price=65000,
        unrealized_pnl=-6.0,
    )
    events = []

    async def capture(payload):
        events.append(payload)

    service.broadcast = capture

    class FakeExecutor:
        close_pending = False

        async def close_position(self, position):
            return {"status": "NEW", "clientOrderId": "abc"}

    service.order_executor = FakeExecutor()

    await service._evaluate_exit(now_ms=1000)

    assert events[0]["type"] == "exit_status"
    assert events[0]["reason"] == "max_loss"
    assert events[1]["type"] == "order_status"
    assert events[1]["status"] == "NEW"


@pytest.mark.asyncio
async def test_exit_ignores_stale_opposite_signal():
    service = LiveHeatmapService()
    service.session.assistant_settings = AssistantRiskSettings(
        auto_exit_enabled=True,
        opposite_signal_exit_enabled=True,
    )
    service.current_position = PositionState(
        symbol="BTCUSDT",
        amount=0.01,
        entry_price=65000,
    )
    service.adaptive_market = SimpleNamespace(
        state=lambda: SimpleNamespace(vpin=0.1),
        latest_signal=Signal(
            signal_id="old",
            symbol="BTCUSDT",
            timestamp=1.0,
            exhaustion_type=ExhaustionType.BUY_EXHAUSTION,
            confidence=0.95,
            arm_id=1,
            metrics=MetricsSnapshot(
                vpin=0.1,
                z_score_buy_flow=3.0,
                z_score_sell_flow=0.0,
                obi=0.0,
                bucket_size=1.0,
                buckets_filled=10,
            ),
        ),
        latest_signal_for_display=lambda now_ms: None,
    )

    class FakeExecutor:
        close_pending = False

        def __init__(self):
            self.calls = []

        async def close_position(self, position):
            self.calls.append(position)
            return {"status": "NEW"}

    service.order_executor = FakeExecutor()

    decision = await service._evaluate_exit(now_ms=10_000)

    assert decision.should_close is False
    assert service.order_executor.calls == []


@pytest.mark.asyncio
async def test_frame_loop_survives_transient_position_poll_error():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.session.start_heatmap()

    class FakeFrameBuilder:
        def __init__(self):
            self.calls = 0

        def build(self, book, trades):
            self.calls += 1
            if self.calls >= 2:
                service.session.stop_heatmap()
            return SimpleNamespace(
                timestamp=1,
                column=[0],
                mid_price=100.0,
                best_bid=99.0,
                best_ask=101.0,
                trades=[],
            )

    async def failing_poll(now_ms):
        raise RuntimeError("temporary account failure")

    events = []

    async def capture(payload):
        events.append(payload)

    async def noop_exit(now_ms):
        return None

    async def noop_snapshot():
        return None

    service.frame_builder = FakeFrameBuilder()
    service._poll_position_if_due = failing_poll
    service._evaluate_exit = noop_exit
    service._broadcast_assistant_snapshot = noop_snapshot
    service.broadcast = capture

    await service._frame_loop()

    assert service.frame_builder.calls >= 2
    assert any(event["type"] == "account_error" for event in events)


@pytest.mark.asyncio
async def test_frame_loop_does_not_wait_for_slow_position_poll():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.session.start_heatmap()

    class FakeFrameBuilder:
        def __init__(self):
            self.calls = 0

        def build(self, book, trades):
            self.calls += 1
            if self.calls >= 3:
                service.session.stop_heatmap()
            return SimpleNamespace(
                timestamp=self.calls,
                column=[0],
                mid_price=100.0,
                best_bid=99.0,
                best_ask=101.0,
                trades=[],
            )

    async def slow_poll(now_ms):
        await asyncio.sleep(1)

    async def noop_exit(now_ms):
        return None

    async def noop_snapshot():
        return None

    service.frame_builder = FakeFrameBuilder()
    service._poll_position_if_due = slow_poll
    service._evaluate_exit = noop_exit
    service._broadcast_assistant_snapshot = noop_snapshot
    service.broadcast = lambda payload: _async_none()

    await asyncio.wait_for(service._frame_loop(), timeout=0.5)

    assert service.frame_builder.calls >= 3


async def _async_none():
    return None


@pytest.mark.asyncio
async def test_refresh_position_marks_close_confirmed_when_position_is_flat():
    service = LiveHeatmapService()
    service.position_tracker = PositionTracker("BTCUSDT")

    class FakeAccount:
        async def fetch_position(self, symbol):
            return PositionState(symbol=symbol, amount=0.0, entry_price=0.0)

    class FakeExecutor:
        def __init__(self):
            self.close_pending = True
            self.close_confirmed = 0

        def mark_close_confirmed(self):
            self.close_confirmed += 1
            self.close_pending = False

    service.account_client = FakeAccount()
    service.order_executor = FakeExecutor()

    await service._refresh_position("BTCUSDT", now_ms=1000)

    assert service.order_executor.close_pending is False
    assert service.order_executor.close_confirmed == 1


@pytest.mark.asyncio
async def test_position_polling_uses_fallback_interval():
    """REST fallback poll fires only at POSITION_REST_FALLBACK_INTERVAL_MS cadence.

    User-data WebSocket is now the primary source of position updates, so the
    REST endpoint is throttled down to a safe rate well under Binance's
    per-IP weight limit.
    """
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.position_tracker = PositionTracker("BTCUSDT")
    service._last_position_poll_ms = 1_000

    class FakeAccount:
        def __init__(self):
            self.calls = 0

        async def fetch_position(self, symbol):
            self.calls += 1
            return PositionState(symbol=symbol, amount=0.0, entry_price=0.0)

    service.account_client = FakeAccount()

    assert POSITION_REST_FALLBACK_INTERVAL_MS > FRAME_INTERVAL_MS
    # Frame-loop cadence must NOT trigger a REST poll any more.
    await service._poll_position_if_due(1_000 + FRAME_INTERVAL_MS)
    assert service.account_client.calls == 0
    # Just before the fallback window — still no call.
    await service._poll_position_if_due(
        1_000 + POSITION_REST_FALLBACK_INTERVAL_MS - 1
    )
    assert service.account_client.calls == 0
    # At the fallback window — one REST call.
    await service._poll_position_if_due(
        1_000 + POSITION_REST_FALLBACK_INTERVAL_MS
    )
    assert service.account_client.calls == 1
    # Subsequent calls within another fallback window are throttled.
    await service._poll_position_if_due(
        1_000 + POSITION_REST_FALLBACK_INTERVAL_MS + 1
    )
    assert service.account_client.calls == 1


@pytest.mark.asyncio
async def test_user_data_account_update_pushes_position_state():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.position_tracker = PositionTracker("BTCUSDT")
    captured = []

    async def capture(payload):
        captured.append(payload)

    service.broadcast = capture

    event = {
        "e": "ACCOUNT_UPDATE",
        "E": 1_700_000_000_500,
        "a": {
            "P": [
                {
                    "s": "BTCUSDT",
                    "pa": "0.01",
                    "ep": "65000.0",
                    "bep": "65010.0",
                    "up": "1.25",
                    "ps": "BOTH",
                }
            ]
        },
    }
    from app.position import parse_account_update_positions

    positions = parse_account_update_positions(event)
    await service._on_user_data_account_update(positions, event)

    assert service.current_position is not None
    assert service.current_position.side == "LONG"
    assert service.current_position.quantity == 0.01
    # User-data push must suppress an immediate REST fallback.
    assert service._last_position_poll_ms == 1_700_000_000_500
    # And it must broadcast a position event.
    assert any(event["type"] == "position" for event in captured)


@pytest.mark.asyncio
async def test_user_data_account_update_confirms_flat_close():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.position_tracker = PositionTracker("BTCUSDT")
    service.position_tracker.current = PositionState(
        symbol="BTCUSDT",
        amount=0.01,
        entry_price=65000.0,
    )
    service.current_position = service.position_tracker.current

    async def noop(payload):
        return None

    service.broadcast = noop

    class FakeExecutor:
        def __init__(self):
            self.close_pending = True
            self.cancel_calls: list[str] = []
            self.close_confirmed = 0

        def mark_close_confirmed(self):
            self.close_pending = False
            self.close_confirmed += 1

        async def cancel_all_open_orders(self, symbol):
            self.cancel_calls.append(symbol)

    service.order_executor = FakeExecutor()

    flat_event = {
        "e": "ACCOUNT_UPDATE",
        "E": 1_700_000_001_000,
        "a": {
            "P": [
                {
                    "s": "BTCUSDT",
                    "pa": "0.0",
                    "ep": "0.0",
                    "bep": "0.0",
                    "up": "0.0",
                    "ps": "BOTH",
                }
            ]
        },
    }
    from app.position import parse_account_update_positions

    await service._on_user_data_account_update(
        parse_account_update_positions(flat_event),
        flat_event,
    )

    assert service.current_position is None
    assert service.order_executor.close_pending is False
    assert service.order_executor.close_confirmed == 1
    assert service.order_executor.cancel_calls == ["BTCUSDT"]


@pytest.mark.asyncio
async def test_user_data_account_update_ignores_other_symbols():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.position_tracker = PositionTracker("BTCUSDT")

    async def noop(payload):
        return None

    service.broadcast = noop

    event = {
        "e": "ACCOUNT_UPDATE",
        "E": 1_700_000_002_000,
        "a": {
            "P": [
                {
                    "s": "ETHUSDT",
                    "pa": "-0.1",
                    "ep": "3200.0",
                    "ps": "BOTH",
                }
            ]
        },
    }
    from app.position import parse_account_update_positions

    await service._on_user_data_account_update(
        parse_account_update_positions(event),
        event,
    )

    assert service.current_position is None


@pytest.mark.asyncio
async def test_exit_uses_current_book_mark_for_max_loss():
    service = LiveHeatmapService()
    service.session.assistant_settings = AssistantRiskSettings(
        auto_exit_enabled=True,
        max_loss_usdt=5.0,
    )
    service.current_position = PositionState(
        symbol="BTCUSDT",
        amount=1.0,
        entry_price=100.0,
        unrealized_pnl=0.0,
    )
    service.book.bids = {94.0: 1.0}
    service.book.asks = {94.5: 1.0}

    class FakeExecutor:
        close_pending = False

        def __init__(self):
            self.calls = []

        async def close_position(self, position):
            self.calls.append(position)
            return {"status": "NEW"}

    service.order_executor = FakeExecutor()

    decision = await service._evaluate_exit(now_ms=1000)

    assert decision.reason == "max_loss"
    assert service.order_executor.calls == [service.current_position]


@pytest.mark.asyncio
async def test_auto_trade_opens_long_on_three_of_four_confluence():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.session.assistant_settings = AssistantRiskSettings(
        auto_trade_enabled=True,
        trade_notional_usdt=13.0,
    )
    service.current_position = None
    service.trading_state = "ARMED"
    service.client = SimpleNamespace(quantity_step=0.001, min_quantity=0.001)
    service.book.bids = {64999.0: 1.0}
    service.book.asks = {65000.0: 1.0}
    # 3-of-4 BUY confluence (gate still warming). User requirement: open
    # market LONG when any 3 of 4 exchanges agree.
    service._last_entry_results = {
        "binance": {
            "market_state": "READY",
            "long_filter": "OK",
            "short_filter": "BLOCKED",
        },
        "bybit": {
            "market_state": "READY",
            "long_filter": "OK",
            "short_filter": "BLOCKED",
        },
        "okx": {
            "market_state": "READY",
            "long_filter": "OK",
            "short_filter": "BLOCKED",
        },
        "gate": {
            "market_state": "WARMING",
            "long_filter": "WAIT",
            "short_filter": "WAIT",
        },
    }

    class FakeExecutor:
        open_pending = False

        def __init__(self):
            self.calls = []

        async def open_position(
            self,
            symbol,
            *,
            side,
            notional_usdt,
            mark_price,
            quantity_step=None,
            min_quantity=None,
        ):
            self.calls.append(
                (
                    symbol,
                    side,
                    notional_usdt,
                    mark_price,
                    quantity_step,
                    min_quantity,
                )
            )
            self.open_pending = True
            return {"status": "NEW", "clientOrderId": "open-1"}

    service.order_executor = FakeExecutor()
    events = []

    async def capture(payload):
        events.append(payload)

    service.broadcast = capture

    await service._evaluate_auto_trade(now_ms=1000)

    assert service.order_executor.calls == [
        ("BTCUSDT", "LONG", 13.0, 65000.0, 0.001, 0.001)
    ]
    assert events[-1]["type"] == "order_status"
    assert events[-1]["side"] == "LONG"


@pytest.mark.asyncio
async def test_auto_trade_refreshes_position_before_opening():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.session.assistant_settings = AssistantRiskSettings(auto_trade_enabled=True)
    service.current_position = None
    service.trading_state = "ARMED"
    service.position_tracker = PositionTracker("BTCUSDT")
    service.client = SimpleNamespace(quantity_step=0.001, min_quantity=0.001)
    service.book.bids = {64999.0: 1.0}
    service.book.asks = {65000.0: 1.0}
    service._last_entry_results = {
        "binance": {
            "market_state": "READY",
            "long_filter": "OK",
            "short_filter": "BLOCKED",
        },
        "bybit": {
            "market_state": "READY",
            "long_filter": "OK",
            "short_filter": "BLOCKED",
        },
        "okx": {
            "market_state": "READY",
            "long_filter": "OK",
            "short_filter": "BLOCKED",
        },
        "gate": {
            "market_state": "WARMING",
            "long_filter": "WAIT",
            "short_filter": "WAIT",
        },
    }

    class FakeAccount:
        async def fetch_position(self, symbol):
            return PositionState(symbol=symbol, amount=0.01, entry_price=65000)

    class FakeExecutor:
        open_pending = False

        def __init__(self):
            self.calls = []

        async def open_position(self, symbol, **kwargs):
            self.calls.append((symbol, kwargs))
            return {"status": "NEW"}

        def mark_open_confirmed(self):
            return None

    service.account_client = FakeAccount()
    service.order_executor = FakeExecutor()

    await service._evaluate_auto_trade(now_ms=1000)

    assert service.current_position is not None
    assert service.order_executor.calls == []


@pytest.mark.asyncio
async def test_auto_trade_does_not_open_without_confluence_or_when_position_open():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.session.assistant_settings = AssistantRiskSettings(auto_trade_enabled=True)
    service.trading_state = "ARMED"
    service.book.bids = {64999.0: 1.0}
    service.book.asks = {65000.0: 1.0}
    service._last_entry_results = {
        "binance": {
            "market_state": "READY",
            "long_filter": "OK",
            "short_filter": "BLOCKED",
        },
        "bybit": {
            "market_state": "READY",
            "long_filter": "BLOCKED",
            "short_filter": "OK",
        },
    }

    class FakeExecutor:
        open_pending = False

        def __init__(self):
            self.calls = []

        async def open_position(self, symbol, *, side, notional_usdt, mark_price):
            self.calls.append((symbol, side, notional_usdt, mark_price))
            return {"status": "NEW"}

    service.order_executor = FakeExecutor()

    await service._evaluate_auto_trade(now_ms=1000)

    assert service.order_executor.calls == []


@pytest.mark.asyncio
async def test_start_trading_waits_for_warm_entry_filters_before_arming():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")

    class FakeAccount:
        async def fetch_position(self, symbol):
            return None

    service.account_client = FakeAccount()
    service.order_executor = SimpleNamespace()
    service.position_tracker = PositionTracker("BTCUSDT")
    service.client = SimpleNamespace()
    service.bybit_client = SimpleNamespace()
    service._last_entry_results = {
        "binance": {"market_state": "WARMING", "long_filter": "WAIT", "short_filter": "WAIT"},
        "bybit": {"market_state": "READY", "long_filter": "WAIT", "short_filter": "WAIT"},
        "okx": {"market_state": "WARMING", "long_filter": "WAIT", "short_filter": "WAIT"},
        "gate": {"market_state": "WARMING", "long_filter": "WAIT", "short_filter": "WAIT"},
    }

    event = await service.start_trading(now_ms=1000)

    # Only 1 of 4 warmed (bybit). Need >=3 warmed to arm.
    assert event["state"] == "WARMING"
    assert service.session.assistant_settings.auto_trade_enabled is True

    service._last_entry_results["binance"]["market_state"] = "READY"
    event = service._refresh_trading_readiness(now_ms=1100)
    # 2 of 4 warmed: still WARMING.
    assert event["state"] == "WARMING"

    service._last_entry_results["okx"]["market_state"] = "READY"
    event = service._refresh_trading_readiness(now_ms=1200)
    # 3 of 4 warmed: ARM.
    assert event["state"] == "ARMED"


def test_trading_readiness_arms_when_filters_are_warmed_but_waiting():
    service = LiveHeatmapService()
    service._last_entry_results = {
        "binance": {
            "market_state": "READY",
            "long_filter": "WAIT",
            "short_filter": "WAIT",
            "reason": "no_signal",
        },
        "bybit": {
            "market_state": "RISKY",
            "long_filter": "WAIT",
            "short_filter": "WAIT",
            "reason": "adaptive_elevated_vpin",
        },
        "okx": {
            "market_state": "READY",
            "long_filter": "WAIT",
            "short_filter": "WAIT",
            "reason": "no_signal",
        },
        "gate": {
            "market_state": "WARMING",
            "long_filter": "WAIT",
            "short_filter": "WAIT",
            "reason": "warming 5/10 buckets, 10/50 trades",
        },
    }

    event = service._refresh_trading_readiness(now_ms=1000)

    # 3 of 4 are out of WARMING (binance READY, bybit RISKY, okx READY)
    # — confluence-readiness met. The risky / warming exchanges block
    # the actual entry (no 3-of-4 same-side OK), but the trader is ARMED.
    assert event["state"] == "ARMED"
    assert "binance READY no_signal" in event["message"]
    assert "bybit RISKY adaptive_elevated_vpin" in event["message"]
    assert "okx READY no_signal" in event["message"]
    assert "gate WARMING" in event["message"]


def test_trading_readiness_reports_warming_exchange_progress():
    service = LiveHeatmapService()
    service._last_entry_results = {
        "binance": {
            "market_state": "WARMING",
            "long_filter": "WAIT",
            "short_filter": "WAIT",
            "reason": "warming 3/10 buckets, 80/200 trades",
        },
        "bybit": {
            "market_state": "READY",
            "long_filter": "WAIT",
            "short_filter": "WAIT",
            "reason": "no_signal",
        },
        "okx": {
            "market_state": "WARMING",
            "long_filter": "WAIT",
            "short_filter": "WAIT",
            "reason": "warming 0/10 buckets",
        },
        "gate": {
            "market_state": "WARMING",
            "long_filter": "WAIT",
            "short_filter": "WAIT",
            "reason": "warming 0/10 buckets",
        },
    }

    event = service._refresh_trading_readiness(now_ms=1000)

    # Only 1 of 4 warmed (bybit) -> still WARMING.
    assert event["state"] == "WARMING"
    assert "binance WARMING warming 3/10 buckets, 80/200 trades" in event["message"]
    assert "bybit READY no_signal" in event["message"]


@pytest.mark.asyncio
async def test_stop_trading_blocks_new_entries_without_disabling_auto_exit():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.session.assistant_settings = AssistantRiskSettings(
        auto_trade_enabled=True,
        auto_exit_enabled=True,
    )
    service.trading_state = "ARMED"

    event = await service.stop_trading()

    assert event["state"] == "STOPPED"
    assert service.session.assistant_settings.auto_trade_enabled is False
    assert service.session.assistant_settings.auto_exit_enabled is True


@pytest.mark.asyncio
async def test_auto_trade_is_blocked_until_trading_is_armed():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.session.assistant_settings = AssistantRiskSettings(auto_trade_enabled=True)
    service.trading_state = "STOPPED"
    service.client = SimpleNamespace(quantity_step=0.001, min_quantity=0.001)
    service.book.bids = {64999.0: 1.0}
    service.book.asks = {65000.0: 1.0}
    service._last_entry_results = {
        "binance": {"market_state": "READY", "long_filter": "OK", "short_filter": "BLOCKED"},
        "bybit": {"market_state": "READY", "long_filter": "OK", "short_filter": "BLOCKED"},
    }

    class FakeExecutor:
        open_pending = False

        def __init__(self):
            self.calls = []

        async def open_position(self, symbol, **kwargs):
            self.calls.append((symbol, kwargs))
            return {"status": "NEW"}

    service.order_executor = FakeExecutor()

    await service._evaluate_auto_trade(now_ms=1000)

    assert service.order_executor.calls == []


@pytest.mark.asyncio
async def test_flat_close_confirmation_cancels_orders_and_starts_cooldown():
    service = LiveHeatmapService()
    service.position_tracker = PositionTracker("BTCUSDT")
    service.trading_state = "IN_POSITION"

    class FakeAccount:
        async def fetch_position(self, symbol):
            return PositionState(symbol=symbol, amount=0.0, entry_price=0.0)

    class FakeExecutor:
        def __init__(self):
            self.close_pending = True
            self.close_confirmed = 0
            self.cancel_calls = []

        def mark_close_confirmed(self):
            self.close_confirmed += 1
            self.close_pending = False

        async def cancel_all_open_orders(self, symbol):
            self.cancel_calls.append(symbol)
            return {"code": 200}

    service.account_client = FakeAccount()
    service.order_executor = FakeExecutor()

    await service._refresh_position("BTCUSDT", now_ms=1000)

    assert service.order_executor.close_confirmed == 1
    assert service.order_executor.cancel_calls == ["BTCUSDT"]
    assert service.trading_state == "COOLDOWN"
    assert service.cooldown_until_ms == 31_000


@pytest.mark.asyncio
async def test_auto_trade_is_blocked_during_cooldown():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.session.assistant_settings = AssistantRiskSettings(auto_trade_enabled=True)
    service.trading_state = "COOLDOWN"
    service.cooldown_until_ms = 31_000
    service.client = SimpleNamespace(quantity_step=0.001, min_quantity=0.001)
    service.book.bids = {64999.0: 1.0}
    service.book.asks = {65000.0: 1.0}
    service._last_entry_results = {
        "binance": {"market_state": "READY", "long_filter": "OK", "short_filter": "BLOCKED"},
        "bybit": {"market_state": "READY", "long_filter": "OK", "short_filter": "BLOCKED"},
    }

    class FakeExecutor:
        open_pending = False

        def __init__(self):
            self.calls = []

        async def open_position(self, symbol, **kwargs):
            self.calls.append((symbol, kwargs))
            return {"status": "NEW"}

    service.order_executor = FakeExecutor()

    await service._evaluate_auto_trade(now_ms=30_999)

    assert service.order_executor.calls == []


@pytest.mark.asyncio
async def test_emergency_flatten_cancels_orders_and_closes_open_position():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.current_position = PositionState(
        symbol="BTCUSDT",
        amount=0.01,
        entry_price=65000,
    )

    class FakeExecutor:
        close_pending = False

        def __init__(self):
            self.cancel_calls = []
            self.close_calls = []

        async def cancel_all_open_orders(self, symbol):
            self.cancel_calls.append(symbol)
            return {"code": 200}

        async def close_position(self, position):
            self.close_calls.append(position)
            self.close_pending = True
            return {"status": "NEW", "clientOrderId": "close-1"}

    service.order_executor = FakeExecutor()

    event = await service.emergency_flatten(now_ms=2000)

    assert event["state"] == "STOPPED"
    assert service.order_executor.cancel_calls == ["BTCUSDT"]
    assert service.order_executor.close_calls == [service.current_position]


@pytest.mark.asyncio
async def test_on_gate_book_update_uses_correct_kwargs_for_adaptive_market():
    """Regression: _on_gate_book_update must call on_top_of_book with bid_vol/ask_vol
    and timestamp_ms — not bid_volume/ask_volume/event_time_ms. The wrong kwargs
    raise TypeError on the first book event, which would silently kill the Gate
    WS stream task and leave the indicator stuck in WARMING with 0 trades."""
    service = LiveHeatmapService()
    service.gate_adaptive_market = AdaptiveMarketService("BTC_USDT")
    from app.order_book import OrderBook

    book = OrderBook()
    book.bids[80000.0] = 3.0
    book.asks[80001.0] = 1.0

    # Must not raise — bug was: wrong kwargs raised TypeError, killing stream.
    service._on_gate_book_update(book, event_time_ms=1_700_000_000_000)

    # OBI = (bid_vol - ask_vol) / (bid_vol + ask_vol) = (3 - 1) / 4 = 0.5
    assert service.gate_adaptive_market.last_obi == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_on_gate_market_trade_increments_trade_count():
    service = LiveHeatmapService()
    service.gate_adaptive_market = AdaptiveMarketService("BTC_USDT")

    service._on_gate_market_trade(
        {
            "symbol": "BTC_USDT",
            "price": 80000.0,
            "qty": 1.0,
            "timestamp": 1_700_000_000_000,
            "is_buyer_maker": False,
        }
    )
    service._on_gate_market_trade(
        {
            "symbol": "BTC_USDT",
            "price": 80001.0,
            "qty": 0.5,
            "timestamp": 1_700_000_000_500,
            "is_buyer_maker": True,
        }
    )

    assert service.gate_adaptive_market.trade_count == 2


# ---------------------------------------------------------------------------
# 3-of-4 confluence entry / exit (user requirement: open at market when any
# three of BIN/BYB/OKX/GATE agree, close when the same-side count drops below
# three or any exchange flips to TOXIC/RISKY/opposite).
# ---------------------------------------------------------------------------

_BUY_RESULT = {"market_state": "READY", "long_filter": "OK", "short_filter": "BLOCKED"}
_SELL_RESULT = {"market_state": "READY", "long_filter": "BLOCKED", "short_filter": "OK"}
_WAIT_RESULT = {
    "market_state": "READY",
    "long_filter": "WAIT",
    "short_filter": "WAIT",
    "reason": "no_signal",
}
_WARMING_RESULT = {
    "market_state": "WARMING",
    "long_filter": "WAIT",
    "short_filter": "WAIT",
}
_TOXIC_RESULT = {"market_state": "TOXIC", "reason": "toxic_vpin_watch_only"}
_RISKY_RESULT = {
    "market_state": "RISKY",
    "long_filter": "WAIT",
    "short_filter": "WAIT",
    "reason": "adaptive_elevated_vpin",
}


def test_confluence_entry_side_long_with_three_of_four():
    assert (
        _confluence_entry_side(
            {
                "binance": _BUY_RESULT,
                "bybit": _BUY_RESULT,
                "okx": _BUY_RESULT,
                "gate": _WARMING_RESULT,
            }
        )
        == "LONG"
    )


def test_confluence_entry_side_long_with_all_four():
    assert (
        _confluence_entry_side(
            {
                "binance": _BUY_RESULT,
                "bybit": _BUY_RESULT,
                "okx": _BUY_RESULT,
                "gate": _BUY_RESULT,
            }
        )
        == "LONG"
    )


def test_confluence_entry_side_short_with_three_of_four():
    assert (
        _confluence_entry_side(
            {
                "binance": _SELL_RESULT,
                "bybit": _SELL_RESULT,
                "okx": _SELL_RESULT,
                "gate": _WAIT_RESULT,
            }
        )
        == "SHORT"
    )


def test_confluence_entry_side_two_of_four_does_not_enter():
    assert (
        _confluence_entry_side(
            {
                "binance": _BUY_RESULT,
                "bybit": _BUY_RESULT,
                "okx": _WAIT_RESULT,
                "gate": _WAIT_RESULT,
            }
        )
        is None
    )


def test_confluence_entry_side_opposite_signal_blocks_entry():
    assert (
        _confluence_entry_side(
            {
                "binance": _BUY_RESULT,
                "bybit": _BUY_RESULT,
                "okx": _BUY_RESULT,
                "gate": _SELL_RESULT,
            }
        )
        is None
    )


def test_confluence_entry_side_any_toxic_blocks_entry_even_with_three_buy():
    assert (
        _confluence_entry_side(
            {
                "binance": _BUY_RESULT,
                "bybit": _BUY_RESULT,
                "okx": _BUY_RESULT,
                "gate": _TOXIC_RESULT,
            }
        )
        is None
    )


def test_confluence_entry_side_any_risky_blocks_entry():
    assert (
        _confluence_entry_side(
            {
                "binance": _BUY_RESULT,
                "bybit": _BUY_RESULT,
                "okx": _BUY_RESULT,
                "gate": _RISKY_RESULT,
            }
        )
        is None
    )


def test_confluence_entry_side_empty_results_is_none():
    assert _confluence_entry_side({}) is None


def test_confluence_exit_reason_none_when_three_still_agree():
    assert (
        _confluence_exit_reason(
            "LONG",
            {
                "binance": _BUY_RESULT,
                "bybit": _BUY_RESULT,
                "okx": _BUY_RESULT,
                "gate": _WAIT_RESULT,
            },
        )
        is None
    )


def test_confluence_exit_reason_fires_when_count_falls_below_three():
    # Was 3-of-4 BUY, one exchange dropped to WAIT — confluence lost.
    assert (
        _confluence_exit_reason(
            "LONG",
            {
                "binance": _BUY_RESULT,
                "bybit": _BUY_RESULT,
                "okx": _WAIT_RESULT,
                "gate": _WAIT_RESULT,
            },
        )
        == "confluence_exit_lost"
    )


def test_confluence_exit_reason_fires_on_opposite_signal():
    # Even with 3 still BUY, one opposite SELL is an exit signal per user spec.
    assert (
        _confluence_exit_reason(
            "LONG",
            {
                "binance": _BUY_RESULT,
                "bybit": _BUY_RESULT,
                "okx": _BUY_RESULT,
                "gate": _SELL_RESULT,
            },
        )
        == "confluence_exit_opposite_signal"
    )


def test_confluence_exit_reason_fires_on_any_toxic_exchange():
    reason = _confluence_exit_reason(
        "LONG",
        {
            "binance": _BUY_RESULT,
            "bybit": _BUY_RESULT,
            "okx": _BUY_RESULT,
            "gate": _TOXIC_RESULT,
        },
    )
    assert reason is not None
    assert reason.startswith("confluence_exit_risk:")
    assert "gate" in reason


def test_confluence_exit_reason_fires_on_any_risky_exchange():
    reason = _confluence_exit_reason(
        "LONG",
        {
            "binance": _BUY_RESULT,
            "bybit": _BUY_RESULT,
            "okx": _RISKY_RESULT,
            "gate": _BUY_RESULT,
        },
    )
    assert reason is not None
    assert reason.startswith("confluence_exit_risk:")
    assert "okx" in reason


def test_confluence_exit_reason_mirror_for_short():
    # Same logic for SHORT side: opposite (long) signal triggers exit.
    assert (
        _confluence_exit_reason(
            "SHORT",
            {
                "binance": _SELL_RESULT,
                "bybit": _SELL_RESULT,
                "okx": _SELL_RESULT,
                "gate": _BUY_RESULT,
            },
        )
        == "confluence_exit_opposite_signal"
    )


def test_confluence_exit_reason_none_for_unknown_side():
    assert (
        _confluence_exit_reason(
            None,
            {
                "binance": _BUY_RESULT,
                "bybit": _BUY_RESULT,
                "okx": _BUY_RESULT,
                "gate": _BUY_RESULT,
            },
        )
        is None
    )


@pytest.mark.asyncio
async def test_evaluate_exit_closes_position_when_confluence_collapses():
    """Open LONG; one exchange flips to TOXIC; market close fires immediately."""
    from app.exit_engine import ExitEngine

    service = LiveHeatmapService()
    service.session.assistant_settings = AssistantRiskSettings(
        auto_trade_enabled=True,
    )
    service.current_position = PositionState(
        symbol="BTCUSDT",
        amount=0.01,
        entry_price=65000,

    )
    service.exit_engine = ExitEngine()
    service._last_entry_results = {
        "binance": _BUY_RESULT,
        "bybit": _BUY_RESULT,
        "okx": _BUY_RESULT,
        "gate": _TOXIC_RESULT,  # newly toxic — should trigger exit
    }
    events = []

    async def capture(payload):
        events.append(payload)

    service.broadcast = capture

    class FakeExecutor:
        close_pending = False

        def __init__(self):
            self.calls = []

        async def close_position(self, position):
            self.calls.append(position)
            return {"status": "NEW", "clientOrderId": "close-1"}

    service.order_executor = FakeExecutor()

    decision = await service._evaluate_exit(now_ms=1000)

    assert decision.should_close is True
    assert decision.hard_exit is True
    assert decision.reason is not None
    assert decision.reason.startswith("confluence_exit_risk:")
    assert len(service.order_executor.calls) == 1
    # First broadcast is the exit_status, second is the order_status.
    assert events[0]["type"] == "exit_status"
    assert events[0]["should_close"] is True
    assert events[1]["type"] == "order_status"


@pytest.mark.asyncio
async def test_evaluate_exit_does_not_fire_confluence_when_auto_trade_off():
    """Confluence-exit is gated by auto_trade_enabled. Manual entries are
    not auto-closed on signal degradation."""
    from app.exit_engine import ExitEngine

    service = LiveHeatmapService()
    service.session.assistant_settings = AssistantRiskSettings(
        auto_trade_enabled=False,
        auto_exit_enabled=False,
    )
    service.current_position = PositionState(
        symbol="BTCUSDT",
        amount=0.01,
        entry_price=65000,

    )
    service.exit_engine = ExitEngine()
    service._last_entry_results = {
        "binance": _BUY_RESULT,
        "bybit": _BUY_RESULT,
        "okx": _BUY_RESULT,
        "gate": _TOXIC_RESULT,
    }

    class FakeExecutor:
        close_pending = False

        def __init__(self):
            self.calls = []

        async def close_position(self, position):
            self.calls.append(position)
            return {"status": "NEW"}

    service.order_executor = FakeExecutor()

    async def capture(payload):
        pass

    service.broadcast = capture

    decision = await service._evaluate_exit(now_ms=1000)

    # Confluence reason ignored because auto_trade is off.
    assert decision.reason is None or not decision.reason.startswith(
        "confluence_exit"
    )
    assert service.order_executor.calls == []


@pytest.mark.asyncio
async def test_evaluate_exit_closes_on_opposite_signal_under_auto_trade():
    """User: 'смена сигнала на противоположный → закрытие по рынку'."""
    from app.exit_engine import ExitEngine

    service = LiveHeatmapService()
    service.session.assistant_settings = AssistantRiskSettings(
        auto_trade_enabled=True,
    )
    service.current_position = PositionState(
        symbol="BTCUSDT",
        amount=0.01,
        entry_price=65000,

    )
    service.exit_engine = ExitEngine()
    service._last_entry_results = {
        "binance": _BUY_RESULT,
        "bybit": _BUY_RESULT,
        "okx": _BUY_RESULT,
        "gate": _SELL_RESULT,  # flipped opposite
    }

    class FakeExecutor:
        close_pending = False

        def __init__(self):
            self.calls = []

        async def close_position(self, position):
            self.calls.append(position)
            return {"status": "NEW"}

    service.order_executor = FakeExecutor()

    async def capture(payload):
        pass

    service.broadcast = capture

    decision = await service._evaluate_exit(now_ms=1000)

    assert decision.should_close is True
    assert decision.reason == "confluence_exit_opposite_signal"
    assert len(service.order_executor.calls) == 1


@pytest.mark.asyncio
async def test_evaluate_exit_closes_when_signal_disappears():
    """User: 'при пропадании сигнала → закрытие по рынку'."""
    from app.exit_engine import ExitEngine

    service = LiveHeatmapService()
    service.session.assistant_settings = AssistantRiskSettings(
        auto_trade_enabled=True,
    )
    service.current_position = PositionState(
        symbol="BTCUSDT",
        amount=0.01,
        entry_price=65000,

    )
    service.exit_engine = ExitEngine()
    service._last_entry_results = {
        "binance": _BUY_RESULT,
        "bybit": _BUY_RESULT,
        "okx": _WAIT_RESULT,  # signal disappeared
        "gate": _WAIT_RESULT,
    }

    class FakeExecutor:
        close_pending = False

        def __init__(self):
            self.calls = []

        async def close_position(self, position):
            self.calls.append(position)
            return {"status": "NEW"}

    service.order_executor = FakeExecutor()

    async def capture(payload):
        pass

    service.broadcast = capture

    decision = await service._evaluate_exit(now_ms=1000)

    assert decision.should_close is True
    assert decision.reason == "confluence_exit_lost"
    assert len(service.order_executor.calls) == 1


@pytest.mark.asyncio
async def test_auto_trade_does_not_open_on_two_of_four_confluence():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.session.assistant_settings = AssistantRiskSettings(
        auto_trade_enabled=True,
    )
    service.current_position = None
    service.trading_state = "ARMED"
    service.client = SimpleNamespace(quantity_step=0.001, min_quantity=0.001)
    service.book.bids = {64999.0: 1.0}
    service.book.asks = {65000.0: 1.0}
    service._last_entry_results = {
        "binance": _BUY_RESULT,
        "bybit": _BUY_RESULT,
        "okx": _WAIT_RESULT,
        "gate": _WAIT_RESULT,
    }

    class FakeExecutor:
        open_pending = False

        def __init__(self):
            self.calls = []

        async def open_position(self, symbol, **kwargs):
            self.calls.append((symbol, kwargs))
            return {"status": "NEW"}

    service.order_executor = FakeExecutor()

    async def capture(payload):
        pass

    service.broadcast = capture

    await service._evaluate_auto_trade(now_ms=1000)

    # 2 of 4 BUY is below the 3-of-4 threshold; no order fired.
    assert service.order_executor.calls == []


@pytest.mark.asyncio
async def test_auto_trade_does_not_open_when_any_exchange_toxic():
    service = LiveHeatmapService()
    service.session.mark_synced("BTCUSDT")
    service.session.assistant_settings = AssistantRiskSettings(
        auto_trade_enabled=True,
    )
    service.current_position = None
    service.trading_state = "ARMED"
    service.client = SimpleNamespace(quantity_step=0.001, min_quantity=0.001)
    service.book.bids = {64999.0: 1.0}
    service.book.asks = {65000.0: 1.0}
    service._last_entry_results = {
        "binance": _BUY_RESULT,
        "bybit": _BUY_RESULT,
        "okx": _BUY_RESULT,
        "gate": _TOXIC_RESULT,
    }

    class FakeExecutor:
        open_pending = False

        def __init__(self):
            self.calls = []

        async def open_position(self, symbol, **kwargs):
            self.calls.append((symbol, kwargs))
            return {"status": "NEW"}

    service.order_executor = FakeExecutor()

    async def capture(payload):
        pass

    service.broadcast = capture

    await service._evaluate_auto_trade(now_ms=1000)

    # 3 BUY + 1 TOXIC: TOXIC blocks entry.
    assert service.order_executor.calls == []


# ---------------------------------------------------------------------------
# Signed VPIN / toxic flow direction surfacing (data-only; trading logic is
# unchanged by signed VPIN -- it is exposed for diagnostics so the UI can
# render an arrow showing which side a TOXIC / RISKY regime is on).
# ---------------------------------------------------------------------------


def test_toxic_direction_is_none_outside_risk_states():
    """READY / WARMING / NORMAL never report a toxic_direction, regardless of
    signed_vpin magnitude -- the arrow only makes sense within RISK regimes."""
    for state in ("READY", "WARMING", "NORMAL"):
        result = {
            "market_state": state,
            "signed_vpin": 0.9,  # strongly directional but not toxic
        }
        assert _toxic_direction(result) is None


def test_toxic_direction_returns_buy_when_signed_vpin_positive_in_toxic():
    result = {"market_state": "TOXIC", "signed_vpin": 0.42}
    assert _toxic_direction(result) == "BUY"


def test_toxic_direction_returns_sell_when_signed_vpin_negative_in_risky():
    result = {"market_state": "RISKY", "signed_vpin": -0.18}
    assert _toxic_direction(result) == "SELL"


def test_toxic_direction_dead_zone_around_zero():
    """When |signed_vpin| is below the threshold, no direction is reported
    even in TOXIC. Volatility-driven toxicity with balanced flow is possible."""
    threshold = TOXIC_DIRECTION_MIN_SIGNED_VPIN
    just_below = {"market_state": "TOXIC", "signed_vpin": threshold - 0.001}
    just_above = {"market_state": "TOXIC", "signed_vpin": threshold + 0.001}
    assert _toxic_direction(just_below) is None
    assert _toxic_direction(just_above) == "BUY"
    assert _toxic_direction({"market_state": "TOXIC", "signed_vpin": 0.0}) is None


def test_toxic_direction_handles_missing_signed_vpin_field():
    """Older payloads without signed_vpin must not blow up the helper."""
    result = {"market_state": "TOXIC", "reason": "toxic_vpin"}
    assert _toxic_direction(result) is None


def test_entry_result_payload_includes_signed_vpin_and_toxic_direction():
    """The payload helper used both for broadcasts and for _last_entry_results
    must surface the new fields so confluence and the UI see the same view."""
    result = SimpleNamespace(
        market_state="TOXIC",
        long_filter="BLOCKED",
        short_filter="BLOCKED",
        reason="toxic_vpin",
        latest_signal_type=None,
        latest_signal_confidence=None,
        vpin=0.95,
        signed_vpin=0.6,
    )
    payload = _entry_result_payload(result)

    assert payload["signed_vpin"] == 0.6
    assert payload["toxic_direction"] == "BUY"
    # The unsigned vpin is preserved (no shadowing).
    assert payload["vpin"] == 0.95


def test_entry_result_payload_omits_direction_when_ready():
    result = SimpleNamespace(
        market_state="READY",
        long_filter="OK",
        short_filter="WAIT",
        reason="sell_exhaustion",
        latest_signal_type="SELL_EXHAUSTION",
        latest_signal_confidence=0.7,
        vpin=0.1,
        signed_vpin=0.8,  # high but not in a risk regime => no arrow
    )
    payload = _entry_result_payload(result)

    assert payload["signed_vpin"] == 0.8
    assert payload["toxic_direction"] is None
