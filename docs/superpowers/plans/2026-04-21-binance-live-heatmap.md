# Binance Live Heatmap V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local live-only Binance USDT Futures heatmap app that lets the user enter a symbol, connect to exchange data, and start or stop real-time heatmap rendering in the browser.

**Architecture:** A FastAPI backend owns Binance connectivity, in-memory market state, and frame generation. A minimal browser frontend uses one app-level WebSocket to control the session and receive status, frame, and trade events, rendering the rolling heatmap with WebGL plus a simple overlay layer.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, httpx, websockets, pydantic, pytest, plain HTML/CSS/JS, WebGL

---

## File Structure

### Backend

- Create: `C:\codex\HEATMAP\app\main.py`
  - FastAPI app factory, startup wiring, static mounts
- Create: `C:\codex\HEATMAP\app\api.py`
  - HTTP routes for index page and health endpoint
- Create: `C:\codex\HEATMAP\app\settings.py`
  - runtime constants: frame cadence, viewport size, Binance URLs
- Create: `C:\codex\HEATMAP\app\models.py`
  - typed message models and DTOs for status, frame, trades, commands
- Create: `C:\codex\HEATMAP\app\order_book.py`
  - order book state, snapshot load, delta application, top-of-book helpers
- Create: `C:\codex\HEATMAP\app\trade_buffer.py`
  - current-frame trade accumulation and drain logic
- Create: `C:\codex\HEATMAP\app\frame_builder.py`
  - viewport bucketing, intensity normalization, trade-to-y mapping
- Create: `C:\codex\HEATMAP\app\binance_client.py`
  - Binance REST snapshot fetch plus WebSocket stream management
- Create: `C:\codex\HEATMAP\app\ws_session.py`
  - app-level browser command handling, connect/start/stop/disconnect state machine

### Frontend

- Create: `C:\codex\HEATMAP\static\index.html`
  - symbol input, buttons, status line, canvas containers
- Create: `C:\codex\HEATMAP\static\style.css`
  - minimal layout and status styling
- Create: `C:\codex\HEATMAP\static\app.js`
  - UI wiring, browser WebSocket, button state transitions
- Create: `C:\codex\HEATMAP\static\renderer.js`
  - WebGL rolling heatmap renderer plus simple trade overlay

### Tests

- Create: `C:\codex\HEATMAP\tests\test_order_book.py`
- Create: `C:\codex\HEATMAP\tests\test_trade_buffer.py`
- Create: `C:\codex\HEATMAP\tests\test_frame_builder.py`
- Create: `C:\codex\HEATMAP\tests\test_ws_session.py`

### Project Files

- Create: `C:\codex\HEATMAP\requirements.txt`
- Create: `C:\codex\HEATMAP\README.md`

## Implementation Notes

- Use TDD on the Python logic first: order book, trade buffer, frame builder, session state machine.
- Keep Binance-specific sequence reconciliation isolated in `binance_client.py`.
- Defer production-grade reconnect logic. V1 needs a clean happy path and a visible failure path.
- Keep frontend free of frameworks.
- Use fixed defaults in `settings.py`:
  - frame cadence `100 ms`
  - heatmap height `768`
  - rolling width `2000`
  - viewport band `+/-1.5%` around mid-price

## Task 1: Scaffold Project Layout

**Files:**
- Create: `C:\codex\HEATMAP\app\main.py`
- Create: `C:\codex\HEATMAP\app\api.py`
- Create: `C:\codex\HEATMAP\app\settings.py`
- Create: `C:\codex\HEATMAP\app\models.py`
- Create: `C:\codex\HEATMAP\static\index.html`
- Create: `C:\codex\HEATMAP\static\style.css`
- Create: `C:\codex\HEATMAP\static\app.js`
- Create: `C:\codex\HEATMAP\static\renderer.js`
- Create: `C:\codex\HEATMAP\requirements.txt`
- Create: `C:\codex\HEATMAP\README.md`

- [ ] **Step 1: Create package directories and empty module markers**

```text
app/
static/
tests/
```

- [ ] **Step 2: Add minimal dependency file**

```text
fastapi
uvicorn[standard]
httpx
websockets
pydantic
pytest
pytest-asyncio
```

- [ ] **Step 3: Add minimal FastAPI bootstrap**

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
```

- [ ] **Step 4: Add bare index route and placeholder static page**

```python
from fastapi.responses import FileResponse

def get_index() -> FileResponse:
    return FileResponse("static/index.html")
```

- [ ] **Step 5: Run app startup smoke check**

Run: `python -m uvicorn app.main:app --reload`
Expected: server starts and serves `GET /`

- [ ] **Step 6: Commit**

```bash
git add app static requirements.txt README.md
git commit -m "chore: scaffold live heatmap project"
```

## Task 2: Order Book Core

**Files:**
- Create: `C:\codex\HEATMAP\app\order_book.py`
- Test: `C:\codex\HEATMAP\tests\test_order_book.py`

- [ ] **Step 1: Write the failing tests for snapshot load and delta application**

```python
def test_load_snapshot_sets_best_bid_and_ask():
    book = OrderBook()
    book.load_snapshot(bids=[("100.0", "2.0")], asks=[("101.0", "3.0")])
    assert book.best_bid() == 100.0
    assert book.best_ask() == 101.0

def test_apply_delta_updates_and_removes_levels():
    book = OrderBook()
    book.load_snapshot(bids=[("100.0", "2.0")], asks=[("101.0", "3.0")])
    book.apply_delta(bids=[("100.0", "0")], asks=[("102.0", "4.0")])
    assert book.best_bid() is None
    assert book.best_ask() == 101.0
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest C:\codex\HEATMAP\tests\test_order_book.py -v`
Expected: FAIL because `OrderBook` does not exist

- [ ] **Step 3: Write minimal `OrderBook` implementation**

```python
class OrderBook:
    def __init__(self) -> None:
        self.bids = {}
        self.asks = {}
```

- [ ] **Step 4: Expand implementation to pass tests**

```python
def load_snapshot(self, bids, asks):
    self.bids = {float(p): float(q) for p, q in bids if float(q) > 0}
    self.asks = {float(p): float(q) for p, q in asks if float(q) > 0}
```

- [ ] **Step 5: Run tests and verify pass**

Run: `pytest C:\codex\HEATMAP\tests\test_order_book.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/order_book.py tests/test_order_book.py
git commit -m "feat: add order book core"
```

## Task 3: Trade Buffer

**Files:**
- Create: `C:\codex\HEATMAP\app\trade_buffer.py`
- Test: `C:\codex\HEATMAP\tests\test_trade_buffer.py`

- [ ] **Step 1: Write failing tests for append and drain behavior**

```python
def test_trade_buffer_drains_only_current_items():
    buf = TradeBuffer()
    buf.add({"price": 100.0, "qty": 1.0})
    assert buf.drain() == [{"price": 100.0, "qty": 1.0}]
    assert buf.drain() == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest C:\codex\HEATMAP\tests\test_trade_buffer.py -v`
Expected: FAIL because `TradeBuffer` does not exist

- [ ] **Step 3: Implement minimal buffer**

```python
class TradeBuffer:
    def __init__(self) -> None:
        self._items = []
```

- [ ] **Step 4: Add `add()` and `drain()`**

```python
def drain(self):
    items = self._items
    self._items = []
    return items
```

- [ ] **Step 5: Run tests and verify pass**

Run: `pytest C:\codex\HEATMAP\tests\test_trade_buffer.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/trade_buffer.py tests/test_trade_buffer.py
git commit -m "feat: add trade buffer"
```

## Task 4: Frame Builder

**Files:**
- Create: `C:\codex\HEATMAP\app\frame_builder.py`
- Modify: `C:\codex\HEATMAP\app\order_book.py`
- Test: `C:\codex\HEATMAP\tests\test_frame_builder.py`

- [ ] **Step 1: Write failing tests for viewport bucketing and normalization**

```python
def test_build_frame_returns_uint8_column_with_expected_height():
    book = OrderBook()
    book.load_snapshot(
        bids=[("100.0", "2.0"), ("99.5", "1.0")],
        asks=[("100.5", "3.0"), ("101.0", "1.5")],
    )
    builder = FrameBuilder(height=8, viewport_pct=0.01)
    frame = builder.build(book, trades=[])
    assert len(frame.column) == 8
    assert all(0 <= value <= 255 for value in frame.column)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest C:\codex\HEATMAP\tests\test_frame_builder.py -v`
Expected: FAIL because `FrameBuilder` does not exist

- [ ] **Step 3: Implement minimal frame DTO and builder**

```python
class FrameBuilder:
    def build(self, book, trades):
        return Frame(column=[0] * self.height, trades=[])
```

- [ ] **Step 4: Implement viewport math and `log1p` intensity mapping**

```python
volume = bucket_volumes[index]
intensity = int(min(255, round(255 * (math.log1p(volume) / max_value))))
```

- [ ] **Step 5: Run tests and verify pass**

Run: `pytest C:\codex\HEATMAP\tests\test_frame_builder.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/frame_builder.py app/order_book.py tests/test_frame_builder.py
git commit -m "feat: build live heatmap frames"
```

## Task 5: Browser Session State Machine

**Files:**
- Create: `C:\codex\HEATMAP\app\ws_session.py`
- Modify: `C:\codex\HEATMAP\app\models.py`
- Test: `C:\codex\HEATMAP\tests\test_ws_session.py`

- [ ] **Step 1: Write failing tests for connect/start/stop transitions**

```python
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
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest C:\codex\HEATMAP\tests\test_ws_session.py -v`
Expected: FAIL because `BrowserSession` does not exist

- [ ] **Step 3: Implement minimal state model**

```python
class BrowserSession:
    def __init__(self) -> None:
        self.state = "disconnected"
        self.symbol = None
```

- [ ] **Step 4: Add validated transition methods**

```python
def mark_synced(self, symbol):
    self.symbol = symbol
    self.state = "live_ready"
```

- [ ] **Step 5: Run tests and verify pass**

Run: `pytest C:\codex\HEATMAP\tests\test_ws_session.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/ws_session.py app/models.py tests/test_ws_session.py
git commit -m "feat: add browser session state machine"
```

## Task 6: Binance Connectivity

**Files:**
- Create: `C:\codex\HEATMAP\app\binance_client.py`
- Modify: `C:\codex\HEATMAP\app\settings.py`
- Modify: `C:\codex\HEATMAP\app\order_book.py`
- Test: `C:\codex\HEATMAP\tests\test_order_book.py`

- [ ] **Step 1: Write failing tests for parsing Binance snapshot and delta payloads**

```python
def test_apply_binance_depth_event_updates_book():
    book = OrderBook()
    book.load_snapshot(bids=[("100.0", "1.0")], asks=[("101.0", "1.0")])
    apply_depth_event(book, {"b": [["100.0", "2.0"]], "a": [["101.0", "0"]]})
    assert book.bids[100.0] == 2.0
    assert 101.0 not in book.asks
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest C:\codex\HEATMAP\tests\test_order_book.py -v`
Expected: FAIL because `apply_depth_event` does not exist

- [ ] **Step 3: Implement REST snapshot fetch and delta translation helpers**

```python
async def fetch_snapshot(symbol: str) -> dict:
    async with httpx.AsyncClient() as client:
        response = await client.get(...)
        response.raise_for_status()
        return response.json()
```

- [ ] **Step 4: Implement WebSocket stream reader for depth and aggTrade**

```python
async with websockets.connect(stream_url) as ws:
    async for raw_message in ws:
        payload = json.loads(raw_message)
```

- [ ] **Step 5: Run focused tests and a manual smoke connection**

Run: `pytest C:\codex\HEATMAP\tests\test_order_book.py -v`
Expected: PASS

Run: `python -m uvicorn app.main:app --reload`
Expected: backend starts; manual connection code reaches Binance

- [ ] **Step 6: Commit**

```bash
git add app/binance_client.py app/settings.py app/order_book.py tests/test_order_book.py
git commit -m "feat: add binance market data client"
```

## Task 7: Wire Backend Session and App WebSocket

**Files:**
- Modify: `C:\codex\HEATMAP\app\main.py`
- Modify: `C:\codex\HEATMAP\app\api.py`
- Modify: `C:\codex\HEATMAP\app\ws_session.py`
- Modify: `C:\codex\HEATMAP\app\models.py`
- Test: `C:\codex\HEATMAP\tests\test_ws_session.py`

- [ ] **Step 1: Write failing tests for command handling and emitted status events**

```python
@pytest.mark.asyncio
async def test_connect_command_sets_connecting_status():
    session = BrowserSession()
    event = await session.handle_command({"type": "connect", "symbol": "BTCUSDT"})
    assert event["type"] == "status"
    assert event["state"] == "connecting"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest C:\codex\HEATMAP\tests\test_ws_session.py -v`
Expected: FAIL because `handle_command` does not exist

- [ ] **Step 3: Implement browser WebSocket endpoint and command dispatch**

```python
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
```

- [ ] **Step 4: Connect session state to Binance client and frame scheduler hooks**

```python
if command["type"] == "start_heatmap":
    self.state = "streaming"
```

- [ ] **Step 5: Run tests and manual WebSocket smoke check**

Run: `pytest C:\codex\HEATMAP\tests\test_ws_session.py -v`
Expected: PASS

Run: `python -m uvicorn app.main:app --reload`
Expected: browser can open app WebSocket successfully

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/api.py app/ws_session.py app/models.py tests/test_ws_session.py
git commit -m "feat: wire app websocket session"
```

## Task 8: Frontend Control Surface

**Files:**
- Modify: `C:\codex\HEATMAP\static\index.html`
- Modify: `C:\codex\HEATMAP\static\style.css`
- Modify: `C:\codex\HEATMAP\static\app.js`

- [ ] **Step 1: Implement minimal control markup**

```html
<input id="symbol" value="BTCUSDT" />
<button id="connect-btn">Connect WS</button>
<button id="toggle-btn" disabled>Start Heatmap</button>
<div id="status">disconnected</div>
<canvas id="heatmap"></canvas>
```

- [ ] **Step 2: Wire browser WebSocket and button event handlers**

```javascript
connectButton.addEventListener("click", () => {
  socket.send(JSON.stringify({ type: "connect", symbol: symbolInput.value }));
});
```

- [ ] **Step 3: Update button enablement from status events**

```javascript
if (event.state === "live_ready") {
  toggleButton.disabled = false;
}
```

- [ ] **Step 4: Manual smoke test in browser**

Run: `python -m uvicorn app.main:app --reload`
Expected: page loads and buttons change status as messages arrive

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/style.css static/app.js
git commit -m "feat: add live heatmap controls"
```

## Task 9: WebGL Rolling Renderer

**Files:**
- Modify: `C:\codex\HEATMAP\static\renderer.js`
- Modify: `C:\codex\HEATMAP\static\app.js`

- [ ] **Step 1: Implement renderer bootstrap and texture allocation**

```javascript
export function createRenderer(canvas, width, height) {
  const gl = canvas.getContext("webgl");
  return { gl, width, height };
}
```

- [ ] **Step 2: Implement `appendColumn(column)` to roll the visible heatmap**

```javascript
function appendColumn(column) {
  // shift texture or CPU buffer, then upload newest column
}
```

- [ ] **Step 3: Implement simple trade overlay rendering**

```javascript
function drawTrades(trades) {
  overlayContext.clearRect(0, 0, width, height);
}
```

- [ ] **Step 4: Connect incoming `frame` and `trades` events to renderer**

```javascript
if (event.type === "frame") renderer.appendColumn(event.column);
if (event.type === "trades") renderer.drawTrades(event.items);
```

- [ ] **Step 5: Manual browser verification**

Run: `python -m uvicorn app.main:app --reload`
Expected: columns visibly scroll from right to left with live updates

- [ ] **Step 6: Commit**

```bash
git add static/renderer.js static/app.js
git commit -m "feat: render rolling live heatmap"
```

## Task 10: End-to-End Polish and Docs

**Files:**
- Modify: `C:\codex\HEATMAP\README.md`
- Modify: `C:\codex\HEATMAP\app\settings.py`
- Modify: `C:\codex\HEATMAP\app\main.py`
- Modify: `C:\codex\HEATMAP\static\style.css`

- [ ] **Step 1: Add a `/health` endpoint and visible error messages**

```python
@router.get("/health")
def health():
    return {"ok": True}
```

- [ ] **Step 2: Document setup and run flow in `README.md`**

```text
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

- [ ] **Step 3: Run full backend test suite**

Run: `pytest C:\codex\HEATMAP\tests -v`
Expected: PASS

- [ ] **Step 4: Run final manual acceptance check**

Checklist:
- open the app
- enter `BTCUSDT`
- click `Connect WS`
- wait for synced status
- click `Start Heatmap`
- observe live heatmap updates
- click `Stop Heatmap`
- click `Start Heatmap` again

- [ ] **Step 5: Commit**

```bash
git add README.md app static tests
git commit -m "docs: finalize live heatmap prototype"
```

## Execution Notes

- If there is no Git repository, skip commit steps until the repository is initialized.
- If Binance sequence synchronization proves unstable, keep the recovery path simple:
  - set status to `error`
  - clear local book
  - require reconnect
- Prefer a working rolling renderer over a sophisticated shader pipeline in V1.
- Do not add historical storage during implementation.
