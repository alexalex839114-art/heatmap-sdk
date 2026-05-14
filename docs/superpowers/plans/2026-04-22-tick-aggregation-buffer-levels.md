# Tick Aggregation And Buffer Levels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace viewport-based compression with exchange-aligned tick aggregation, add buffered display levels for edge stability, and keep reconnect and live rendering stable.

**Architecture:** The backend remains the source of truth for live visualization, but `FrameBuilder` stops deriving rows from a percentage viewport. Instead it builds a stable discrete display grid from Binance `tick_size`, an aggregation factor derived from `Compression`, and hidden buffer levels that allow infrequent recenters. The frontend protocol stays mostly unchanged and renders the visible slice only.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, httpx, websockets, pytest, plain HTML/CSS/JS, Canvas 2D renderer

---

## File Structure

### Backend

- Modify: `C:\codex\HEATMAP\app\binance_client.py`
  - fetch and expose Binance `tick_size`
  - keep reconnect/shutdown behavior stable
- Modify: `C:\codex\HEATMAP\app\frame_builder.py`
  - replace `viewport_pct` display logic with tick-grid aggregation
  - maintain grid anchor, visible levels, and buffer levels
  - map trades through the same grid
- Modify: `C:\codex\HEATMAP\app\ws_session.py`
  - treat `compression` as aggregation
  - pass `tick_size` into the frame builder
  - include display-grid metadata in status or reset payloads if useful
- Modify: `C:\codex\HEATMAP\app\settings.py`
  - replace or supplement `VIEWPORT_PCT` with explicit display-grid defaults

### Frontend

- Modify: `C:\codex\HEATMAP\static\app.js`
  - continue sending `compression`
  - optionally surface `display_step` and `tick_size` in UI state
- Modify: `C:\codex\HEATMAP\static\index.html`
  - no required layout change beyond optional display-step/status text

### Tests

- Modify: `C:\codex\HEATMAP\tests\test_frame_builder.py`
  - add failing tests for tick aggregation, buffer reuse, recenter, and trade alignment
- Modify: `C:\codex\HEATMAP\tests\test_binance_client.py`
  - add failing tests for metadata/tick size handling
- Modify: `C:\codex\HEATMAP\tests\test_ws_session.py`
  - add failing tests for aggregation-driven reconnect/session wiring

### Notes

- `C:\codex\HEATMAP\app\models.py` does not need to grow unless the protocol becomes more structured than dict payloads.
- Keep `renderer.js` unchanged unless manual verification shows the new visible slice needs a client-side label or debugging aid.

## Implementation Notes

- Use TDD for each behavioral unit: write the failing test, run it, implement the minimum fix, rerun.
- Do not try to preserve `viewport_pct` semantics inside `FrameBuilder`; that would mix two incompatible models.
- Keep `Compression` in the browser protocol for compatibility, but rename to `aggregation` internally where practical.
- Use hard-coded defaults first:
  - `visible_levels = HEATMAP_HEIGHT`
  - `buffer_levels = 128`
  - `recenter_margin_levels = 32`
- Recenter only when the mid-price leaves the safe region. The design goal is stability, not perfect continuous centering.
- Use `mid_price` as the recenter trigger, with visible-area thresholds at `25%` and `75%`. When recenter happens, place `mid_price` back at the center of the visible window.
- Preserve the existing reconnect fixes in `BinanceMarketClient.stop()` and `LiveHeatmapService._disconnect_market()`.

## Task 1: Add Symbol Tick Size Metadata

**Files:**
- Modify: `C:\codex\HEATMAP\app\binance_client.py`
- Modify: `C:\codex\HEATMAP\tests\test_binance_client.py`

- [ ] **Step 1: Write the failing tests for tick size extraction**

```python
import pytest

from app.binance_client import BinanceMarketClient
from app.order_book import OrderBook
from app.trade_buffer import TradeBuffer


def test_extract_tick_size_from_exchange_info_symbol_filters():
    payload = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                ],
            }
        ]
    }
    client = BinanceMarketClient("BTCUSDT", OrderBook(), TradeBuffer())

    assert client._extract_tick_size(payload) == 0.10


def test_extract_tick_size_raises_when_price_filter_missing():
    payload = {"symbols": [{"symbol": "BTCUSDT", "filters": []}]}
    client = BinanceMarketClient("BTCUSDT", OrderBook(), TradeBuffer())

    with pytest.raises(RuntimeError):
        client._extract_tick_size(payload)
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `python -m pytest C:\codex\HEATMAP\tests\test_binance_client.py -v`
Expected: FAIL because `_extract_tick_size` does not exist

- [ ] **Step 3: Add minimal tick-size extraction implementation**

```python
def _extract_tick_size(self, exchange_info: dict[str, Any]) -> float:
    for symbol_info in exchange_info.get("symbols", []):
        if symbol_info.get("symbol") != self.symbol:
            continue
        for item in symbol_info.get("filters", []):
            if item.get("filterType") == "PRICE_FILTER":
                return float(item["tickSize"])
    raise RuntimeError(f"Missing PRICE_FILTER tick size for {self.symbol}")
```

- [ ] **Step 4: Add metadata fetch wiring and state**

```python
self.tick_size: float | None = None

async def fetch_exchange_info(self, symbol: str) -> dict[str, Any]:
    ...

async def start(self) -> None:
    self.tick_size = self._extract_tick_size(await self.fetch_exchange_info(self.symbol))
    ...
```

- [ ] **Step 5: Run the focused tests again**

Run: `python -m pytest C:\codex\HEATMAP\tests\test_binance_client.py -v`
Expected: PASS for the new extraction tests and existing shutdown tests

- [ ] **Step 6: Commit**

```bash
git add C:\codex\HEATMAP\app\binance_client.py C:\codex\HEATMAP\tests\test_binance_client.py
git commit -m "feat: add binance tick size metadata"
```

## Task 2: Replace Viewport Compression With Tick Aggregation

**Files:**
- Modify: `C:\codex\HEATMAP\app\frame_builder.py`
- Modify: `C:\codex\HEATMAP\tests\test_frame_builder.py`

- [ ] **Step 1: Write the failing tests for grid-based aggregation**

```python
from app.frame_builder import FrameBuilder
from app.order_book import OrderBook


def test_compression_two_merges_two_adjacent_ticks_into_one_bucket():
    book = OrderBook()
    book.load_snapshot(
        bids=[("100.0", "1.0"), ("99.9", "2.0")],
        asks=[("100.1", "1.5"), ("100.2", "2.5")],
    )
    builder = FrameBuilder(
        height=8,
        tick_size=0.1,
        aggregation=2,
        visible_levels=8,
        buffer_levels=0,
    )

    frame = builder.build(book, trades=[])

    assert len(frame.column) == 8
    assert max(frame.column) > 0


def test_trade_mapping_uses_same_grid_as_aggregated_book():
    book = OrderBook()
    book.load_snapshot(
        bids=[("100.0", "1.0")],
        asks=[("100.1", "1.0")],
    )
    builder = FrameBuilder(
        height=8,
        tick_size=0.1,
        aggregation=1,
        visible_levels=8,
        buffer_levels=2,
    )

    frame = builder.build(book, trades=[{"price": 100.1, "qty": 1.0}])

    assert frame.trades
    assert 0 <= frame.trades[0].y < 8
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `python -m pytest C:\codex\HEATMAP\tests\test_frame_builder.py -v`
Expected: FAIL because the new constructor and grid behavior do not exist

- [ ] **Step 3: Refactor `FrameBuilder` constructor and persistent grid state**

```python
class FrameBuilder:
    def __init__(
        self,
        height: int,
        tick_size: float,
        aggregation: int = 1,
        visible_levels: int | None = None,
        buffer_levels: int = 128,
        recenter_margin_levels: int = 32,
    ) -> None:
        self.height = height
        self.tick_size = tick_size
        self.aggregation = max(1, int(aggregation))
        self.visible_levels = visible_levels or height
        self.buffer_levels = buffer_levels
        self.recenter_margin_levels = recenter_margin_levels
```

- [ ] **Step 4: Implement display-step grid aggregation**

```python
self.display_step = self.tick_size * self.aggregation
self.total_levels = self.visible_levels + 2 * self.buffer_levels

def _bucket_index_for_price(self, price: float) -> int | None:
    ...

def _ensure_grid_anchor(self, mid_price: float) -> None:
    ...
```

- [ ] **Step 5: Replace the old viewport-based tests with aggregation and buffer tests**

Add assertions for:
- bucket volume aggregation across merged ticks
- buffer reuse when mid moves slightly
- recenter when mid moves past threshold
- visible slice always equals `visible_levels`

- [ ] **Step 6: Run the focused tests again**

Run: `python -m pytest C:\codex\HEATMAP\tests\test_frame_builder.py -v`
Expected: PASS with the new tick-grid behavior

- [ ] **Step 7: Commit**

```bash
git add C:\codex\HEATMAP\app\frame_builder.py C:\codex\HEATMAP\tests\test_frame_builder.py
git commit -m "feat: add tick aggregated frame builder"
```

## Task 3: Wire Aggregation Into Session Lifecycle

**Files:**
- Modify: `C:\codex\HEATMAP\app\ws_session.py`
- Modify: `C:\codex\HEATMAP\app\settings.py`
- Modify: `C:\codex\HEATMAP\tests\test_ws_session.py`

- [ ] **Step 1: Write the failing tests for connect-time frame builder configuration**

```python
import pytest

from app.ws_session import LiveHeatmapService


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

    await service._connect_symbol("BTCUSDT", compression=3)

    assert service.frame_builder.tick_size == 0.1
    assert service.frame_builder.aggregation == 3
```

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `python -m pytest C:\codex\HEATMAP\tests\test_ws_session.py -v`
Expected: FAIL because the frame builder is still created before client metadata is available

- [ ] **Step 3: Add explicit display-grid settings**

```python
HEATMAP_BUFFER_LEVELS = 128
HEATMAP_RECENTER_MARGIN_LEVELS = 32
```

- [ ] **Step 4: Move frame-builder construction to after successful client start**

```python
await self.client.start()
self.frame_builder = FrameBuilder(
    height=HEATMAP_HEIGHT,
    tick_size=self.client.tick_size or 0.0,
    aggregation=compression,
    visible_levels=HEATMAP_HEIGHT,
    buffer_levels=HEATMAP_BUFFER_LEVELS,
    recenter_margin_levels=HEATMAP_RECENTER_MARGIN_LEVELS,
)
```

- [ ] **Step 5: Expose display metadata in session status or reset payload**

```python
await self.broadcast(
    {
        "type": "reset",
        "tick_size": self.client.tick_size,
        "display_step": self.frame_builder.display_step,
        "buffer_levels": self.frame_builder.buffer_levels,
    }
)
```

- [ ] **Step 6: Run the focused tests again**

Run: `python -m pytest C:\codex\HEATMAP\tests\test_ws_session.py -v`
Expected: PASS and no regression in connect/start/stop/disconnect behavior

- [ ] **Step 7: Commit**

```bash
git add C:\codex\HEATMAP\app\ws_session.py C:\codex\HEATMAP\app\settings.py C:\codex\HEATMAP\tests\test_ws_session.py
git commit -m "feat: wire tick aggregation into live session"
```

## Task 4: Update Frontend Session State

**Files:**
- Modify: `C:\codex\HEATMAP\static\app.js`
- Modify: `C:\codex\HEATMAP\static\index.html`

- [ ] **Step 1: Add a small UI target for display-grid metadata**

```html
<div id="display-step">Display step: -</div>
```

- [ ] **Step 2: Update browser WebSocket handlers to consume reset/status metadata**

```javascript
if (message.type === "reset") {
  if (message.display_step) {
    displayStepEl.textContent = `Display step: ${message.display_step}`;
  }
}
```

- [ ] **Step 3: Verify no protocol change is required for the connect command**

Run: no command
Expected: `connect` payload remains `{ type, symbol, compression }`

- [ ] **Step 4: Run a syntax check on frontend files**

Run: `node --check C:\codex\HEATMAP\static\app.js`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add C:\codex\HEATMAP\static\app.js C:\codex\HEATMAP\static\index.html
git commit -m "feat: show display step metadata"
```

## Task 5: Regression Verification

**Files:**
- Modify as needed based on regressions found in earlier tasks

- [ ] **Step 1: Run the full Python test suite**

Run: `python -m pytest C:\codex\HEATMAP\tests -v`
Expected: PASS

- [ ] **Step 2: Run frontend syntax checks**

Run: `node --check C:\codex\HEATMAP\static\app.js`
Expected: PASS

Run: `node --check C:\codex\HEATMAP\static\renderer.js`
Expected: PASS

- [ ] **Step 3: Run a live smoke test**

Run:

```powershell
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Manual verification:
- connect `BTCUSDT` with `Compression = 1`
- start heatmap and verify status becomes `streaming`
- reconnect with `Compression = 2`
- reconnect with `Compression = 3`
- verify reconnect still works
- verify display step changes
- verify visible price range widens
- verify edge jitter is reduced

- [ ] **Step 4: Commit any final fixes**

```bash
git add C:\codex\HEATMAP
git commit -m "fix: stabilize tick aggregated heatmap"
```

- [ ] **Step 5: Record verification output in README if behavior changed materially**

Update:
- launch notes
- meaning of `Compression`
- any new visible display-step indicator
