# PROMPT: AdaptiveAnalyticsSDK v1.1 — Final Specification

## Role and Context

You are a Principal Quantitative Engineer and an expert in market microstructure. Your task is to design and write, in Python, an independent, high-performance SDK named `AdaptiveAnalyticsSDK`.

The SDK is intended for real-time analysis of order flow and the order book. Its primary goal is the generation of entry signals for scalping strategies (catching false breakouts and micro-knives) with adaptive filtering of market toxicity.

**Critical positioning:** This is **not** a trading bot — it is an analytical SDK. It does not trade. It emits strictly typed signals and consumes feedback about realized trade quality. The architecture must be Universal, Plug-and-Play, with isolated state for each symbol.

---

## Tech Stack and Architectural Constraints

- **Language:** Python 3.11+. **Synchronous core API** (rationale: all hot-path computations are CPU-bound, in-memory; `asyncio` adds event-loop overhead with no I/O benefit. Async wrapping is trivial for downstream consumers).
- **Dependencies: only `numpy`.** Forbidden: `pandas`, `scipy`. The standard normal CDF must be computed via `0.5 * (1 + math.erf(x / math.sqrt(2)))`.
- **Strict typing.** All public structures must use `@dataclass(slots=True)`. **No `dict` in public interfaces.** No `Any`, no `getattr`/`setattr` shortcuts.
- **Memory bounds.** Only `collections.deque(maxlen=...)`, fixed-size numpy arrays, or custom ring buffers. No unbounded lists, no growing dicts without eviction.
- **Threading model.** Single-thread ingress assumed; no internal locks. Thread-safety is the consumer's responsibility.
- **Layout (v1):** one package `adaptive_sdk/` with files `types.py`, `vpin.py`, `exhaustion.py`, `mab.py`, `sdk.py`. Async/threaded adapters are out of scope for v1.

---

## A. Architectural Decisions (LOCKED)

### A1. Synchronous API
`on_trade`, `on_book_update`, `report_outcome`, etc. are synchronous and return immediately. No `async def` in the core.

### A2. BVC + Trade Splitting Semantics
In **TRADE_SIGN** mode: a trade whose volume exceeds the bucket's remaining capacity is split across buckets. Each chunk inherits the original trade's aggressor sign. This is the canonical VPIN behavior — splitting is **mandatory** in TRADE_SIGN.

In **BVC** mode: chunks of a split trade are **unsigned**. They accumulate `V_total` and update `P_close` of the bucket they fall into. Buy/sell separation happens **only at bucket close** via:
```
V_buy  = V_total * Phi((P_close - P_open) / sigma)
V_sell = V_total - V_buy
```
where `Phi` is the standard normal CDF computed via `math.erf`.

Configurable bucket fill mode (per Panayides et al. on volume-bar bias):
- `BucketFillMode.EXACT_FILL`: classical VPIN-style exact volume buckets with split.
- `BucketFillMode.MINIMUM_FILL`: bucket closes on the first trade that crosses the threshold; no splitting.

**Defaults:**
- `TRADE_SIGN` ⇒ `EXACT_FILL` (split is canonical; `MINIMUM_FILL` permitted for thin markets but flagged non-canonical in docstrings).
- `BVC` ⇒ `MINIMUM_FILL` (BVC operates at bulk granularity; chunk-level splitting adds boundary complexity without reducing variance of `Phi(...)`).

### A3. Sigma for BVC (single canonical path in v1)
`bvc_sigma_source = BUCKET_RETURNS_EWMA` only. Update rule on every bucket close:
```
r_i        = ln(P_close_i / P_close_{i-1})
ewma_r2_i  = (1 - lambda) * ewma_r2_{i-1} + lambda * r_i^2
sigma_i    = sqrt(ewma_r2_i + epsilon)
```
- `lambda = 0.06` (≈ half-life 11 buckets).
- Centered estimator (mean assumed ≈ 0 at bucket scale).
- BVC classification is **deferred** until at least `min_buckets_for_bvc_sigma = 10` buckets are observed. Before that, buckets accumulate but VPIN is not emitted (`is_ready = False`).

`TICK_EWMA` is explicitly **excluded** from v1 (experimental, future).

### A4. VPIN Window
`VPIN = mean(|V_buy - V_sell|) / bucket_size` over the last `vpin_window` closed buckets. **Default `vpin_window = 50`.**

### A5. Bandit — Gaussian Thompson Sampling (Normal-Inverse-Gamma)
Each arm maintains a NIG posterior `(mu, kappa, alpha, beta)`.

**Sampling** (per `select_arm` call):
```
sigma2 ~ InverseGamma(alpha, beta)
mu_s   ~ Normal(mu, sigma2 / kappa)
```
Pick `argmax_arm(mu_s)`.

**Update** on `report_outcome` (Murphy 2007, eqs. 86–89):
```
mu_new    = (kappa * mu + reward) / (kappa + 1)
kappa_new = kappa + 1
alpha_new = alpha + 0.5
beta_new  = beta + 0.5 * (kappa / (kappa + 1)) * (reward - mu)^2
```

**Prior (weakly informative):** `mu_0 = 0, kappa_0 = 1, alpha_0 = 2, beta_0 = 1`.

Three arms differ by their `z_threshold`:
- `AGGRESSIVE` (id=0): `z_threshold = 1.5`
- `NORMAL` (id=1): `z_threshold = 2.0`
- `CONSERVATIVE` (id=2): `z_threshold = 2.5`

### A6. Reward Shaping
**Default:** `reward = pnl - fees - slippage`.

**Optional hook** (advanced users): `SymbolConfig.reward_transform: Optional[Callable[[Outcome], float]]`. If set, used in place of the default. `mfe`, `mae`, `holding_time_ms` are preserved on `Outcome` for analytics and custom reward policies — they are **not** in the default objective. Document this explicitly.

### A7. Pending Signals Registry
SDK maintains an internal registry:
```python
@dataclass(slots=True)
class PendingSignal:
    symbol: str
    arm_id: int
    timestamp: float
    is_resolved: bool = False
```
Stored in `pending_signals: dict[str, PendingSignal]` keyed by `signal_id`.

**Rules:**
- TTL = `signal_ttl_seconds` (default `3600.0`). Expired entries are removed lazily.
- Hard cap `pending_signals_max = 10_000`. On overflow, evict the oldest unresolved entries first.
- `report_outcome` is **idempotent**: a second call with the same `signal_id` returns `False` and does not update any arm.
- Periodic lazy cleanup every `pending_cleanup_interval_ticks = 1000` `on_trade` calls.

### A8. Local Extrema — Time-Based Window
Local high/low are computed over a **time window**, not a tick count, for cross-symbol stability:
- `price_extrema_window_ms` (default `3000` — typical micro-knife horizon).
- Implementation: a `deque[(timestamp, price)]` with left-eviction by timestamp, plus two monotonic deques providing O(1) `min` and `max`.

### A9. Per-Second Flow EWMA (consequence of A8)
Because tick density varies wildly across symbols, raw per-tick EWMA on aggressor flow makes Z-scores incomparable. v1 uses **wall-clock-bucketed flow**:
- Aggressor volume is accumulated into a fixed-duration window `flow_window_ms` (default `1000`).
- On window rollover (driven by trade timestamps, not real-time), one update is applied to the EWMA / EWVar of `buy_aggressor_volume_per_second` and `sell_aggressor_volume_per_second`.
- `z_buy_flow  = (buy_vol_curr_window  - ewma_buy)  / sqrt(ewvar_buy  + epsilon)`
- `z_sell_flow = (sell_vol_curr_window - ewma_sell) / sqrt(ewvar_sell + epsilon)`

EWVar is computed via West 1979 (exponentially weighted recursive variance):
```
delta       = x - ewma_prev
ewma_new    = ewma_prev + lambda * delta
ewvar_new   = (1 - lambda) * (ewvar_prev + lambda * delta^2)
```

### A10. OBI in Confidence (optional confirmatory factor)
`on_book_update` updates `OBI = (bid_vol - ask_vol) / (bid_vol + ask_vol + epsilon) ∈ [-1, 1]` per symbol.

If no book updates have ever been received for a symbol, `obi_alignment = 1.0` (neutral; no degradation of the API).

For `BUY_EXHAUSTION` (we expect a bounce up, so we want bid pressure):
```
obi_alignment = clip(0.5 + 0.5 * obi, 0.5, 1.0)
```
For `SELL_EXHAUSTION` (we expect a bounce down):
```
obi_alignment = clip(0.5 - 0.5 * obi, 0.5, 1.0)
```

### A11. Confidence Formula
```
base       = sigmoid((|z_relevant| - z_threshold_arm) / z_scale)        # z_scale = 0.5
toxicity   = clip((vpin_high - vpin) / (vpin_high - vpin_mid), 0.0, 1.0)
confidence = base * toxicity * obi_alignment                            # ∈ [0, 1]
```
Where `z_relevant` is `z_sell_flow` for `SELL_EXHAUSTION` and `z_buy_flow` for `BUY_EXHAUSTION`.

If `vpin >= vpin_high` ⇒ signal is suppressed (return `None`).

**Defaults:** `vpin_mid = 0.30`, `vpin_high = 0.50`, `z_scale = 0.5`.

> **NOTE in docstring:** `vpin_mid` and `vpin_high` are starting heuristics for liquid spot crypto (BTC/ETH). Production deployments **must** calibrate these per `(symbol, regime)` from historical VPIN distributions (e.g. q60 / q90 quantiles). They are not universal constants.

### A12. Bucket Sizing Policy
Two modes only in v1:
- `FIXED`: manual `fixed_bucket_size` in base currency (e.g. `10.0` BTC).
- `ROLLING_DAILY`:
  - Tracks `deque[(ts, vol)]` evicted on `ts < now - 86400`.
  - `bucket_size = max(min_bucket_size, rolling_24h_volume / target_buckets_per_day)`.
  - Recomputed at most once every `60` seconds (debounced) to avoid jitter.

`VOLUME_PROFILE` is **excluded** from v1.

### A13. Warmup Gate
`is_ready == True` if and only if **all** of the following hold for the symbol:
- Closed buckets `>= min_buckets_for_vpin` (default `50`).
- Total observed ticks `>= min_ticks_for_z` (default `200`).
- BVC σ has accumulated `>= min_buckets_for_bvc_sigma = 10` (only checked in BVC mode).

Until ready: no signals are emitted; `get_state` returns current accumulators with `is_ready = False`.

### A14. Per-Symbol Isolation
Each registered symbol owns a `SymbolContext` containing its own:
- `TrueVPINEngine` instance and bucket history.
- `ExhaustionDetector` instance with EWMA/EWVar, ring buffers, monotonic deques.
- `ThompsonSamplingMAB` instance with three arms.
- `OBI` state and last `BookSnapshot`.
- Optional `zone_of_interest = (lower, upper)`.

No cross-symbol state. Registering a new symbol does not perturb any existing context.

### A15. Zone of Interest
Optional filter. Without a zone set ⇒ signals are generated whenever the rest of the gates pass. With a zone set ⇒ a signal is generated only if `lower_bound <= price <= upper_bound` at the trade timestamp.

### A16. BVC Positioning (docstring, not enforced)
> "Use `TRADE_SIGN` when a reliable aggressor flag is available (Binance `is_buyer_maker`, Bybit `side`, OKX `tradeId`+`takerSide`, etc.). `BVC` is a **fallback** for: bulk-classifying historical OHLCV-style data, cross-venue normalization, or feeds without an aggressor flag. Pöppe-Moeller-Schiereck (2016) showed `BVC` underperforms tick-rule on aggressor-flag-rich exchanges; do not pick it by default."

**Default `classification_mode = TRADE_SIGN`.**

---

## B. Public Data Contracts

```python
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ClassificationMode(Enum):
    TRADE_SIGN = 1
    BVC = 2


class BucketFillMode(Enum):
    EXACT_FILL = 1     # split trades across buckets
    MINIMUM_FILL = 2   # close bucket on first overflow, no split


class BucketSizePolicy(Enum):
    FIXED = 1
    ROLLING_DAILY = 2


class BVCSigmaSource(Enum):
    BUCKET_RETURNS_EWMA = 1
    # TICK_EWMA = 2  # excluded from v1


class ExhaustionType(Enum):
    SELL_EXHAUSTION = 1
    BUY_EXHAUSTION = 2


class ArmId(Enum):
    AGGRESSIVE = 0
    NORMAL = 1
    CONSERVATIVE = 2


@dataclass(slots=True, frozen=True)
class TradeTick:
    symbol: str
    price: float
    quantity: float
    is_buyer_maker: bool   # True ⇔ seller is the aggressor (Binance convention)
    timestamp: float       # seconds, float


@dataclass(slots=True, frozen=True)
class BookSnapshot:
    symbol: str
    best_bid: float
    best_ask: float
    bid_vol: float         # volume at best bid (for OBI)
    ask_vol: float         # volume at best ask
    timestamp: float


@dataclass(slots=True, frozen=True)
class MetricsSnapshot:
    vpin: float
    z_score_buy_flow: float
    z_score_sell_flow: float
    obi: float
    bucket_size: float
    buckets_filled: int


@dataclass(slots=True, frozen=True)
class Signal:
    signal_id: str             # uuid4().hex, generated by SDK
    symbol: str
    timestamp: float
    exhaustion_type: ExhaustionType
    confidence: float          # ∈ [0, 1]
    arm_id: int                # ArmId.value
    metrics: MetricsSnapshot


@dataclass(slots=True, frozen=True)
class Outcome:
    signal_id: str
    pnl: float
    mfe: float                 # Maximum Favorable Excursion (analytics only)
    mae: float                 # Maximum Adverse Excursion  (analytics only)
    fees: float
    slippage: float
    holding_time_ms: float     # analytics only


@dataclass(slots=True, frozen=True)
class SymbolState:
    is_ready: bool
    vpin: float
    sell_exhaustion_z: float
    buy_exhaustion_z: float
    pending_signals_count: int
    buckets_filled: int


@dataclass(slots=True)
class SymbolConfig:
    classification_mode: ClassificationMode = ClassificationMode.TRADE_SIGN
    bucket_fill_mode: BucketFillMode = BucketFillMode.EXACT_FILL
    bucket_policy: BucketSizePolicy = BucketSizePolicy.FIXED
    fixed_bucket_size: float = 10.0
    target_buckets_per_day: int = 50
    min_bucket_size: float = 0.1
    bucket_size_recompute_interval_s: float = 60.0
    bvc_sigma_source: BVCSigmaSource = BVCSigmaSource.BUCKET_RETURNS_EWMA
    bvc_lambda: float = 0.06
    min_buckets_for_bvc_sigma: int = 10
    vpin_window: int = 50
    min_buckets_for_vpin: int = 50
    min_ticks_for_z: int = 200
    price_extrema_window_ms: int = 3000
    flow_window_ms: int = 1000
    flow_lambda: float = 0.06
    vpin_mid: float = 0.30      # CALIBRATE PER (symbol, regime) IN PROD
    vpin_high: float = 0.50     # CALIBRATE PER (symbol, regime) IN PROD
    z_thresholds: tuple[float, float, float] = (1.5, 2.0, 2.5)
    z_scale: float = 0.5
    signal_ttl_seconds: float = 3600.0
    pending_signals_max: int = 10_000
    pending_cleanup_interval_ticks: int = 1000
    reward_transform: Optional[Callable[[Outcome], float]] = None


@dataclass(slots=True)
class GlobalConfig:
    default_symbol_config: SymbolConfig = field(default_factory=SymbolConfig)
```

> **Note on `frozen=True`**: `Signal`, `Outcome`, `TradeTick`, `BookSnapshot`, `SymbolState`, `MetricsSnapshot` are immutable value objects (defensive against accidental mutation by consumers). `SymbolConfig` and `GlobalConfig` remain mutable for ergonomic construction.

---

## C. Public SDK Class

```python
class AdaptiveAnalyticsSDK:
    def __init__(self, config: GlobalConfig) -> None: ...

    def register_symbol(
        self,
        symbol: str,
        custom_config: Optional[SymbolConfig] = None,
    ) -> None:
        """Create an isolated context. Idempotent: re-registration is a no-op."""

    def on_trade(self, tick: TradeTick) -> Optional[Signal]:
        """Main ingress. Updates VPIN buckets, exhaustion metrics, and may emit a signal."""

    def on_book_update(self, snapshot: BookSnapshot) -> None:
        """Updates OBI for the symbol. Optional — exhaustion detection works without it."""

    def set_zone_of_interest(
        self,
        symbol: str,
        upper_bound: float,
        lower_bound: float,
    ) -> None: ...

    def clear_zone_of_interest(self, symbol: str) -> None: ...

    def get_state(self, symbol: str) -> SymbolState: ...

    def report_outcome(self, symbol: str, outcome: Outcome) -> bool:
        """Returns False if signal_id is unknown, expired, or already resolved (idempotent)."""
```

---

## D. Component Specifications

### D1. `TrueVPINEngine`

**Responsibilities:**
- Accept ticks; assemble volume buckets per `BucketFillMode` and `BucketSizePolicy`.
- On bucket close, classify via `TRADE_SIGN` (cumulative signed volumes during fill) or `BVC` (`Phi((P_close - P_open) / sigma)` at close).
- Maintain `deque[BucketResult]` of length `vpin_window`.
- Expose `vpin: float` and `buckets_filled: int`.

**Splitting algorithm (TRADE_SIGN, EXACT_FILL):**
```
remaining = trade.quantity
sign      = +1 if not trade.is_buyer_maker else -1   # +1 = buyer aggressor
while remaining > 0:
    capacity = bucket_size - bucket.v_total
    chunk    = min(remaining, capacity)
    if sign > 0:
        bucket.v_buy  += chunk
    else:
        bucket.v_sell += chunk
    bucket.v_total   += chunk
    bucket.p_close   = trade.price       # last seen price wins
    if bucket.v_total >= bucket_size - epsilon:
        close_bucket(bucket)
        bucket = new_bucket(p_open=trade.price)
    remaining -= chunk
```

**BVC accumulation (BVC, MINIMUM_FILL — default for BVC):**
```
bucket.v_total += trade.quantity
bucket.p_close  = trade.price
if bucket.v_total >= bucket_size:
    classify_bucket_bvc(bucket)
    new_bucket(p_open=trade.price)
```

**BVC classification on close:**
```
r          = ln(bucket.p_close / bucket.p_open)
phi        = 0.5 * (1 + erf(r / (sigma * sqrt(2))))
bucket.v_buy  = bucket.v_total * phi
bucket.v_sell = bucket.v_total - bucket.v_buy
update_sigma_ewma(r)
```

**Edge cases (must handle):**
- `bucket.p_open == bucket.p_close` ⇒ `r = 0` ⇒ `phi = 0.5` ⇒ 50/50 split.
- `bucket.p_open <= 0` or `p_close <= 0` ⇒ skip log return, treat as `r = 0`.
- `sigma == 0` (insufficient history) ⇒ skip emitting VPIN until warm-up satisfied.

**`ROLLING_DAILY` bucket size:**
- `deque[(ts, vol)]`, evict left while `front.ts < now - 86400`.
- Recompute `bucket_size` at most once per `bucket_size_recompute_interval_s`.

### D2. `ExhaustionDetector`

**Responsibilities:**
- Accumulate aggressor volume per `flow_window_ms` window. On window rollover (detected by trade timestamps), emit one EWMA/EWVar update for both buy and sell flows.
- Maintain `RollingExtrema` (time-windowed `min`/`max` of price) over `price_extrema_window_ms`.
- Expose `z_buy_flow`, `z_sell_flow`, `local_high`, `local_low`.

**Triggers (per `on_trade`, after warmup):**
- `SELL_EXHAUSTION`: `z_sell_flow > z_threshold_arm` AND `tick.price > local_low * (1 + kappa)` where `kappa = 1e-4` (price did NOT make a fresh local low — flow burned out).
- `BUY_EXHAUSTION`: `z_buy_flow  > z_threshold_arm` AND `tick.price < local_high * (1 - kappa)`.

If both trigger in the same tick, pick the side with the larger `|z|`.

**EWMA / EWVar (West 1979):**
```
delta     = x - ewma
ewma     += lambda * delta
ewvar     = (1 - lambda) * (ewvar + lambda * delta * delta)
```
Initial: `ewma = first_observation`, `ewvar = 0`. Z-score is undefined (returns `0`) until `ewvar > epsilon`.

### D3. `ThompsonSamplingMAB`

**Responsibilities:**
- Maintain three NIG posteriors (one per arm).
- `select_arm()`: sample once per arm, return `argmax`.
- `update(arm_id, reward)`: closed-form NIG update (formulas in A5).
- Reward transform applied externally (in `SDK.report_outcome` before calling `update`).

**Sampling (numerically stable):**
```
sigma2 = 1.0 / numpy.random.gamma(shape=alpha, scale=1.0/beta)
mu_s   = numpy.random.normal(loc=mu, scale=sqrt(sigma2 / kappa))
```

### D4. `AdaptiveAnalyticsSDK` (orchestrator)

**`on_trade(tick)`:**
1. `ctx = self._contexts[tick.symbol]` (`KeyError` ⇒ raise `ValueError`).
2. Feed tick to `ctx.vpin_engine` (handles splitting + bucket sizing).
3. Feed tick to `ctx.exhaustion_detector` (flow window, extrema, EWMA).
4. Increment tick counter; periodically run pending-signals cleanup.
5. If `not ctx.is_ready()` ⇒ return `None`.
6. If `ctx.vpin >= ctx.config.vpin_high` ⇒ return `None` (toxicity gate).
7. If `zone_of_interest` is set and `tick.price` outside ⇒ return `None`.
8. `arm_id = ctx.mab.select_arm()`; threshold = `ctx.config.z_thresholds[arm_id]`.
9. Check `SELL_EXHAUSTION` and `BUY_EXHAUSTION` triggers against this threshold.
10. If triggered:
    - Build `MetricsSnapshot`.
    - Compute `confidence` (A11).
    - Generate `signal_id = uuid4().hex`.
    - Insert into `pending_signals` (evict oldest if over cap).
    - Return `Signal`.
11. Otherwise return `None`.

**`report_outcome(symbol, outcome)`:**
1. Lookup `pending_signals[outcome.signal_id]`.
2. If missing, expired (`now - ts > ttl`), or `is_resolved` ⇒ return `False`.
3. Compute reward: `reward_transform(outcome) if config.reward_transform else outcome.pnl - outcome.fees - outcome.slippage`.
4. `ctx.mab.update(pending.arm_id, reward)`.
5. Mark `pending.is_resolved = True` (kept until TTL for idempotency).
6. Return `True`.

---

## E. Code Quality Requirements

- Full type hints. **No `Any`, no `dict` in public APIs.**
- Docstrings with mathematical exposition for: BVC classification, trade splitting algorithm, NIG update derivation, EWMA/EWVar (West 1979).
- Numerical stability: explicit `epsilon = 1e-12` for divisions; guard `log` against `<= 0`; warmup flags for EWMA/EWVar.
- Logger via `logging.getLogger(__name__)`, **no `print` in hot path**.
- All numeric thresholds and window sizes parameterized via `SymbolConfig` — no magic numbers in component bodies.
- Public functions are pure where possible; mutation is confined to `SymbolContext`.

---

## F. What NOT to Do

- Do **not** use `pandas` or `scipy`.
- Do **not** use `async def` in the core (sync only).
- Do **not** mutate input dataclasses (`TradeTick`, `BookSnapshot`, etc.).
- Do **not** keep unbounded collections (every container must have a maxlen or eviction policy).
- Do **not** drop or "stretch" trade volume during splitting in TRADE_SIGN/EXACT_FILL.
- Do **not** emit signals during warmup.
- Do **not** sign individual chunks in BVC mode.
- Do **not** introduce `VOLUME_PROFILE`, tick-based BVC sigma, or risk-adjusted reward as defaults — these are documented as future work.

---

## G. What MUST Be Done

- All four components fully implemented (`TrueVPINEngine`, `ExhaustionDetector`, `ThompsonSamplingMAB`, `AdaptiveAnalyticsSDK`).
- Per-symbol isolation via `SymbolContext`.
- BVC + MINIMUM_FILL by default; TRADE_SIGN + EXACT_FILL with full splitting (A2).
- BVC sigma via `BUCKET_RETURNS_EWMA` only (A3).
- Gaussian Thompson Sampling with NIG posterior (A5).
- `signal_id → arm` registry with TTL, size cap, idempotency (A7).
- Time-based extrema window (A8) and per-second flow EWMA (A9).
- OBI as optional confirmatory factor (A10), neutral (`= 1.0`) when book data absent.
- Confidence per A11; toxicity gate per A11.
- Warmup gate per A13.
- BVC docstring positioning per A16.

---

## H. Deliverable Format

Single Python package `adaptive_sdk/`:
```
adaptive_sdk/
    __init__.py        # re-exports public types and AdaptiveAnalyticsSDK
    types.py           # all dataclasses, enums, SymbolConfig, GlobalConfig
    vpin.py            # TrueVPINEngine, BucketResult, sigma EWMA
    exhaustion.py      # ExhaustionDetector, RollingExtrema, EWMA/EWVar helpers
    mab.py             # ThompsonSamplingMAB, NIG arm
    sdk.py             # SymbolContext, AdaptiveAnalyticsSDK
```

Optional: `tests/` directory with minimal unit tests covering:
- Trade splitting conservation: `sum(chunks) == original quantity` across N split scenarios.
- BVC math: `Phi(0) == 0.5`, monotonicity in `r`, edge cases (`p_open == p_close`, negative `r`).
- NIG update: posterior moments after a known sequence match closed-form.
- Warmup gate: no signals emitted before thresholds are met.
- Idempotent `report_outcome`: second call returns `False` and does not double-update arms.

---

## I. Begin Now

Implement everything above. Start with `types.py`, then `vpin.py`, `exhaustion.py`, `mab.py`, `sdk.py`. Document the math inline. Ask no clarifying questions — every ambiguity in this specification has been pre-resolved.
