from __future__ import annotations

import logging
from collections.abc import Callable
from decimal import Decimal, ROUND_DOWN
from uuid import uuid4

from app.position import PositionState


logger = logging.getLogger(__name__)


def build_market_close_order(
    position: PositionState,
    *,
    client_order_id: str,
) -> dict[str, str]:
    if not position.is_open:
        raise ValueError("Cannot close a flat position")
    side = "SELL" if position.amount > 0 else "BUY"
    return {
        "symbol": position.symbol,
        "side": side,
        "type": "MARKET",
        "quantity": _format_quantity(position.quantity),
        "reduceOnly": "true",
        "newClientOrderId": client_order_id,
    }


def build_market_open_order(
    symbol: str,
    *,
    side: str,
    notional_usdt: float,
    mark_price: float,
    client_order_id: str,
    quantity_step: float | None = None,
    min_quantity: float | None = None,
) -> dict[str, str]:
    if notional_usdt <= 0:
        raise ValueError("Entry notional must be positive")
    if mark_price <= 0:
        raise ValueError("Entry mark price must be positive")
    normalized_side = side.upper()
    if normalized_side not in {"LONG", "SHORT"}:
        raise ValueError("Entry side must be LONG or SHORT")
    quantity = Decimal(str(notional_usdt)) / Decimal(str(mark_price))
    if quantity_step is not None and quantity_step > 0:
        step = Decimal(str(quantity_step))
        quantity = (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step
    if min_quantity is not None and min_quantity > 0:
        minimum = Decimal(str(min_quantity))
        if quantity < minimum:
            raise ValueError("Entry quantity is below exchange minimum")
    if quantity <= 0:
        raise ValueError("Entry quantity is below exchange step")
    return {
        "symbol": symbol.upper(),
        "side": "BUY" if normalized_side == "LONG" else "SELL",
        "type": "MARKET",
        "quantity": _format_quantity(quantity),
        "newClientOrderId": client_order_id,
    }


class OrderExecutor:
    """Executor for assistant-managed market entries and exits."""

    def __init__(
        self,
        account_client,
        *,
        client_order_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._account_client = account_client
        self._client_order_id_factory = client_order_id_factory
        self._open_pending = False
        self._close_pending = False

    async def open_position(
        self,
        symbol: str,
        *,
        side: str,
        notional_usdt: float,
        mark_price: float,
        quantity_step: float | None = None,
        min_quantity: float | None = None,
    ) -> dict:
        if self._open_pending:
            raise RuntimeError("Open order already pending")
        client_order_id = self._next_client_order_id("open")
        params = build_market_open_order(
            symbol,
            side=side,
            notional_usdt=notional_usdt,
            mark_price=mark_price,
            client_order_id=client_order_id,
            quantity_step=quantity_step,
            min_quantity=min_quantity,
        )
        self._open_pending = True
        try:
            logger.info("Submitting MARKET entry order: %s", params)
            result = await self._account_client.signed_post("/fapi/v1/order", params)
            logger.info("Entry order accepted: %s", result)
            return result
        except Exception:
            logger.exception("Entry order failed: %s", params)
            self._open_pending = False
            raise

    async def close_position(self, position: PositionState) -> dict:
        if self._close_pending:
            raise RuntimeError("Close order already pending")
        client_order_id = self._next_client_order_id("close")
        params = build_market_close_order(position, client_order_id=client_order_id)
        self._close_pending = True
        try:
            logger.info("Submitting MARKET close order: %s", params)
            result = await self._account_client.signed_post("/fapi/v1/order", params)
            logger.info("Close order accepted: %s", result)
            return result
        except Exception:
            logger.exception("Close order failed: %s", params)
            self._close_pending = False
            raise

    async def cancel_all_open_orders(self, symbol: str) -> dict:
        normalized_symbol = symbol.upper()
        logger.info("Cancelling all open orders for %s", normalized_symbol)
        result = await self._account_client.cancel_all_open_orders(normalized_symbol)
        logger.info("Cancel-all accepted for %s: %s", normalized_symbol, result)
        return result

    def mark_open_confirmed(self) -> None:
        self._open_pending = False

    def mark_close_confirmed(self) -> None:
        self._close_pending = False

    def mark_pending_for_test(self) -> None:
        self._close_pending = True

    @property
    def open_pending(self) -> bool:
        return self._open_pending

    @property
    def close_pending(self) -> bool:
        return self._close_pending

    def _next_client_order_id(self, kind: str) -> str:
        if self._client_order_id_factory is not None:
            return self._client_order_id_factory()
        return _default_client_order_id(kind)


def _format_quantity(quantity: float | Decimal) -> str:
    text = format(Decimal(str(quantity)).normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _default_client_order_id(kind: str) -> str:
    return f"hm-sdk-{kind}-{uuid4().hex[:20]}"
