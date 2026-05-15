from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import replace
from typing import Any

from fastapi import WebSocket

from app.binance_client import BinanceMarketClient
from app.bybit_client import BybitMarketClient
from app.coinbase_client import CoinbaseMarketClient
from app.kraken_client import KrakenMarketClient
from app.exchange_symbols import to_coinbase_product, to_kraken_pair
from app.assistant_config import AssistantRiskSettings, load_default_risk_settings
from app.assistant_config import load_binance_account_settings
from app.adaptive_service import AdaptiveMarketService
from app.binance_account import BinanceAccountClient
from app.binance_user_data import BinanceUserDataClient
from app.entry_filter import AdaptiveVpinRegime, EntryFilterEngine
from app.exit_engine import ExitEngine
from app.order_executor import OrderExecutor
from app.position import PositionState
from app.position_tracker import PositionTracker
from app.frame_builder import FrameBuilder
from app.order_book import OrderBook
from app.settings import (
    FRAME_INTERVAL_MS,
    HEATMAP_BUFFER_LEVELS,
    HEATMAP_HEIGHT,
    HEATMAP_RECENTER_MARGIN_LEVELS,
    POSITION_REST_FALLBACK_INTERVAL_MS,
)
from app.trade_buffer import TradeBuffer


TRADING_COOLDOWN_MS = 30_000
TRADING_ACTIVE_STATES = {"WARMING", "ARMED", "IN_POSITION", "COOLDOWN"}


class BrowserSession:
    def __init__(self) -> None:
        self.state = "disconnected"
        self.symbol: str | None = None
        self.compression = 1
        self.assistant_settings = load_default_risk_settings()

    def mark_connecting(self, symbol: str, compression: int = 1) -> None:
        self.symbol = symbol
        self.compression = max(1, int(compression))
        self.state = "connecting"

    def mark_synced(self, symbol: str, compression: int | None = None) -> None:
        self.symbol = symbol
        if compression is not None:
            self.compression = max(1, int(compression))
        self.state = "live_ready"

    def start_heatmap(self) -> None:
        if self.state not in {"live_ready", "stopped", "streaming"}:
            raise RuntimeError("Heatmap requires a synced order book")
        self.state = "streaming"

    def stop_heatmap(self) -> None:
        self.state = "stopped"

    def disconnect(self) -> None:
        self.state = "disconnected"
        self.symbol = None
        self.compression = 1

    async def handle_command(self, command: dict[str, Any]) -> dict[str, Any]:
        command_type = command.get("type")
        if command_type == "connect":
            symbol = str(command.get("symbol", "")).upper()
            compression = int(command.get("compression", 1) or 1)
            self.mark_connecting(symbol, compression=compression)
            return self.status_event()
        if command_type == "start_heatmap":
            self.start_heatmap()
            return self.status_event()
        if command_type == "stop_heatmap":
            self.stop_heatmap()
            return self.status_event()
        if command_type == "disconnect":
            self.disconnect()
            return self.status_event()
        if command_type == "set_assistant_settings":
            self.update_assistant_settings(command.get("settings", {}))
            return self.assistant_status_event()
        if command_type == "enable_auto_trade":
            self.assistant_settings = _replace_settings(
                self.assistant_settings, auto_trade_enabled=True
            )
            return self.assistant_status_event()
        if command_type == "disable_auto_trade":
            self.assistant_settings = _replace_settings(
                self.assistant_settings, auto_trade_enabled=False
            )
            return self.assistant_status_event()
        if command_type == "enable_auto_exit":
            self.assistant_settings = _replace_settings(
                self.assistant_settings, auto_exit_enabled=True
            )
            return self.assistant_status_event()
        if command_type == "disable_auto_exit":
            self.assistant_settings = _replace_settings(
                self.assistant_settings, auto_exit_enabled=False
            )
            return self.assistant_status_event()
        raise RuntimeError(f"Unsupported command: {command_type}")

    def status_event(self, message: str | None = None) -> dict[str, Any]:
        return {
            "type": "status",
            "state": self.state,
            "symbol": self.symbol,
            "compression": self.compression,
            "message": message,
        }

    def update_assistant_settings(self, payload: dict[str, Any]) -> None:
        current = self.assistant_settings
        self.assistant_settings = AssistantRiskSettings(
            auto_trade_enabled=current.auto_trade_enabled,
            auto_exit_enabled=current.auto_exit_enabled,
            trade_notional_usdt=max(
                0.0,
                float(payload.get("trade_notional_usdt", current.trade_notional_usdt)),
            ),
            max_loss_usdt=float(payload.get("max_loss_usdt", current.max_loss_usdt)),
            max_holding_time_sec=float(
                payload.get("max_holding_time_sec", current.max_holding_time_sec)
            ),
            confirmation_ms=int(payload.get("confirmation_ms", current.confirmation_ms)),
            opposite_signal_exit_enabled=bool(
                payload.get(
                    "opposite_signal_exit_enabled",
                    current.opposite_signal_exit_enabled,
                )
            ),
            toxic_vpin_exit_enabled=bool(
                payload.get("toxic_vpin_exit_enabled", current.toxic_vpin_exit_enabled)
            ),
            min_price_excursion_bps=max(
                0.0,
                float(
                    payload.get(
                        "min_price_excursion_bps",
                        current.min_price_excursion_bps,
                    )
                ),
            ),
            min_price_excursion_vol_multiplier=max(
                0.0,
                float(
                    payload.get(
                        "min_price_excursion_vol_multiplier",
                        current.min_price_excursion_vol_multiplier,
                    )
                ),
            ),
            require_price_extrema_progress=bool(
                payload.get(
                    "require_price_extrema_progress",
                    current.require_price_extrema_progress,
                )
            ),
            stop_rv_multiplier=max(
                0.0,
                float(payload.get("stop_rv_multiplier", current.stop_rv_multiplier)),
            ),
            take_rv_multiplier=max(
                0.0,
                float(payload.get("take_rv_multiplier", current.take_rv_multiplier)),
            ),
        )

    def assistant_status_event(self) -> dict[str, Any]:
        settings = self.assistant_settings
        return {
            "type": "assistant_status",
            "auto_trade_enabled": settings.auto_trade_enabled,
            "auto_exit_enabled": settings.auto_exit_enabled,
            "trade_notional_usdt": settings.trade_notional_usdt,
            "max_loss_usdt": settings.max_loss_usdt,
            "max_holding_time_sec": settings.max_holding_time_sec,
            "confirmation_ms": settings.confirmation_ms,
            "opposite_signal_exit_enabled": settings.opposite_signal_exit_enabled,
            "toxic_vpin_exit_enabled": settings.toxic_vpin_exit_enabled,
            "min_price_excursion_bps": settings.min_price_excursion_bps,
            "min_price_excursion_vol_multiplier": (
                settings.min_price_excursion_vol_multiplier
            ),
            "require_price_extrema_progress": settings.require_price_extrema_progress,
            "stop_rv_multiplier": settings.stop_rv_multiplier,
            "take_rv_multiplier": settings.take_rv_multiplier,
        }


def _replace_settings(
    settings: AssistantRiskSettings,
    **overrides: Any,
) -> AssistantRiskSettings:
    values = {
        "auto_trade_enabled": settings.auto_trade_enabled,
        "auto_exit_enabled": settings.auto_exit_enabled,
        "trade_notional_usdt": settings.trade_notional_usdt,
        "max_loss_usdt": settings.max_loss_usdt,
        "max_holding_time_sec": settings.max_holding_time_sec,
        "confirmation_ms": settings.confirmation_ms,
        "opposite_signal_exit_enabled": settings.opposite_signal_exit_enabled,
        "toxic_vpin_exit_enabled": settings.toxic_vpin_exit_enabled,
        "min_price_excursion_bps": settings.min_price_excursion_bps,
        "min_price_excursion_vol_multiplier": (
            settings.min_price_excursion_vol_multiplier
        ),
        "require_price_extrema_progress": settings.require_price_extrema_progress,
        "stop_rv_multiplier": settings.stop_rv_multiplier,
        "take_rv_multiplier": settings.take_rv_multiplier,
    }
    values.update(overrides)
    return AssistantRiskSettings(**values)


class LiveHeatmapService:
    def __init__(self) -> None:
        self.session = BrowserSession()
        self.book = OrderBook()
        self.trade_buffer = TradeBuffer()
        self.frame_builder: FrameBuilder | None = None
        self.client: BinanceMarketClient | None = None
        self.adaptive_market: AdaptiveMarketService | None = None
        self.entry_filter: EntryFilterEngine | None = EntryFilterEngine(
            block_on_toxic_vpin=False,
            vpin_regime=AdaptiveVpinRegime(window_ms=60_000),
        )
        self.bybit_book = OrderBook()
        self.bybit_trade_buffer = TradeBuffer()
        self.bybit_client: BybitMarketClient | None = None
        self.bybit_adaptive_market: AdaptiveMarketService | None = None
        self.bybit_entry_filter: EntryFilterEngine | None = EntryFilterEngine(
            block_on_toxic_vpin=False,
            vpin_regime=AdaptiveVpinRegime(window_ms=60_000),
        )
        self.coinbase_book = OrderBook()
        self.coinbase_trade_buffer = TradeBuffer()
        self.coinbase_client: CoinbaseMarketClient | None = None
        self.coinbase_adaptive_market: AdaptiveMarketService | None = None
        self.coinbase_entry_filter: EntryFilterEngine | None = EntryFilterEngine(
            block_on_toxic_vpin=False,
            vpin_regime=AdaptiveVpinRegime(window_ms=60_000),
        )
        self.kraken_book = OrderBook()
        self.kraken_trade_buffer = TradeBuffer()
        self.kraken_client: KrakenMarketClient | None = None
        self.kraken_adaptive_market: AdaptiveMarketService | None = None
        self.kraken_entry_filter: EntryFilterEngine | None = EntryFilterEngine(
            block_on_toxic_vpin=False,
            vpin_regime=AdaptiveVpinRegime(window_ms=60_000),
        )
        self._coinbase_start_task: asyncio.Task | None = None
        self._kraken_start_task: asyncio.Task | None = None
        self.exit_engine: ExitEngine | None = ExitEngine()
        self.account_client: BinanceAccountClient | None = None
        self.order_executor: OrderExecutor | None = None
        self.position_tracker: PositionTracker | None = None
        self.user_data_client: BinanceUserDataClient | None = None
        self.current_position: PositionState | None = None
        self._last_entry_results: dict[str, dict[str, Any]] = {}
        self._last_auto_trade_ms: int = 0
        self._last_position_poll_ms: int = 0
        # Timestamp (event-time "E" from Binance) of the most recent
        # ACCOUNT_UPDATE that touched the active symbol. Used to detect when a
        # slow REST /positionRisk response would clobber a fresher user-data
        # push and discard it.
        self._last_ws_position_update_ms: int = 0
        self._last_assistant_broadcast_ms: int = 0
        self._position_poll_task: asyncio.Task | None = None
        self.trading_state = "OFF"
        self.cooldown_until_ms = 0
        self.trading_message: str | None = None
        self.frame_task: asyncio.Task | None = None
        self.connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def register(self, websocket: WebSocket) -> None:
        self.connections.add(websocket)
        await websocket.send_json(self.session.status_event())
        await websocket.send_json(self.trading_status_event())

    async def unregister(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    async def handle_websocket_command(self, command: dict[str, Any]) -> None:
        command_type = command.get("type")
        if command_type == "start_trading":
            await self.broadcast(await self.start_trading())
            return
        if command_type == "stop_trading":
            await self.broadcast(await self.stop_trading())
            return
        if command_type == "emergency_flatten":
            await self.broadcast(await self.emergency_flatten())
            return

        event = await self.session.handle_command(command)
        if command.get("type") == "set_assistant_settings":
            self._apply_assistant_market_settings()
        await self.broadcast(event)

        command_type = command["type"]
        if command_type == "connect":
            await self._connect_symbol(
                command["symbol"],
                int(command.get("compression", self.session.compression) or 1),
            )
        elif command_type == "start_heatmap":
            self._ensure_frame_task()
        elif command_type == "stop_heatmap":
            await self._stop_frame_task()
        elif command_type == "disconnect":
            await self._disconnect_market()

    async def _connect_symbol(self, symbol: str, compression: int) -> None:
        async with self._lock:
            await self._stop_frame_task()
            await self._disconnect_market()
            self.book = OrderBook()
            self.trade_buffer = TradeBuffer()
            self.bybit_book = OrderBook()
            self.bybit_trade_buffer = TradeBuffer()
            self.coinbase_book = OrderBook()
            self.coinbase_trade_buffer = TradeBuffer()
            self.kraken_book = OrderBook()
            self.kraken_trade_buffer = TradeBuffer()
            self._last_entry_results = {}
            self._last_auto_trade_ms = 0
            self.frame_builder = None
            self.adaptive_market = AdaptiveMarketService(symbol)
            self.bybit_adaptive_market = AdaptiveMarketService(symbol)
            self.coinbase_adaptive_market = None
            self.kraken_adaptive_market = None
            self._apply_assistant_market_settings()
            self.entry_filter = EntryFilterEngine(
                block_on_toxic_vpin=False,
                vpin_regime=AdaptiveVpinRegime(window_ms=60_000),
            )
            self.bybit_entry_filter = EntryFilterEngine(
                block_on_toxic_vpin=False,
                vpin_regime=AdaptiveVpinRegime(window_ms=60_000),
            )
            self.coinbase_entry_filter = EntryFilterEngine(
                block_on_toxic_vpin=False,
                vpin_regime=AdaptiveVpinRegime(window_ms=60_000),
            )
            self.kraken_entry_filter = EntryFilterEngine(
                block_on_toxic_vpin=False,
                vpin_regime=AdaptiveVpinRegime(window_ms=60_000),
            )
            self.exit_engine = ExitEngine()
            self.position_tracker = PositionTracker(symbol)
            account_settings = load_binance_account_settings()
            if account_settings.has_credentials:
                self.account_client = BinanceAccountClient(account_settings)
                self.order_executor = OrderExecutor(self.account_client)
            else:
                self.account_client = None
                self.order_executor = None
            await self._start_user_data_stream()
            self.client = BinanceMarketClient(
                symbol=symbol,
                order_book=self.book,
                trade_buffer=self.trade_buffer,
                on_trade=self._on_market_trade,
                on_book_update=self._on_book_update,
            )
            self.bybit_client = BybitMarketClient(
                symbol=symbol,
                order_book=self.bybit_book,
                trade_buffer=self.bybit_trade_buffer,
                on_trade=self._on_bybit_market_trade,
                on_book_update=self._on_bybit_book_update,
            )
            try:
                await self.client.start()
                await self.bybit_client.start()
            except Exception as exc:
                self.session.state = "error"
                await self.broadcast({"type": "error", "message": str(exc)})
                await self.broadcast(self.session.status_event(str(exc)))
                await self._disconnect_market()
                return

            await self._stop_indicator_start_tasks()
            self._coinbase_start_task = asyncio.create_task(
                self._start_coinbase_indicator(symbol),
                name="coinbase-indicator-start",
            )
            self._kraken_start_task = asyncio.create_task(
                self._start_kraken_indicator(symbol),
                name="kraken-indicator-start",
            )

            self.frame_builder = FrameBuilder(
                height=HEATMAP_HEIGHT,
                tick_size=self.client.tick_size or 0.0,
                aggregation=compression,
                visible_levels=HEATMAP_HEIGHT,
                buffer_levels=HEATMAP_BUFFER_LEVELS,
                recenter_margin_levels=HEATMAP_RECENTER_MARGIN_LEVELS,
            )
            self.session.mark_synced(symbol.upper(), compression=compression)
            await self.broadcast(
                {
                    "type": "reset",
                    "tick_size": self.client.tick_size,
                    "display_step": self.frame_builder.display_step,
                    "buffer_levels": self.frame_builder.buffer_levels,
                }
            )
            await self.broadcast(self.session.status_event())

    def _ensure_frame_task(self) -> None:
        if self.frame_task and not self.frame_task.done():
            return
        self.frame_task = asyncio.create_task(self._frame_loop(), name="heatmap-frame-loop")

    async def _stop_frame_task(self) -> None:
        if self.frame_task is None:
            return
        self.frame_task.cancel()
        with suppress(asyncio.CancelledError):
            await self.frame_task
        self.frame_task = None
        await self._stop_position_poll_task()

    async def _stop_position_poll_task(self) -> None:
        if self._position_poll_task is None:
            return
        self._position_poll_task.cancel()
        with suppress(asyncio.CancelledError):
            await self._position_poll_task
        self._position_poll_task = None

    async def _stop_indicator_start_tasks(self) -> None:
        for attr in ("_coinbase_start_task", "_kraken_start_task"):
            task: asyncio.Task | None = getattr(self, attr, None)
            if task is None:
                continue
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
            setattr(self, attr, None)

    async def _start_user_data_stream(self) -> None:
        if self.account_client is None:
            self.user_data_client = None
            return
        try:
            client = BinanceUserDataClient(
                self.account_client,
                on_account_update=self._on_user_data_account_update,
            )
            await client.start()
        except Exception as exc:  # noqa: BLE001
            self.user_data_client = None
            await self.broadcast(
                {
                    "type": "account_error",
                    "message": f"user data stream unavailable: {exc}",
                }
            )
            return
        self.user_data_client = client

    async def _stop_user_data_stream(self) -> None:
        if self.user_data_client is None:
            return
        with suppress(Exception):
            await self.user_data_client.stop()
        self.user_data_client = None

    async def _disconnect_market(self) -> None:
        try:
            if self.client is not None:
                await self.client.stop()
        finally:
            await self._stop_indicator_start_tasks()
            await self._stop_user_data_stream()
            self.client = None
            self.adaptive_market = None
            self.entry_filter = None
            if self.bybit_client is not None:
                await self.bybit_client.stop()
            self.bybit_client = None
            self.bybit_adaptive_market = None
            self.bybit_entry_filter = None
            if self.coinbase_client is not None:
                with suppress(Exception):
                    await self.coinbase_client.stop()
            self.coinbase_client = None
            self.coinbase_adaptive_market = None
            self.coinbase_entry_filter = None
            if self.kraken_client is not None:
                with suppress(Exception):
                    await self.kraken_client.stop()
            self.kraken_client = None
            self.kraken_adaptive_market = None
            self.kraken_entry_filter = None
            self.exit_engine = None
            self.account_client = None
            self.order_executor = None
            self.position_tracker = None
            self.current_position = None
            self._last_entry_results = {}
            self._last_auto_trade_ms = 0
            self._last_position_poll_ms = 0
            self._last_ws_position_update_ms = 0
            self._last_assistant_broadcast_ms = 0
            await self._stop_position_poll_task()
            self.trading_state = "OFF"
            self.cooldown_until_ms = 0
            self.trading_message = None
            self.session.assistant_settings = _replace_settings(
                self.session.assistant_settings,
                auto_trade_enabled=False,
            )
            self.session.disconnect()

    def trading_status_event(
        self,
        *,
        now_ms: int | None = None,
        message: str | None = None,
    ) -> dict[str, Any]:
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        remaining_ms = 0
        if self.cooldown_until_ms > now_ms:
            remaining_ms = self.cooldown_until_ms - now_ms
        event_message = message if message is not None else self.trading_message
        return {
            "type": "trading_status",
            "state": self.trading_state,
            "enabled": self.trading_state in {"ARMED", "IN_POSITION"},
            "can_open": self.trading_state == "ARMED",
            "cooldown_remaining_ms": remaining_ms,
            "message": event_message,
        }

    async def start_trading(self, *, now_ms: int | None = None) -> dict[str, Any]:
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        self.session.assistant_settings = _replace_settings(
            self.session.assistant_settings,
            auto_trade_enabled=True,
        )
        if self.session.symbol is None:
            return self._set_trading_state(
                "ERROR",
                now_ms=now_ms,
                message="connect a symbol before trading",
            )
        if self.account_client is None or self.order_executor is None:
            return self._set_trading_state(
                "ERROR",
                now_ms=now_ms,
                message="Binance account credentials are not configured",
            )
        if self.client is None or self.bybit_client is None:
            return self._set_trading_state(
                "ERROR",
                now_ms=now_ms,
                message="market streams are not connected",
            )
        if self.frame_builder is not None and self.session.state in {"live_ready", "stopped"}:
            self.session.start_heatmap()
            self._ensure_frame_task()
            await self.broadcast(self.session.status_event())
        try:
            if self.position_tracker is not None:
                await self._refresh_position(self.session.symbol, now_ms)
        except Exception as exc:
            return self._set_trading_state("ERROR", now_ms=now_ms, message=str(exc))
        return self._refresh_trading_readiness(now_ms=now_ms)

    async def stop_trading(self, *, now_ms: int | None = None) -> dict[str, Any]:
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        self.session.assistant_settings = _replace_settings(
            self.session.assistant_settings,
            auto_trade_enabled=False,
        )
        return self._set_trading_state(
            "STOPPED",
            now_ms=now_ms,
            message="new entries disabled",
        )

    async def emergency_flatten(self, *, now_ms: int | None = None) -> dict[str, Any]:
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        self.session.assistant_settings = _replace_settings(
            self.session.assistant_settings,
            auto_trade_enabled=False,
        )
        symbol = self.session.symbol
        if symbol is None or self.order_executor is None:
            return self._set_trading_state(
                "ERROR",
                now_ms=now_ms,
                message="no active trading symbol",
            )
        try:
            if hasattr(self.order_executor, "cancel_all_open_orders"):
                await self.order_executor.cancel_all_open_orders(symbol)
            if (
                self.current_position is not None
                and self.current_position.is_open
                and not getattr(self.order_executor, "close_pending", False)
            ):
                result = await self.order_executor.close_position(self.current_position)
                await self.broadcast(
                    {
                        "type": "order_status",
                        "action": "emergency_close",
                        "status": result.get("status"),
                        "clientOrderId": result.get("clientOrderId"),
                    }
                )
        except Exception as exc:
            await self.broadcast({"type": "account_error", "message": str(exc)})
            return self._set_trading_state("ERROR", now_ms=now_ms, message=str(exc))
        return self._set_trading_state(
            "STOPPED",
            now_ms=now_ms,
            message="orders cancelled; close submitted if position was open",
        )

    def _set_trading_state(
        self,
        state: str,
        *,
        now_ms: int,
        message: str | None = None,
    ) -> dict[str, Any]:
        self.trading_state = state
        self.trading_message = message
        return self.trading_status_event(now_ms=now_ms, message=message)

    def _refresh_trading_readiness(self, *, now_ms: int) -> dict[str, Any]:
        if self.current_position is not None and self.current_position.is_open:
            return self._set_trading_state(
                "IN_POSITION",
                now_ms=now_ms,
                message="position is open",
            )
        if self._cooldown_active(now_ms):
            return self._set_trading_state(
                "COOLDOWN",
                now_ms=now_ms,
                message="post-close pause",
            )
        if self._entry_filters_warmed():
            return self._set_trading_state(
                "ARMED",
                now_ms=now_ms,
                message=self._entry_readiness_message(),
            )
        return self._set_trading_state(
            "WARMING",
            now_ms=now_ms,
            message=self._entry_readiness_message(),
        )

    def _cooldown_active(self, now_ms: int) -> bool:
        return self.cooldown_until_ms > now_ms

    def _entry_filters_warmed(self) -> bool:
        binance = self._last_entry_results.get("binance")
        bybit = self._last_entry_results.get("bybit")
        return (
            self._entry_result_warmed(binance)
            and self._entry_result_warmed(bybit)
        )

    @staticmethod
    def _entry_result_warmed(result: Any) -> bool:
        market_state = _entry_value(result, "market_state")
        return market_state is not None and market_state != "WARMING"

    def _entry_readiness_message(self) -> str:
        parts = []
        for exchange in ("binance", "bybit"):
            result = self._last_entry_results.get(exchange)
            state = _entry_value(result, "market_state") or "NO_DATA"
            reason = _entry_value(result, "reason") or "-"
            parts.append(f"{exchange} {state} {reason}")
        return " | ".join(parts)

    async def _confirm_flat_close(self, symbol: str, now_ms: int) -> None:
        if self.order_executor is not None and hasattr(
            self.order_executor,
            "mark_close_confirmed",
        ):
            self.order_executor.mark_close_confirmed()
        if self.order_executor is not None and hasattr(
            self.order_executor,
            "cancel_all_open_orders",
        ):
            try:
                await self.order_executor.cancel_all_open_orders(symbol)
            except Exception as exc:
                self.trading_state = "ERROR"
                self.trading_message = str(exc)
                await self.broadcast({"type": "account_error", "message": str(exc)})
                await self.broadcast(self.trading_status_event(now_ms=now_ms))
                return
        self.cooldown_until_ms = now_ms + TRADING_COOLDOWN_MS
        self.trading_state = "COOLDOWN"
        self.trading_message = "post-close pause"
        await self.broadcast(
            {
                "type": "order_status",
                "action": "cancel_all",
                "status": "DONE",
            }
        )
        await self.broadcast(self.trading_status_event(now_ms=now_ms))

    def _on_market_trade(self, trade: dict[str, Any]) -> None:
        if self.adaptive_market is None:
            return
        self.adaptive_market.on_agg_trade(trade)
        self._schedule_assistant_snapshot()

    def _on_bybit_market_trade(self, trade: dict[str, Any]) -> None:
        if self.bybit_adaptive_market is None:
            return
        self.bybit_adaptive_market.on_agg_trade(trade)
        self._schedule_assistant_snapshot()

    def _on_book_update(self, book: OrderBook, event_time_ms: int | None) -> None:
        if self.adaptive_market is None:
            return
        best_bid = book.best_bid()
        best_ask = book.best_ask()
        if best_bid is None or best_ask is None:
            return
        bid_vol = book.bids.get(best_bid, 0.0)
        ask_vol = book.asks.get(best_ask, 0.0)
        self.adaptive_market.on_top_of_book(
            best_bid=best_bid,
            best_ask=best_ask,
            bid_vol=bid_vol,
            ask_vol=ask_vol,
            timestamp_ms=event_time_ms,
        )
        self._schedule_assistant_snapshot()

    def _on_bybit_book_update(self, book: OrderBook, event_time_ms: int | None) -> None:
        if self.bybit_adaptive_market is None:
            return
        best_bid = book.best_bid()
        best_ask = book.best_ask()
        if best_bid is None or best_ask is None:
            return
        bid_vol = book.bids.get(best_bid, 0.0)
        ask_vol = book.asks.get(best_ask, 0.0)
        self.bybit_adaptive_market.on_top_of_book(
            best_bid=best_bid,
            best_ask=best_ask,
            bid_vol=bid_vol,
            ask_vol=ask_vol,
            timestamp_ms=event_time_ms,
        )
        self._schedule_assistant_snapshot()

    def _on_coinbase_market_trade(self, trade: dict[str, Any]) -> None:
        if self.coinbase_adaptive_market is None:
            return
        self.coinbase_adaptive_market.on_agg_trade(trade)
        self._schedule_assistant_snapshot()

    def _on_coinbase_book_update(self, book: OrderBook, event_time_ms: int | None) -> None:
        if self.coinbase_adaptive_market is None:
            return
        best_bid = book.best_bid()
        best_ask = book.best_ask()
        if best_bid is None or best_ask is None:
            return
        bid_vol = book.bids.get(best_bid, 0.0)
        ask_vol = book.asks.get(best_ask, 0.0)
        self.coinbase_adaptive_market.on_top_of_book(
            best_bid=best_bid,
            best_ask=best_ask,
            bid_vol=bid_vol,
            ask_vol=ask_vol,
            timestamp_ms=event_time_ms,
        )
        self._schedule_assistant_snapshot()

    def _on_kraken_market_trade(self, trade: dict[str, Any]) -> None:
        if self.kraken_adaptive_market is None:
            return
        self.kraken_adaptive_market.on_agg_trade(trade)
        self._schedule_assistant_snapshot()

    def _on_kraken_book_update(self, book: OrderBook, event_time_ms: int | None) -> None:
        if self.kraken_adaptive_market is None:
            return
        best_bid = book.best_bid()
        best_ask = book.best_ask()
        if best_bid is None or best_ask is None:
            return
        bid_vol = book.bids.get(best_bid, 0.0)
        ask_vol = book.asks.get(best_ask, 0.0)
        self.kraken_adaptive_market.on_top_of_book(
            best_bid=best_bid,
            best_ask=best_ask,
            bid_vol=bid_vol,
            ask_vol=ask_vol,
            timestamp_ms=event_time_ms,
        )
        self._schedule_assistant_snapshot()

    async def _start_coinbase_indicator(self, symbol: str) -> None:
        product_id = to_coinbase_product(symbol)
        if product_id is None:
            self.coinbase_client = None
            self.coinbase_adaptive_market = None
            self.coinbase_entry_filter = None
            await self.broadcast(
                {
                    "type": "indicator_status",
                    "exchange": "coinbase",
                    "state": "unavailable",
                    "message": f"no Coinbase product for {symbol}",
                }
            )
            return
        self.coinbase_adaptive_market = AdaptiveMarketService(product_id)
        self._apply_assistant_market_settings()
        await self.broadcast(
            {
                "type": "indicator_status",
                "exchange": "coinbase",
                "state": "connecting",
                "product": product_id,
            }
        )
        client = CoinbaseMarketClient(
            product_id=product_id,
            order_book=self.coinbase_book,
            trade_buffer=self.coinbase_trade_buffer,
            on_trade=self._on_coinbase_market_trade,
            on_book_update=self._on_coinbase_book_update,
        )
        try:
            await client.start(sync_timeout=60.0)
        except Exception as exc:
            with suppress(Exception):
                await client.stop()
            self.coinbase_client = None
            self.coinbase_adaptive_market = None
            self.coinbase_entry_filter = None
            await self.broadcast(
                {
                    "type": "indicator_status",
                    "exchange": "coinbase",
                    "state": "error",
                    "product": product_id,
                    "message": str(exc) or type(exc).__name__,
                }
            )
            return
        self.coinbase_client = client
        await self.broadcast(
            {
                "type": "indicator_status",
                "exchange": "coinbase",
                "state": "ready",
                "product": product_id,
            }
        )

    async def _start_kraken_indicator(self, symbol: str) -> None:
        pair = to_kraken_pair(symbol)
        if pair is None:
            self.kraken_client = None
            self.kraken_adaptive_market = None
            self.kraken_entry_filter = None
            await self.broadcast(
                {
                    "type": "indicator_status",
                    "exchange": "kraken",
                    "state": "unavailable",
                    "message": f"no Kraken pair for {symbol}",
                }
            )
            return
        self.kraken_adaptive_market = AdaptiveMarketService(pair)
        self._apply_assistant_market_settings()
        await self.broadcast(
            {
                "type": "indicator_status",
                "exchange": "kraken",
                "state": "connecting",
                "product": pair,
            }
        )
        client = KrakenMarketClient(
            pair=pair,
            order_book=self.kraken_book,
            trade_buffer=self.kraken_trade_buffer,
            on_trade=self._on_kraken_market_trade,
            on_book_update=self._on_kraken_book_update,
        )
        try:
            await client.start(sync_timeout=60.0)
        except Exception as exc:
            with suppress(Exception):
                await client.stop()
            self.kraken_client = None
            self.kraken_adaptive_market = None
            self.kraken_entry_filter = None
            await self.broadcast(
                {
                    "type": "indicator_status",
                    "exchange": "kraken",
                    "state": "error",
                    "product": pair,
                    "message": str(exc) or type(exc).__name__,
                }
            )
            return
        self.kraken_client = client
        await self.broadcast(
            {
                "type": "indicator_status",
                "exchange": "kraken",
                "state": "ready",
                "product": pair,
            }
        )

    def _schedule_assistant_snapshot(self) -> None:
        now_ms = int(time.time() * 1000)
        if now_ms - self._last_assistant_broadcast_ms < 500:
            return
        self._last_assistant_broadcast_ms = now_ms
        asyncio.create_task(self._broadcast_assistant_snapshot())

    def _apply_assistant_market_settings(self) -> None:
        settings = self.session.assistant_settings
        for market in (
            self.adaptive_market,
            self.bybit_adaptive_market,
            self.coinbase_adaptive_market,
            self.kraken_adaptive_market,
        ):
            if market is None:
                continue
            market.update_price_excursion_settings(
                min_price_excursion_bps=settings.min_price_excursion_bps,
                min_price_excursion_vol_multiplier=(
                    settings.min_price_excursion_vol_multiplier
                ),
            )
            market.update_require_price_extrema_progress(
                settings.require_price_extrema_progress
            )

    async def _frame_loop(self) -> None:
        interval_seconds = FRAME_INTERVAL_MS / 1000
        try:
            while self.session.state == "streaming":
                if self.frame_builder is None:
                    await asyncio.sleep(interval_seconds)
                    continue
                frame = self.frame_builder.build(self.book, self.trade_buffer.drain())
                await self.broadcast(
                    {
                        "type": "frame",
                        "timestamp": frame.timestamp,
                        "column": frame.column,
                        "mid_price": frame.mid_price,
                        "best_bid": frame.best_bid,
                        "best_ask": frame.best_ask,
                    }
                )
                await self.broadcast(
                    {
                        "type": "trades",
                        "timestamp": time.time_ns(),
                        "items": [
                            {
                                "price": trade.price,
                                "qty": trade.qty,
                                "y": trade.y,
                                "is_buyer_maker": trade.is_buyer_maker,
                            }
                            for trade in frame.trades
                        ],
                    }
                )
                now_ms = int(time.time() * 1000)
                self._schedule_position_poll(now_ms)
                await self._evaluate_exit(now_ms)
                await self._broadcast_assistant_snapshot()
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise

    def _schedule_position_poll(self, now_ms: int) -> None:
        if self._position_poll_task is not None and not self._position_poll_task.done():
            return
        self._position_poll_task = asyncio.create_task(
            self._run_position_poll(now_ms),
            name="binance-position-poll",
        )

    async def _run_position_poll(self, now_ms: int) -> None:
        try:
            await self._poll_position_if_due(now_ms)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.broadcast({"type": "account_error", "message": str(exc)})

    async def _poll_position_if_due(self, now_ms: int) -> None:
        symbol = self.session.symbol
        if symbol is None or self.account_client is None:
            return
        if now_ms - self._last_position_poll_ms < POSITION_REST_FALLBACK_INTERVAL_MS:
            return
        self._last_position_poll_ms = now_ms
        await self._refresh_position(symbol, now_ms)

    async def _on_user_data_account_update(
        self,
        positions: dict[str, PositionState],
        event: dict[str, Any],
    ) -> None:
        symbol = self.session.symbol
        if symbol is None or self.position_tracker is None:
            return
        # Binance ACCOUNT_UPDATE only includes positions that actually changed.
        # If our symbol is absent, nothing about it has changed — leave state
        # alone instead of clobbering it to flat.
        position = positions.get(symbol.upper())
        if position is None:
            return
        update_time = event.get("E")
        if isinstance(update_time, (int, float)):
            now_ms = int(update_time)
        else:
            now_ms = int(time.time() * 1000)
        # User data events are authoritative; suppress fallback REST poll for
        # at least one full fallback window after each push, and stamp the
        # event time so an in-flight REST /positionRisk response cannot
        # overwrite this state with older data.
        self._last_position_poll_ms = now_ms
        self._last_ws_position_update_ms = max(
            self._last_ws_position_update_ms, now_ms
        )
        await self._apply_position_update(symbol, position, now_ms)

    async def _apply_position_update(
        self,
        symbol: str,
        position: PositionState | None,
        now_ms: int,
    ) -> None:
        if self.position_tracker is None:
            return
        realized_vol = 0.0
        if self.adaptive_market is not None:
            realized_vol = float(getattr(self.adaptive_market.state(), "realized_vol", 0.0))
        tracked = self.position_tracker.update(
            position,
            now_ms,
            realized_vol=realized_vol,
            stop_rv_multiplier=self.session.assistant_settings.stop_rv_multiplier,
            take_rv_multiplier=self.session.assistant_settings.take_rv_multiplier,
        )
        previous = self.current_position
        self.current_position = tracked if tracked is not None and tracked.is_open else None
        if (
            self.current_position is not None
            and self.order_executor is not None
            and hasattr(self.order_executor, "mark_open_confirmed")
        ):
            self.order_executor.mark_open_confirmed()
        elif (
            tracked is not None
            and not tracked.is_open
            and self.order_executor is not None
            and getattr(self.order_executor, "close_pending", False)
        ):
            await self._confirm_flat_close(symbol, now_ms)
        if self.current_position is not None:
            await self.broadcast(
                {
                    "type": "position",
                    "symbol": self.current_position.symbol,
                    "side": self.current_position.side,
                    "quantity": self.current_position.quantity,
                    "entry_price": self.current_position.entry_price,
                    "unrealized_pnl": self.current_position.unrealized_pnl,
                    "opened_at_ms": self.current_position.opened_at_ms,
                    "realized_vol_at_entry": self.current_position.realized_vol_at_entry,
                    "rv_stop_price": self.current_position.rv_stop_price,
                    "rv_take_price": self.current_position.rv_take_price,
                }
            )
        elif previous is not None:
            await self.broadcast(
                {
                    "type": "position",
                    "symbol": previous.symbol,
                    "side": "FLAT",
                    "quantity": 0.0,
                    "entry_price": previous.entry_price,
                    "unrealized_pnl": 0.0,
                    "opened_at_ms": None,
                    "realized_vol_at_entry": 0.0,
                    "rv_stop_price": None,
                    "rv_take_price": None,
                }
            )

    async def _refresh_position(
        self,
        symbol: str,
        now_ms: int,
    ) -> PositionState | None:
        if self.account_client is None or self.position_tracker is None:
            return None
        # Snapshot the latest user-data timestamp BEFORE the await so we can
        # detect a WS push that landed during the in-flight REST call. If a
        # newer ACCOUNT_UPDATE arrived while we were waiting, the REST
        # response is stale by construction and must not be applied.
        ws_seq_before = self._last_ws_position_update_ms
        position = await self.account_client.fetch_position(symbol)
        if self._last_ws_position_update_ms > ws_seq_before:
            return self.current_position
        # Defence-in-depth: even when no WS push arrived during the await,
        # the REST row itself may simply be older than the last WS event
        # already applied (e.g. Binance edge-node clock skew). Skip in that
        # case too.
        rest_update_ms = position.update_time_ms if position is not None else None
        if (
            rest_update_ms is not None
            and self._last_ws_position_update_ms > 0
            and rest_update_ms < self._last_ws_position_update_ms
        ):
            return self.current_position
        realized_vol = 0.0
        if self.adaptive_market is not None:
            realized_vol = float(getattr(self.adaptive_market.state(), "realized_vol", 0.0))
        tracked = self.position_tracker.update(
            position,
            now_ms,
            realized_vol=realized_vol,
            stop_rv_multiplier=self.session.assistant_settings.stop_rv_multiplier,
            take_rv_multiplier=self.session.assistant_settings.take_rv_multiplier,
        )
        self.current_position = tracked if tracked is not None and tracked.is_open else None
        if (
            self.current_position is not None
            and self.order_executor is not None
            and hasattr(self.order_executor, "mark_open_confirmed")
        ):
            self.order_executor.mark_open_confirmed()
        elif (
            tracked is not None
            and not tracked.is_open
            and self.order_executor is not None
            and getattr(self.order_executor, "close_pending", False)
        ):
            await self._confirm_flat_close(symbol, now_ms)
        self._last_position_poll_ms = now_ms
        return tracked

    async def _evaluate_exit(self, now_ms: int):
        if self.exit_engine is None or self.current_position is None:
            return None
        marked_position = self._mark_position_to_book(self.current_position)
        decision = self.exit_engine.evaluate(
            position=marked_position,
            sdk_state=self.adaptive_market.state() if self.adaptive_market is not None else None,
            latest_signal=self._latest_active_signal(now_ms),
            settings=self.session.assistant_settings,
            now_ms=now_ms,
        )
        if decision.reason is not None:
            await self.broadcast(
                {
                    "type": "exit_status",
                    "state": decision.state,
                    "reason": decision.reason,
                    "should_close": decision.should_close,
                    "hard_exit": decision.hard_exit,
                }
            )
        if (
            decision.should_close
            and self.order_executor is not None
            and not self.order_executor.close_pending
        ):
            try:
                order_result = await self.order_executor.close_position(self.current_position)
            except Exception as exc:
                await self.broadcast({"type": "account_error", "message": str(exc)})
            else:
                await self.broadcast(
                    {
                        "type": "order_status",
                        "status": order_result.get("status"),
                        "clientOrderId": order_result.get("clientOrderId"),
                    }
                )
        return decision

    def _latest_active_signal(self, now_ms: int):
        if self.adaptive_market is None:
            return None
        latest_for_display = getattr(self.adaptive_market, "latest_signal_for_display", None)
        if latest_for_display is not None:
            return latest_for_display(now_ms)
        return getattr(self.adaptive_market, "latest_signal", None)

    def _mark_position_to_book(self, position: PositionState) -> PositionState:
        mark_price: float | None = None
        if position.side == "LONG":
            mark_price = self.book.best_bid()
        elif position.side == "SHORT":
            mark_price = self.book.best_ask()
        if mark_price is None:
            return position
        unrealized_pnl = (mark_price - position.entry_price) * position.amount
        return replace(position, unrealized_pnl=unrealized_pnl)

    async def _evaluate_auto_trade(self, now_ms: int) -> None:
        settings = self.session.assistant_settings
        if not settings.auto_trade_enabled:
            return
        if self.trading_state in TRADING_ACTIVE_STATES:
            self._refresh_trading_readiness(now_ms=now_ms)
        if self.trading_state != "ARMED":
            return
        if self.order_executor is None or self.session.symbol is None:
            return
        if self.current_position is not None:
            return
        if getattr(self.order_executor, "open_pending", False):
            return
        if self._last_auto_trade_ms and now_ms - self._last_auto_trade_ms < 15_000:
            return

        side = _confluence_entry_side(self._last_entry_results)
        if side is None:
            return

        mark_price = self._entry_mark_price(side)
        if mark_price is None:
            return

        try:
            if self.account_client is not None and self.position_tracker is not None:
                position = await self._refresh_position(self.session.symbol, now_ms)
                if position is not None and position.is_open:
                    return
            quantity_step = getattr(self.client, "quantity_step", None)
            min_quantity = getattr(self.client, "min_quantity", None)
            open_kwargs = {
                "side": side,
                "notional_usdt": settings.trade_notional_usdt,
                "mark_price": mark_price,
            }
            if quantity_step is not None:
                open_kwargs["quantity_step"] = quantity_step
            if min_quantity is not None:
                open_kwargs["min_quantity"] = min_quantity
            order_result = await self.order_executor.open_position(
                self.session.symbol,
                **open_kwargs,
            )
        except Exception as exc:
            await self.broadcast({"type": "account_error", "message": str(exc)})
            return

        self._last_auto_trade_ms = now_ms
        await self.broadcast(
            {
                "type": "order_status",
                "action": "open",
                "side": side,
                "status": order_result.get("status"),
                "clientOrderId": order_result.get("clientOrderId"),
            }
        )

    def _entry_mark_price(self, side: str) -> float | None:
        if side == "LONG":
            return self.book.best_ask()
        if side == "SHORT":
            return self.book.best_bid()
        return None

    async def _broadcast_assistant_snapshot(self) -> None:
        await self.broadcast(self.session.assistant_status_event())
        now_ms = int(time.time() * 1000)
        if self.adaptive_market is not None and self.entry_filter is not None:
            warmup = self.adaptive_market.warmup_progress()
            result = self.entry_filter.evaluate(
                self.adaptive_market.state(),
                self.adaptive_market.latest_signal_for_display(now_ms),
                trade_count=warmup["trade_count"],
                min_buckets_for_vpin=warmup["min_buckets_for_vpin"],
                min_ticks_for_z=warmup["min_ticks_for_z"],
                now_ms=now_ms,
            )
            self._last_entry_results["binance"] = _entry_result_payload(result)
            await self.broadcast(
                {
                    "type": "entry_filter",
                    "market_state": result.market_state,
                    "long_filter": result.long_filter,
                    "short_filter": result.short_filter,
                    "reason": result.reason,
                    "latest_signal_type": result.latest_signal_type,
                    "latest_signal_confidence": result.latest_signal_confidence,
                    "vpin": result.vpin,
                    "exchange": "binance",
                }
            )
        if self.bybit_adaptive_market is not None and self.bybit_entry_filter is not None:
            warmup = self.bybit_adaptive_market.warmup_progress()
            result = self.bybit_entry_filter.evaluate(
                self.bybit_adaptive_market.state(),
                self.bybit_adaptive_market.latest_signal_for_display(now_ms),
                trade_count=warmup["trade_count"],
                min_buckets_for_vpin=warmup["min_buckets_for_vpin"],
                min_ticks_for_z=warmup["min_ticks_for_z"],
                now_ms=now_ms,
            )
            self._last_entry_results["bybit"] = _entry_result_payload(result)
            await self.broadcast(
                {
                    "type": "entry_filter",
                    "exchange": "bybit",
                    "market_state": result.market_state,
                    "long_filter": result.long_filter,
                    "short_filter": result.short_filter,
                    "reason": result.reason,
                    "latest_signal_type": result.latest_signal_type,
                    "latest_signal_confidence": result.latest_signal_confidence,
                    "vpin": result.vpin,
                }
            )
        await self._broadcast_indicator_entry_filter(
            "coinbase",
            self.coinbase_adaptive_market,
            self.coinbase_entry_filter,
            now_ms=now_ms,
        )
        await self._broadcast_indicator_entry_filter(
            "kraken",
            self.kraken_adaptive_market,
            self.kraken_entry_filter,
            now_ms=now_ms,
        )
        if self.trading_state in TRADING_ACTIVE_STATES:
            await self.broadcast(self._refresh_trading_readiness(now_ms=now_ms))
        else:
            await self.broadcast(self.trading_status_event(now_ms=now_ms))
        await self._evaluate_auto_trade(now_ms)
        if self.current_position is not None:
            await self.broadcast(
                {
                    "type": "position",
                    "symbol": self.current_position.symbol,
                    "side": self.current_position.side,
                    "quantity": self.current_position.quantity,
                    "entry_price": self.current_position.entry_price,
                    "unrealized_pnl": self.current_position.unrealized_pnl,
                    "opened_at_ms": self.current_position.opened_at_ms,
                    "realized_vol_at_entry": self.current_position.realized_vol_at_entry,
                    "rv_stop_price": self.current_position.rv_stop_price,
                    "rv_take_price": self.current_position.rv_take_price,
                }
            )

    async def _broadcast_indicator_entry_filter(
        self,
        exchange: str,
        adaptive_market: AdaptiveMarketService | None,
        entry_filter: EntryFilterEngine | None,
        *,
        now_ms: int,
    ) -> None:
        if adaptive_market is None or entry_filter is None:
            self._last_entry_results.pop(exchange, None)
            return
        warmup = adaptive_market.warmup_progress()
        result = entry_filter.evaluate(
            adaptive_market.state(),
            adaptive_market.latest_signal_for_display(now_ms),
            trade_count=warmup["trade_count"],
            min_buckets_for_vpin=warmup["min_buckets_for_vpin"],
            min_ticks_for_z=warmup["min_ticks_for_z"],
            now_ms=now_ms,
        )
        self._last_entry_results[exchange] = _entry_result_payload(result)
        await self.broadcast(
            {
                "type": "entry_filter",
                "exchange": exchange,
                "market_state": result.market_state,
                "long_filter": result.long_filter,
                "short_filter": result.short_filter,
                "reason": result.reason,
                "latest_signal_type": result.latest_signal_type,
                "latest_signal_confidence": result.latest_signal_confidence,
                "vpin": result.vpin,
            }
        )

    async def broadcast(self, payload: dict[str, Any]) -> None:
        stale_connections: list[WebSocket] = []
        for websocket in self.connections:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale_connections.append(websocket)
        for websocket in stale_connections:
            self.connections.discard(websocket)


def _entry_result_payload(result: Any) -> dict[str, Any]:
    return {
        "market_state": result.market_state,
        "long_filter": result.long_filter,
        "short_filter": result.short_filter,
        "reason": result.reason,
        "latest_signal_type": result.latest_signal_type,
        "latest_signal_confidence": result.latest_signal_confidence,
        "vpin": result.vpin,
    }


def _entry_value(result: Any, key: str) -> Any:
    if isinstance(result, dict):
        return result.get(key)
    return getattr(result, key, None)


def _confluence_entry_side(results: dict[str, Any]) -> str | None:
    binance = results.get("binance")
    bybit = results.get("bybit")
    if binance is None or bybit is None:
        return None
    if (
        _entry_value(binance, "long_filter") == "OK"
        and _entry_value(bybit, "long_filter") == "OK"
    ):
        return "LONG"
    if (
        _entry_value(binance, "short_filter") == "OK"
        and _entry_value(bybit, "short_filter") == "OK"
    ):
        return "SHORT"
    return None
