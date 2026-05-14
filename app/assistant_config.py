from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from app.settings import BINANCE_FAPI_REST_URL

load_dotenv()


@dataclass(slots=True, frozen=True)
class AssistantRiskSettings:
    auto_trade_enabled: bool = False
    auto_exit_enabled: bool = False
    trade_notional_usdt: float = 10.0
    max_loss_usdt: float = 5.0
    max_holding_time_sec: float = 60.0
    confirmation_ms: int = 500
    opposite_signal_exit_enabled: bool = True
    toxic_vpin_exit_enabled: bool = True
    min_price_excursion_bps: float = 2.0
    min_price_excursion_vol_multiplier: float = 0.6
    require_price_extrema_progress: bool = True
    stop_rv_multiplier: float = 1.0
    take_rv_multiplier: float = 1.5


@dataclass(slots=True, frozen=True)
class BinanceAccountSettings:
    api_key: str
    api_secret: str
    base_url: str = BINANCE_FAPI_REST_URL
    recv_window_ms: int = 10_000

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)


def load_binance_account_settings() -> BinanceAccountSettings:
    return BinanceAccountSettings(
        api_key=os.getenv("BINANCE_API_KEY", ""),
        api_secret=os.getenv("BINANCE_API_SECRET", ""),
        base_url=os.getenv("BINANCE_FAPI_BASE_URL", BINANCE_FAPI_REST_URL),
        recv_window_ms=_env_int("BINANCE_RECV_WINDOW_MS", 10_000),
    )


def load_default_risk_settings() -> AssistantRiskSettings:
    return AssistantRiskSettings(
        auto_trade_enabled=_env_bool("AUTO_TRADE_ENABLED", False),
        auto_exit_enabled=_env_bool("AUTO_EXIT_ENABLED", False),
        trade_notional_usdt=_env_float("ASSISTANT_TRADE_NOTIONAL_USDT", 10.0),
        max_loss_usdt=_env_float("ASSISTANT_MAX_LOSS_USDT", 5.0),
        max_holding_time_sec=_env_float("ASSISTANT_MAX_HOLDING_TIME_SEC", 60.0),
        confirmation_ms=_env_int("ASSISTANT_CONFIRMATION_MS", 500),
        opposite_signal_exit_enabled=_env_bool("ASSISTANT_OPPOSITE_SIGNAL_EXIT", True),
        toxic_vpin_exit_enabled=_env_bool("ASSISTANT_TOXIC_VPIN_EXIT", True),
        min_price_excursion_bps=_env_float("ASSISTANT_MIN_PRICE_EXCURSION_BPS", 2.0),
        min_price_excursion_vol_multiplier=_env_float(
            "ASSISTANT_MIN_PRICE_EXCURSION_VOL_MULTIPLIER",
            0.6,
        ),
        require_price_extrema_progress=_env_bool(
            "ASSISTANT_REQUIRE_PRICE_EXTREMA_PROGRESS",
            True,
        ),
        stop_rv_multiplier=_env_float("ASSISTANT_STOP_RV_MULTIPLIER", 1.0),
        take_rv_multiplier=_env_float("ASSISTANT_TAKE_RV_MULTIPLIER", 1.5),
    )


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default
