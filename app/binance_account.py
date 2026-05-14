from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

import httpx

from app.assistant_config import BinanceAccountSettings
from app.position import PositionState


class BinanceAPIError(RuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        path: str,
        code: int | None,
        message: str,
    ) -> None:
        self.status_code = status_code
        self.path = path
        self.code = code
        self.message = message
        code_text = f" code={code}" if code is not None else ""
        super().__init__(f"Binance API {status_code} {path}{code_text}: {message}")


class BinanceAccountClient:
    """Signed Binance USD-M Futures account client.

    Network methods are intentionally small and injectable so tests can use
    ``httpx.MockTransport``. Trading-specific order placement is owned by
    ``OrderExecutor``.
    """

    def __init__(
        self,
        settings: BinanceAccountSettings,
        *,
        transport: httpx.AsyncBaseTransport | httpx.BaseTransport | None = None,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._now_ms = now_ms or _system_time_ms
        self._server_time_offset_ms = 0
        self._last_time_sync_ms = 0

    async def start_listen_key(self) -> str:
        payload = await self.public_post("/fapi/v1/listenKey")
        listen_key = payload.get("listenKey")
        if not listen_key:
            raise RuntimeError("Binance did not return listenKey")
        return str(listen_key)

    async def keepalive_listen_key(self) -> None:
        await self.public_put("/fapi/v1/listenKey")

    async def close_listen_key(self) -> None:
        await self.public_delete("/fapi/v1/listenKey")

    async def public_post(self, path: str) -> dict[str, Any]:
        async with self._http_client() as client:
            response = await client.post(path, headers=self._api_headers())
            _raise_for_binance_error(response, path)
            return response.json()

    async def public_put(self, path: str) -> dict[str, Any]:
        async with self._http_client() as client:
            response = await client.put(path, headers=self._api_headers())
            _raise_for_binance_error(response, path)
            return response.json() if response.content else {}

    async def public_delete(self, path: str) -> dict[str, Any]:
        async with self._http_client() as client:
            response = await client.delete(path, headers=self._api_headers())
            _raise_for_binance_error(response, path)
            return response.json() if response.content else {}

    async def public_get(self, path: str) -> dict[str, Any]:
        async with self._http_client() as client:
            response = await client.get(path)
            _raise_for_binance_error(response, path)
            return response.json() if response.content else {}

    async def signed_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        await self._ensure_server_time_synced()
        query = self._signed_query(params or {})
        async with self._http_client() as client:
            response = await client.get(f"{path}?{query}", headers=self._api_headers())
            _raise_for_binance_error(response, path)
            return response.json()

    async def signed_post(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        await self._ensure_server_time_synced()
        query = self._signed_query(params or {})
        async with self._http_client() as client:
            response = await client.post(f"{path}?{query}", headers=self._api_headers())
            _raise_for_binance_error(response, path)
            return response.json()

    async def signed_delete(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        await self._ensure_server_time_synced()
        query = self._signed_query(params or {})
        async with self._http_client() as client:
            response = await client.delete(f"{path}?{query}", headers=self._api_headers())
            _raise_for_binance_error(response, path)
            return response.json() if response.content else {}

    async def cancel_all_open_orders(self, symbol: str) -> dict[str, Any]:
        payload = await self.signed_delete(
            "/fapi/v1/allOpenOrders",
            {"symbol": symbol.upper()},
        )
        if not isinstance(payload, dict):
            return {}
        return payload

    async def fetch_position(self, symbol: str) -> PositionState | None:
        payload = await self.signed_get(
            "/fapi/v3/positionRisk",
            {"symbol": symbol.upper()},
        )
        if not isinstance(payload, list):
            return None
        for row in payload:
            position = _position_from_risk_row(row)
            if position is not None and position.symbol == symbol.upper():
                return position
        return None

    def _signed_query(self, params: dict[str, Any]) -> str:
        unsigned_params = dict(params)
        unsigned_params.setdefault("recvWindow", self.settings.recv_window_ms)
        unsigned_params["timestamp"] = self._signed_timestamp_ms()
        unsigned_query = urlencode(unsigned_params)
        signature = hmac.new(
            self.settings.api_secret.encode(),
            unsigned_query.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{unsigned_query}&signature={signature}"

    def _http_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.base_url,
            timeout=10.0,
            transport=self._transport,
        )

    def _api_headers(self) -> dict[str, str]:
        return {"X-MBX-APIKEY": self.settings.api_key}

    async def _ensure_server_time_synced(self) -> None:
        now_ms = self._now_ms()
        if self._last_time_sync_ms and now_ms - self._last_time_sync_ms < 60_000:
            return
        started_ms = now_ms
        payload = await self.public_get("/fapi/v1/time")
        finished_ms = self._now_ms()
        if not isinstance(payload, dict):
            return
        server_time = payload.get("serverTime")
        if server_time is None:
            return
        midpoint_ms = int((started_ms + finished_ms) / 2)
        self._server_time_offset_ms = int(server_time) - midpoint_ms
        self._last_time_sync_ms = finished_ms

    def _signed_timestamp_ms(self) -> int:
        return self._now_ms() + self._server_time_offset_ms


def _raise_for_binance_error(response: httpx.Response, path: str) -> None:
    if response.status_code < 400:
        return
    code = None
    message = response.text
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        raw_code = payload.get("code")
        if isinstance(raw_code, int):
            code = raw_code
        elif isinstance(raw_code, str):
            try:
                code = int(raw_code)
            except ValueError:
                code = None
        message = str(payload.get("msg") or payload)
    raise BinanceAPIError(
        status_code=response.status_code,
        path=path,
        code=code,
        message=message,
    )


def _position_from_risk_row(row: Any) -> PositionState | None:
    if not isinstance(row, dict):
        return None
    if row.get("positionSide", "BOTH") != "BOTH":
        return None
    symbol = row.get("symbol")
    if not symbol:
        return None
    try:
        amount = float(row.get("positionAmt", 0.0))
        entry_price = float(row.get("entryPrice", 0.0))
        break_even = float(row.get("breakEvenPrice", 0.0))
        unrealized = float(row.get("unRealizedProfit", 0.0))
        update_time = int(row["updateTime"]) if row.get("updateTime") is not None else None
    except (TypeError, ValueError):
        return None
    return PositionState(
        symbol=str(symbol).upper(),
        amount=amount,
        entry_price=entry_price,
        break_even_price=break_even,
        unrealized_pnl=unrealized,
        position_side="BOTH",
        update_time_ms=update_time,
    )


def _system_time_ms() -> int:
    import time

    return int(time.time() * 1000)
