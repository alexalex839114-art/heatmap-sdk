# Manual Scalping Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-symbol Binance USD-M Futures manual scalping assistant that filters manual entries with `adaptive-sdk`, tracks the real one-way position, and can close that position with a market order.

**Architecture:** Keep the existing FastAPI/WebSocket heatmap as the shell. Add focused backend services for adaptive market analytics, entry filtering, position tracking, exit decisions, signed Binance account access, and market-close execution. UI additions stay compact and operational: status, entry filter, position, auto-exit, and risk settings.

**Tech Stack:** Python 3.13, FastAPI, httpx, websockets, pydantic, pytest, pytest-asyncio, browser JavaScript, embedded `adaptive_sdk`, Binance USD-M Futures REST/user-data APIs.

---

## File Structure

- Create `app/assistant_config.py`: environment-backed assistant/account configuration and UI risk settings dataclasses.
- Create `app/position.py`: normalized position model and parser for Binance `ACCOUNT_UPDATE` payloads.
- Create `app/adaptive_service.py`: bridge Binance market events into `AdaptiveAnalyticsSDK`.
- Create `app/entry_filter.py`: pre-entry market state derived from SDK readiness, VPIN, signals, and OBI.
- Create `app/exit_engine.py`: hard/soft exit rules and assistant state transitions.
- Create `app/binance_account.py`: signed REST helpers, listen-key lifecycle, user-data stream parsing.
- Create `app/order_executor.py`: single-purpose market close executor; no open/increase/reverse methods.
- Modify `app/binance_client.py`: let market trade/depth processing notify adaptive analytics.
- Modify `app/ws_session.py`: own assistant services, settings, account lifecycle, status broadcasts, and close flow.
- Modify `app/settings.py`: load Binance signed API settings from environment.
- Modify `static/index.html`: add compact assistant controls/status panel.
- Modify `static/app.js`: handle assistant events and settings commands.
- Modify `static/style.css`: style dense operational panel without disrupting heatmap.
- Add focused tests in `tests/test_position.py`, `tests/test_entry_filter.py`, `tests/test_exit_engine.py`, `tests/test_order_executor.py`, `tests/test_adaptive_service.py`, `tests/test_binance_account.py`, and extend `tests/test_ws_session.py`.

---

### Task 1: Assistant Config And Position Model

**Files:**
- Create: `app/assistant_config.py`
- Create: `app/position.py`
- Test: `tests/test_position.py`

- [ ] **Step 1: Write failing tests for one-way position parsing**

```python
from app.position import PositionState, parse_account_update_positions


def test_parse_one_way_long_position():
    event = {
        "e": "ACCOUNT_UPDATE",
        "a": {
            "P": [{
                "s": "BTCUSDT",
                "pa": "0.012",
                "ep": "65000.0",
                "bep": "65010.0",
                "up": "3.5",
                "ps": "BOTH",
            }]
        },
    }

    positions = parse_account_update_positions(event)

    assert positions["BTCUSDT"].side == "LONG"
    assert positions["BTCUSDT"].quantity == 0.012
    assert positions["BTCUSDT"].unrealized_pnl == 3.5


def test_parse_one_way_short_position():
    event = {
        "e": "ACCOUNT_UPDATE",
        "a": {
            "P": [{
                "s": "ETHUSDT",
                "pa": "-0.5",
                "ep": "3200.0",
                "bep": "3198.0",
                "up": "-2.1",
                "ps": "BOTH",
            }]
        },
    }

    positions = parse_account_update_positions(event)

    assert positions["ETHUSDT"].side == "SHORT"
    assert positions["ETHUSDT"].quantity == 0.5
    assert positions["ETHUSDT"].amount == -0.5


def test_zero_position_is_flat():
    pos = PositionState(symbol="BTCUSDT", amount=0.0, entry_price=0.0)
    assert pos.side == "FLAT"
    assert not pos.is_open
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_position.py -v`
Expected: FAIL because `app.position` does not exist.

- [ ] **Step 3: Implement `PositionState` and parser**

Create a frozen/slots dataclass with fields:

- `symbol: str`
- `amount: float`
- `entry_price: float`
- `break_even_price: float = 0.0`
- `unrealized_pnl: float = 0.0`
- `position_side: str = "BOTH"`
- `update_time_ms: int | None = None`
- `opened_at_ms: int | None = None`

Properties:

- `is_open`
- `side`: `LONG`, `SHORT`, or `FLAT`
- `quantity`: `abs(amount)`

Parser:

- read `event["a"]["P"]`
- skip malformed rows defensively
- uppercase symbols
- support only `ps == "BOTH"` for v1

- [ ] **Step 4: Add assistant config types**

Create:

```python
@dataclass(slots=True, frozen=True)
class AssistantRiskSettings:
    auto_exit_enabled: bool = False
    max_loss_usdt: float = 5.0
    max_holding_time_sec: float = 60.0
    confirmation_ms: int = 500
    opposite_signal_exit_enabled: bool = True
    toxic_vpin_exit_enabled: bool = True
```

Also create environment loader for API key/secret/base URL in `app/assistant_config.py`.

- [ ] **Step 5: Run test**

Run: `.venv\Scripts\python.exe -m pytest tests/test_position.py -v`
Expected: PASS.

---

### Task 2: Adaptive Market Service

**Files:**
- Create: `app/adaptive_service.py`
- Test: `tests/test_adaptive_service.py`

- [ ] **Step 1: Write failing tests**

```python
from app.adaptive_service import AdaptiveMarketService


def test_adaptive_service_warms_and_exposes_state():
    service = AdaptiveMarketService("BTCUSDT")
    assert service.symbol == "BTCUSDT"
    state = service.state()
    assert state.is_ready is False


def test_on_trade_accepts_binance_agg_trade_shape():
    service = AdaptiveMarketService("BTCUSDT")
    signal = service.on_agg_trade({
        "price": 65000.0,
        "qty": 0.1,
        "timestamp": 1700000000000,
        "is_buyer_maker": False,
    })
    assert signal is None
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_adaptive_service.py -v`
Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement service**

Use embedded `adaptive_sdk`:

- instantiate `AdaptiveAnalyticsSDK(GlobalConfig())`
- register one symbol
- convert ms timestamps to seconds
- feed `TradeTick`
- expose `state()`
- keep `latest_signal`
- add `on_top_of_book(best_bid, best_ask, bid_vol, ask_vol, timestamp_ms)`

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_adaptive_service.py tests_adaptive_sdk -v`
Expected: PASS.

---

### Task 3: Entry Filter Engine

**Files:**
- Create: `app/entry_filter.py`
- Test: `tests/test_entry_filter.py`

- [ ] **Step 1: Write failing tests**

```python
from dataclasses import dataclass

from adaptive_sdk.types import ExhaustionType
from app.entry_filter import EntryFilterEngine


@dataclass
class DummyState:
    is_ready: bool
    vpin: float
    sell_exhaustion_z: float = 0.0
    buy_exhaustion_z: float = 0.0
    pending_signals_count: int = 0
    buckets_filled: int = 0


def test_entry_filter_warming_until_sdk_ready():
    result = EntryFilterEngine().evaluate(DummyState(False, 0.0), None)
    assert result.market_state == "WARMING"
    assert result.long_filter == "WAIT"
    assert result.short_filter == "WAIT"


def test_entry_filter_blocks_toxic_market():
    result = EntryFilterEngine(vpin_high=0.5).evaluate(DummyState(True, 0.6), None)
    assert result.market_state == "TOXIC"
    assert result.long_filter == "BLOCKED"
    assert result.short_filter == "BLOCKED"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_entry_filter.py -v`
Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement conservative entry filter**

Outputs:

- `market_state`: `WARMING`, `TOXIC`, `READY`
- `long_filter`: `OK`, `WAIT`, `BLOCKED`
- `short_filter`: `OK`, `WAIT`, `BLOCKED`
- `reason`
- optional latest signal summary

Initial conservative mapping:

- not ready: both `WAIT`
- toxic: both `BLOCKED`
- buy exhaustion: `LONG_OK`, short `WAIT`
- sell exhaustion: `SHORT_OK`, long `WAIT`
- no signal: both `WAIT`

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_entry_filter.py -v`
Expected: PASS.

---

### Task 4: Exit Engine

**Files:**
- Create: `app/exit_engine.py`
- Test: `tests/test_exit_engine.py`

- [ ] **Step 1: Write failing tests for hard exits**

```python
from app.assistant_config import AssistantRiskSettings
from app.exit_engine import ExitEngine
from app.position import PositionState


def test_max_loss_triggers_immediate_exit():
    engine = ExitEngine()
    pos = PositionState(symbol="BTCUSDT", amount=0.01, entry_price=65000, unrealized_pnl=-6.0)
    result = engine.evaluate(
        position=pos,
        sdk_state=None,
        latest_signal=None,
        settings=AssistantRiskSettings(auto_exit_enabled=True, max_loss_usdt=5.0),
        now_ms=1000,
    )
    assert result.should_close is True
    assert result.reason == "max_loss"
    assert result.state == "EXIT_ARMED"


def test_auto_exit_off_blocks_close_but_reports_reason():
    engine = ExitEngine()
    pos = PositionState(symbol="BTCUSDT", amount=0.01, entry_price=65000, unrealized_pnl=-6.0)
    result = engine.evaluate(
        position=pos,
        sdk_state=None,
        latest_signal=None,
        settings=AssistantRiskSettings(auto_exit_enabled=False, max_loss_usdt=5.0),
        now_ms=1000,
    )
    assert result.should_close is False
    assert result.reason == "max_loss"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_exit_engine.py -v`
Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement hard exits**

Implement:

- flat/no position -> `NO_POSITION`, no close
- max loss -> hard exit
- toxic VPIN -> hard exit when setting enabled
- opposite high-confidence signal -> hard exit when setting enabled
- auto-exit off suppresses `should_close` but keeps reason/status

- [ ] **Step 4: Add and implement soft exit confirmation tests**

Test holding-time soft exit requires `confirmation_ms` before close.

- [ ] **Step 5: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_exit_engine.py -v`
Expected: PASS.

---

### Task 5: Binance Account Client

**Files:**
- Create: `app/binance_account.py`
- Test: `tests/test_binance_account.py`

- [ ] **Step 1: Write tests for signed request construction**

Use `respx` only if already available; otherwise use a fake transport/helper-level tests to avoid adding dependencies.

Test:

- query strings include `timestamp`
- HMAC SHA256 signature is added
- listen-key start endpoint is called with API key header
- position REST payload is normalized through `PositionState`

- [ ] **Step 2: Implement signed REST helper**

Use:

- `hmac`
- `hashlib`
- `urllib.parse.urlencode`
- `httpx.AsyncClient`

Endpoints:

- `POST /fapi/v1/listenKey`
- `PUT /fapi/v1/listenKey`
- `DELETE /fapi/v1/listenKey`
- `GET /fapi/v3/positionRisk` or configured fallback if Binance rejects v3

- [ ] **Step 3: Implement user-data stream task skeleton**

Use `websockets.connect` against `wss://fstream.binance.com/ws/{listenKey}`.

Parse:

- `ACCOUNT_UPDATE` through `parse_account_update_positions`
- order updates later if needed

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_binance_account.py -v`
Expected: PASS.

---

### Task 6: Order Executor

**Files:**
- Create: `app/order_executor.py`
- Test: `tests/test_order_executor.py`

- [ ] **Step 1: Write tests proving only close orders are built**

```python
from app.order_executor import build_market_close_order
from app.position import PositionState


def test_long_position_closes_with_sell_market_reduce_only():
    pos = PositionState(symbol="BTCUSDT", amount=0.01, entry_price=65000)
    order = build_market_close_order(pos, client_order_id="close-test")
    assert order["side"] == "SELL"
    assert order["type"] == "MARKET"
    assert order["quantity"] == "0.01"
    assert order["reduceOnly"] == "true"


def test_short_position_closes_with_buy_market_reduce_only():
    pos = PositionState(symbol="BTCUSDT", amount=-0.02, entry_price=65000)
    order = build_market_close_order(pos, client_order_id="close-test")
    assert order["side"] == "BUY"
    assert order["quantity"] == "0.02"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `.venv\Scripts\python.exe -m pytest tests/test_order_executor.py -v`
Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement close-order builder and executor**

Builder:

- reject flat positions
- format quantity deterministically without scientific notation
- produce `symbol`, `side`, `type=MARKET`, `quantity`, `reduceOnly=true`, `newClientOrderId`

Executor:

- owns pending close flag
- calls `BinanceAccountClient.signed_post("/fapi/v1/order", params)`
- does not retry blindly
- exposes last error/status

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_order_executor.py -v`
Expected: PASS.

---

### Task 7: Wire Assistant Into WebSocket Session

**Files:**
- Modify: `app/ws_session.py`
- Modify: `app/binance_client.py`
- Test: `tests/test_ws_session.py`

- [ ] **Step 1: Add tests for assistant setting commands**

Test that `set_assistant_settings`, `enable_auto_exit`, and `disable_auto_exit` update session settings and emit status payloads without touching Binance.

- [ ] **Step 2: Add adaptive callback into market client**

Modify `BinanceMarketClient` to accept optional callbacks:

- `on_trade: Callable[[dict], None] | None`
- `on_book_update: Callable[[OrderBook, int | None], None] | None`

Call `on_trade` from `_process_trade_event` after `TradeBuffer.add`.

Call book callback after depth deltas when best bid/ask can be read.

- [ ] **Step 3: Add assistant services to `LiveHeatmapService`**

On symbol connect:

- create `AdaptiveMarketService`
- create/reuse settings
- create entry/exit engines
- start account client only when API keys are present

On frame loop or market events:

- broadcast `entry_filter` while flat
- broadcast `position` when account state exists
- evaluate exit engine while in position
- if `should_close`, call executor

- [ ] **Step 4: Run tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_ws_session.py tests/test_binance_client.py -v`
Expected: PASS.

---

### Task 8: Assistant UI

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `static/style.css`
- Test: existing JS test if applicable, plus manual browser check

- [ ] **Step 1: Add assistant panel markup**

Fields:

- auto-exit checkbox
- max loss input
- max holding time input
- confirmation ms input
- opposite signal checkbox
- toxic VPIN checkbox
- assistant state
- entry filter
- position
- PnL
- exit reason

- [ ] **Step 2: Wire UI events**

On setting change, send:

```js
{ type: "set_assistant_settings", settings: {...} }
```

Handle backend events:

- `assistant_status`
- `entry_filter`
- `position`
- `sdk_signal`
- `exit_status`
- `order_status`
- `account_error`

- [ ] **Step 3: Style for dense trading UI**

Keep controls scan-friendly:

- no nested cards
- compact labels
- stable widths
- warning/error colors

- [ ] **Step 4: Manual browser verification**

Run: `.venv\Scripts\python.exe -m uvicorn app.main:app --reload`

Open: `http://127.0.0.1:8000`

Expected:

- app loads
- connect controls still work
- assistant panel does not overlap heatmap
- settings controls fit in sidebar

---

### Task 9: End-To-End Verification And Docs

**Files:**
- Modify: `README.md`
- Add: `.env.example`
- Modify: `requirements.txt` if new dependencies are added

- [ ] **Step 1: Add `.env.example`**

Include:

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_FAPI_BASE_URL=https://fapi.binance.com
AUTO_EXIT_ENABLED=false
```

- [ ] **Step 2: Update README**

Document:

- real key requirements
- withdrawals disabled
- one-way mode only
- minimum-order live test warning
- no auto entries
- market close behavior

- [ ] **Step 3: Run full test suite**

Run:

```powershell
.venv\Scripts\python.exe -m pytest tests tests_adaptive_sdk
```

Expected: all tests pass.

- [ ] **Step 4: Manual minimum-size live checklist**

Do not automate this in tests. Document checklist:

- start app
- connect symbol
- verify entry filter updates while flat
- enable auto-exit only after account status is OK
- open minimum-size manual position externally
- verify position appears
- trigger/check max-loss or very short max-holding-time close
- verify position reaches zero

---

## Implementation Notes

- Do not add sub-account support in v1.
- Do not add history/replay in v1.
- Do not add automatic entries in v1.
- Do not expose SDK raw calibration in UI in v1.
- Use mocked account/order clients in tests. Real Binance calls are manual only.
- `C:\codex\heatmap-sdk` is not currently a git repository, so commit steps are omitted until a repo is initialized.
