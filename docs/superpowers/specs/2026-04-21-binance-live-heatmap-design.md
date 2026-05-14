# Binance Live Heatmap V1 Design

## Goal

Build a local, live-only prototype that connects to Binance USDT Futures for a user-selected symbol and renders a real-time order book heatmap in the browser.

The goal of V1 is not historical analysis. The goal is to let the user launch the app, enter a symbol such as `BTCUSDT`, connect to the exchange, and watch a live heatmap update in real time.

## Scope

### In Scope

- Local single-user application
- Binance USDT Futures only
- One symbol at a time
- Real exchange WebSocket data
- Browser UI with:
  - symbol input
  - `Connect WS` button
  - `Start/Stop Heatmap` button
  - connection/status indicator
- Live order book maintenance in memory
- Live heatmap generation at a fixed cadence
- Live trade markers overlaid on the heatmap
- GPU-backed frontend rendering via WebGL

### Out of Scope

- Bybit support
- Historical storage
- Parquet
- Deep Zoom tiles
- Multi-symbol viewing
- Authentication
- State recovery after restart
- User-configurable advanced controls in V1

## Product Decisions

- Exchange: Binance only
- Market: USDT perpetual futures
- Data mode: live only
- Primary user priority: visual smoothness over historical fidelity
- Symbol selection: text input in UI
- Connection lifecycle:
  - `Connect WS` establishes and syncs exchange data
  - `Start Heatmap` begins rendering columns from the live in-memory book
  - `Stop Heatmap` stops heatmap generation without requiring a full reconnect

## Architecture

The system consists of one backend process and one browser frontend.

### Backend

The backend runs on FastAPI and hosts:

- HTTP endpoints for serving the UI
- an app-level WebSocket for browser communication
- a Binance market data collector
- an in-memory order book
- a live frame builder

### Frontend

The frontend is a lightweight browser app that:

- lets the user enter a symbol
- opens a control/data WebSocket to the backend
- receives status updates, heatmap frames, and trade markers
- renders the heatmap in WebGL
- renders trades and simple overlays on top

## Main Components

### 1. Binance Collector

The collector is responsible for:

- connecting to Binance USDT Futures market data streams for the selected symbol
- loading an initial order book snapshot
- consuming incremental depth updates
- consuming trade updates
- maintaining synchronization state

The collector owns all exchange-specific logic and exposes normalized internal state to the rest of the app.

### 2. In-Memory Order Book

The order book stores the current bids and asks for the active symbol.

Responsibilities:

- apply depth deltas
- remove zero-sized levels
- keep bids and asks queryable for frame generation
- expose `best_bid`, `best_ask`, and derived `mid_price`

The order book is the source of truth for live visualization. No persistent storage is used in V1.

### 3. Trade Buffer

Recent trades are buffered in memory and grouped into the current frame interval.

Responsibilities:

- store trades arriving since the last frame tick
- expose trades for the current `100 ms` frame
- clear or roll over once a frame is emitted

### 4. Frame Builder

The frame builder runs on a fixed timer, recommended at `100 ms`.

On each tick it:

- reads the current order book
- calculates `best_bid`, `best_ask`, and `mid_price`
- builds a viewport centered on the current mid-price
- maps order book liquidity into vertical price buckets
- applies `log1p(volume)` normalization
- emits a single heatmap column
- maps current-frame trades into viewport `y` positions

### 5. Browser Session Manager

The backend keeps one active browser visualization session at a time for V1.

Responsibilities:

- accept commands from the UI
- coordinate connect/start/stop/disconnect
- publish status messages
- publish frames and trade markers
- issue reset events when symbol or viewport state changes

## Live Data Flow

1. User opens the page.
2. User enters a symbol, for example `BTCUSDT`.
3. User clicks `Connect WS`.
4. Frontend sends `connect(symbol)` to the backend.
5. Backend connects to Binance Futures streams and synchronizes the local order book.
6. Backend emits status updates: `connecting`, then `syncing`, then `live_ready`.
7. User clicks `Start Heatmap`.
8. Frontend sends `start_heatmap()`.
9. Backend begins frame generation every `100 ms`.
10. Each frame is pushed to the browser.
11. Frontend appends the new column on the right and shifts older columns left.
12. If the user clicks `Stop Heatmap`, frame generation stops but the exchange connection may remain active.

## Heatmap Model

### Viewport Strategy

V1 uses a mid-centered viewport instead of an absolute historical price axis.

This means:

- the vertical axis is always centered around the current market region
- the heatmap is optimized for live readability
- the display follows the market rather than representing a fixed absolute historical range

This is acceptable because V1 is explicitly live-only and not intended for historical replay.

### Vertical Axis

Recommended defaults:

- `height = 768` or `1024` rows
- fixed viewport around `mid_price`
- viewport width defined either by:
  - a percentage band around mid, such as `+/-1.5%`, or
  - a fixed number of price ticks around mid

For V1, a fixed tick-based or percentage-based viewport may be hard-coded.

### Price Bucketing

Each frame maps current bid and ask levels into `height` vertical buckets.

For each bucket:

- sum all resting liquidity whose price falls inside the bucket
- treat bids and asks uniformly for intensity purposes
- produce one scalar intensity value

### Intensity Function

For each bucket:

1. compute total resting volume
2. transform with `log1p(volume)`
3. normalize into `0..255`

The output heatmap column is a `uint8` array of length `height`.

### Trades Overlay

Trades received during the current frame window are mapped into the same viewport.

Each trade marker includes:

- timestamp
- price
- quantity
- derived `y` coordinate in the current frame
- optional side/color metadata if available

The frontend renders trades as overlay markers on top of the heatmap.

## UI Design

The V1 page includes only the controls needed for the first live prototype.

### Controls

- `Symbol` input
- `Connect WS` button
- `Start Heatmap` button
- status line

### Status Values

Recommended states:

- `disconnected`
- `connecting`
- `syncing`
- `live_ready`
- `streaming`
- `stopped`
- `error`

### Button Behavior

#### Connect WS

- reads the symbol from the input
- requests backend connection for that symbol
- disabled while a connection attempt is already in progress

#### Start Heatmap

- enabled only after the order book is synchronized
- starts frame generation and rendering
- changes label to `Stop Heatmap` while active

#### Stop Heatmap

- stops frame emission
- keeps the underlying market data connection alive unless the user explicitly disconnects or changes symbol

## Browser Protocol

The frontend and backend communicate over one application WebSocket.

### Client Commands

- `connect(symbol)`
- `start_heatmap()`
- `stop_heatmap()`
- `disconnect()`

### Server Events

#### `status`

Carries current lifecycle state and optional metadata.

Example payload fields:

- `state`
- `symbol`
- `message`

#### `frame`

Carries one heatmap time slice.

Example payload fields:

- `timestamp`
- `column` as `uint8[height]`
- `mid_price`
- `best_bid`
- `best_ask`

#### `trades`

Carries trade markers associated with the most recent frame interval.

Example payload fields:

- `timestamp`
- `items`

Each item may include:

- `price`
- `qty`
- `y`

#### `reset`

Instructs the client to clear current buffers and restart rendering state.

Used when:

- symbol changes
- viewport logic resets
- stream resynchronization invalidates the current visual state

#### `error`

Carries a user-visible error message.

## Rendering Strategy

### WebGL Base Layer

The heatmap is rendered in WebGL for smoother scrolling and better performance under continuous updates.

Recommended model:

- maintain a rolling texture or column buffer
- append each new `uint8` column at the right edge
- shift the visual window left as new columns arrive

### Overlay Layer

Trades and simple guides can be drawn as:

- a second WebGL pass, or
- a lightweight 2D canvas overlay above the WebGL surface

For V1, either is acceptable as long as the rendering remains smooth and the architecture stays simple.

## Error Handling

The app should fail visibly and simply.

Cases to handle:

- invalid symbol input
- Binance connection failure
- order book sync failure
- stream interruption
- backend WebSocket disconnect

Expected behavior:

- update status line
- send `error` event to the UI
- disable heatmap start when the book is not synchronized
- attempt clean reset when reconnecting to the same or a different symbol

## Performance Targets

These are practical targets for V1 rather than hard guarantees.

- frame cadence: `100 ms`
- visual behavior: smooth enough to watch continuously without obvious stutter
- startup path: user can reach a visible live heatmap quickly after connecting
- memory profile: bounded rolling in-memory window rather than unbounded accumulation

## Suggested Internal Modules

Recommended file layout:

```text
project/
project/
+-- app/
|   +-- main.py
|   +-- api.py
|   +-- ws_session.py
|   +-- models.py
|   +-- binance_client.py
|   +-- order_book.py
|   +-- trade_buffer.py
|   +-- frame_builder.py
|   `-- settings.py
+-- static/
|   +-- index.html
|   +-- app.js
|   +-- renderer.js
|   `-- style.css
`-- requirements.txt
```

This keeps exchange integration, state management, and rendering concerns separated.

## Testing Strategy

V1 should include focused tests for the logic that can break correctness most easily.

Priority areas:

- order book delta application
- frame bucketing logic
- intensity normalization
- trade-to-viewport mapping
- connect/start/stop state transitions

Manual verification is also required:

- connect to a real symbol
- observe transition to synchronized state
- start heatmap
- verify live scrolling and trade overlay
- stop and restart without page reload
- change symbol and confirm reset behavior

## Future Extensions

The V1 architecture should leave room for later additions without forcing a rewrite.

Likely V2+ additions:

- Bybit support
- historical capture and playback
- Parquet storage
- zoomable historical views
- configurable viewport and frame interval
- multiple overlays and indicators

## Non-Goals for V1

The following are explicitly not success criteria for the first version:

- production-grade exchange resiliency
- absolute-price historical fidelity
- long-term data retention
- advanced chart controls
- exact Bookmap feature parity

## Success Criteria

V1 is successful if:

- the user can type a Binance USDT Futures symbol
- the app connects and synchronizes a live order book
- the user can start the heatmap without reloading the page
- the browser shows continuously updating heatmap columns
- live trades appear over the map
- stopping and restarting the heatmap works predictably
