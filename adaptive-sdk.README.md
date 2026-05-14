# AdaptiveAnalyticsSDK v1.1

Real-time order-flow analytics SDK for scalping strategies. Generates typed
exhaustion signals with VPIN toxicity filtering and a Gaussian
Thompson-Sampling Multi-Armed Bandit that learns from post-trade outcomes.

**This is an analytical SDK, not a trading bot.** It ingests trades and
book snapshots, emits `Signal` objects, and consumes `Outcome` feedback.

## Quick start

```python
from adaptive_sdk import (
    AdaptiveAnalyticsSDK, GlobalConfig, SymbolConfig,
    TradeTick, BookSnapshot, Outcome,
)

sdk = AdaptiveAnalyticsSDK(GlobalConfig())
sdk.register_symbol("BTCUSDT")

# Feed trades and book snapshots as they arrive:
tick = TradeTick("BTCUSDT", price=65000.0, quantity=0.5,
                 is_buyer_maker=False, timestamp=1700000000.0)
signal = sdk.on_trade(tick)
if signal is not None:
    # Your trading system decides what to do with the signal.
    print(signal.exhaustion_type, signal.confidence, signal.arm_id)

# After closing the position, report the outcome back into the MAB:
outcome = Outcome(signal_id=signal.signal_id, pnl=12.5, mfe=15.0, mae=2.0,
                  fees=0.8, slippage=0.3, holding_time_ms=4200.0)
sdk.report_outcome("BTCUSDT", outcome)
```

## Components

| Module | Class | Purpose |
|---|---|---|
| `vpin.py` | `TrueVPINEngine` | Volume-time buckets, TRADE_SIGN/BVC classification, trade splitting |
| `exhaustion.py` | `ExhaustionDetector` | Per-second flow Z-score, time-windowed price extrema |
| `mab.py` | `ThompsonSamplingMAB` | 3-arm Gaussian TS with Normal-Inverse-Gamma posterior |
| `sdk.py` | `AdaptiveAnalyticsSDK` | Facade, per-symbol isolation, pending-signal registry |

## Architecture highlights

- **TRADE_SIGN + EXACT_FILL is default & canonical.** BVC is a fallback for
  feeds without an aggressor flag (Poppe-Moeller-Schiereck 2016 showed BVC
  underperforms tick-rule on aggressor-flag-rich exchanges).
- **BVC chunks are unsigned.** Split trades aggregate into `V_total`; buy/sell
  split happens only at bucket close via `Phi((P_close - P_open) / sigma)`.
- **Gaussian Thompson Sampling.** Continuous PnL reward -- NIG conjugate
  posterior (Murphy 2007), not Beta-Bernoulli.
- **Per-second flow EWMA, time-windowed price extrema.** Makes Z-scores and
  extrema comparable across symbols with wildly different tick densities.
- **Idempotent `report_outcome`.** Safe against duplicate reports and lost
  trade state.
- **Zero pandas/scipy.** Only `numpy` for the RNG in Thompson Sampling;
  normal CDF uses `math.erf`.

## Warmup

A symbol is not `is_ready` until:

1. `min_buckets_for_vpin` buckets have been classified (default 50),
2. `min_ticks_for_z` trades have been observed (default 200),
3. For BVC mode, the sigma tracker has seen `min_buckets_for_bvc_sigma`
   cross-bucket returns (default 10).

No signals are emitted during warmup.

## Calibration note

`vpin_mid = 0.30` and `vpin_high = 0.50` are **starting heuristics** for
liquid spot crypto (BTC/ETH). Production deployments MUST calibrate these
per `(symbol, regime)` from historical VPIN distributions (e.g. q60 / q90
quantiles). They are not universal constants.

## Running tests

```bash
pip install numpy pytest
python -m pytest
```

## Layout

```
adaptive_sdk/
    __init__.py       # public re-exports
    types.py          # dataclasses, enums, SymbolConfig, GlobalConfig
    vpin.py           # TrueVPINEngine, BVCSigmaTracker
    exhaustion.py     # ExhaustionDetector, RollingExtrema, EWMAEWVar
    mab.py            # ThompsonSamplingMAB, NIGArm
    sdk.py            # AdaptiveAnalyticsSDK, SymbolContext
tests/
    test_vpin.py
    test_exhaustion.py
    test_mab.py
    test_sdk.py
```
