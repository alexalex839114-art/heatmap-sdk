# Tick Aggregation And Buffer Levels Design

## Goal

Replace the current display-only `compression` behavior with a correct tick-based aggregation model and add buffered display levels so the live heatmap does not constantly re-anchor at the top and bottom edges.

This design is an incremental correction to the existing live-only Binance heatmap V1. It does not add history, zoom, or exchange expansion. It only fixes how the live price axis is built and how `Compression` should behave.

## Problem Statement

The current implementation treats `compression` primarily as a viewport stretch. That creates two problems:

- the visual effect is weak or inconsistent because the displayed price step does not actually change in a stable way
- the heatmap can visually jitter at the edges because the display axis recenters too often around the latest mid-price

The intended behavior is different:

- if the exchange tick size is `tick_size`
- and the user sets `Compression = N`
- then the displayed price step must become `tick_size * N`

That means aggregation must happen on a discrete price grid derived from exchange metadata, not from a percentage-only viewport transform.

## Scope

### In Scope

- interpret UI `Compression` as display aggregation in ticks
- fetch and use real instrument `tick_size` from Binance Futures metadata
- build frames on a stable display grid
- add hidden `buffer_levels` above and below the visible heatmap
- recenter the grid only when price leaves the safe buffer region
- keep the current live-only browser app and session model

### Out Of Scope

- historical storage or replay
- zoom controls
- Bybit support
- multi-symbol viewing
- configurable buffer parameters in the UI

## Product Decisions

- the UI label remains `Compression` for now to avoid unnecessary churn
- semantically, `Compression` means `aggregation`
- `Compression = 1` means one display step equals one exchange tick
- `Compression = 2` means one display step equals two exchange ticks
- buffered levels are backend-only in this iteration

## Architecture Changes

### Current Model

The current frame builder derives a vertical range from a mid-centered percentage viewport and then distributes liquidity into buckets based on that range.

This is simple, but it does not represent a true exchange-aligned display grid.

### New Model

The new frame builder uses a discrete display grid:

- `tick_size` comes from Binance metadata for the active symbol
- `aggregation = compression`
- `display_step = tick_size * aggregation`
- `visible_levels` is the number of rows rendered in the browser
- `buffer_levels` is the number of hidden rows maintained above and below the visible window
- `total_levels = visible_levels + 2 * buffer_levels`

The backend aggregates order book liquidity into this grid, not into an arbitrary percentage bucket layout.

## Data Flow

1. User enters a symbol and a `Compression` value.
2. Browser sends `connect(symbol, compression)`.
3. Backend loads symbol metadata including `tick_size`.
4. Backend synchronizes the live order book as before.
5. Frame builder initializes or reuses a display grid with:
   - one step size equal to `tick_size * compression`
   - visible levels plus hidden buffer levels
6. On each frame tick, the backend snapshots the current book state and maps each price level into the nearest display bucket.
7. The backend computes a full buffered column, then slices out only the visible region for the browser.
8. The browser renders that visible column exactly as before.

## Display Grid Model

### Grid Parameters

Recommended defaults for this iteration:

- `visible_levels = canvas height`
- `buffer_levels = 128`
- `frame_interval = 100 ms`

These values can remain hard-coded until the live behavior is verified.

### Grid Anchor

The grid is centered around the current market region, but not rebuilt every frame.

At initialization:

- compute the current `mid_price`
- quantize it to the display grid using `display_step`
- build a total range of `total_levels` around that anchor

The important rule is that the display grid is discrete and exchange-aligned.

### Tick-Based Aggregation

For each resting book level:

1. read the raw exchange price and size
2. convert the price into a display-grid index using `display_step`
3. accumulate the size into that bucket

All exchange levels that fall into the same display bucket are summed together.

This is the correct implementation of the user's requested "compressed order book" display behavior.

## Buffered Levels

### Purpose

The visible heatmap should not jump simply because the best bid or best ask moved slightly.

Without a buffer, the display range needs frequent recentering, which causes edge jitter and unstable visual tracking.

### Behavior

The backend maintains extra rows above and below the visible region:

- visible rows are sent to the browser
- buffer rows are retained only for stable mapping

As long as the current mid-price stays inside the safe central region, the same grid anchor is reused.

### Recentering Rule

The grid recenters only when the quantized market center moves beyond a threshold inside the visible area.

A simple initial rule is acceptable:

- use `mid_price` as the reference price
- keep the current grid while `mid_price` remains inside the central half of the visible screen
- rebuild the grid when `mid_price` enters the top quarter or bottom quarter of the visible screen
- after recenter, place `mid_price` back at the center of the visible region

The recenter action should be discrete, not continuous.

## Frame Builder Behavior

The new frame builder owns:

- current grid anchor
- `tick_size`
- `aggregation`
- `display_step`
- `visible_levels`
- `buffer_levels`

On each frame tick it:

1. reads `best_bid` and `best_ask`
2. derives `mid_price`
3. decides whether the current grid can be reused or must be recentered
4. allocates a buffered column with `total_levels`
5. aggregates bid and ask liquidity into the buffered column
6. applies the intensity transform
7. slices the visible region
8. maps trades into visible `y` coordinates using the same grid

## Trade Mapping

Trades must use the exact same display grid as the book.

For each trade:

- compute its display bucket from price and `display_step`
- discard it if it lands outside the visible region
- map it to the visible `y` coordinate after the buffered slice

This keeps trades visually aligned with the aggregated liquidity ladder.

## Backend Changes

### `app/binance_client.py`

- fetch `exchangeInfo` for the active symbol
- extract and expose `tick_size`
- cache per-symbol metadata for reconnects when practical

### `app/ws_session.py`

- keep `compression` in the incoming protocol
- treat it as `aggregation`
- store `tick_size`, `visible_levels`, and `buffer_levels` in session state or frame builder state as needed

### `app/frame_builder.py`

- replace `viewport_pct * compression` logic with a display-grid implementation
- add grid anchoring and buffered slicing
- map trades through the same grid

### `app/main.py`

- no architectural change is required
- protocol payloads may expose `tick_size`, `display_step`, and `buffer_levels` for debugging or UI display

## Frontend Changes

### `static/index.html`

- keep the existing `Compression` input

### `static/app.js`

- continue sending `compression` on `connect`
- optionally display backend-reported `display_step`

### `static/renderer.js`

- no structural rendering change is required
- it continues to consume one visible heatmap column at a time

## Testing Strategy

### Unit Tests

Add or update tests to cover:

- `Compression = 1` maps one exchange tick to one display bucket
- `Compression = 2` maps two adjacent exchange ticks into one display bucket
- aggregated bucket volume equals the sum of merged source levels
- small mid-price movement inside the buffer does not recenter the grid
- larger movement past the buffer threshold does recenter the grid
- trade markers align with the same grid used for the heatmap column

### Live Verification

Manual checks:

- reconnect after changing `Compression` still works reliably
- switching from `1` to `2` to `3` produces an obvious display-step change
- visible price range increases as `Compression` increases
- edge jitter is reduced during normal live movement

## Success Criteria

- `Compression` now changes display price step, not just viewport span
- the live heatmap visually changes in an obvious way when aggregation changes
- the heatmap edges are more stable because the grid is buffered
- reconnect and reconnect-with-new-compression remain stable
- the change fits the current live-only V1 scope without introducing history or zoom complexity
