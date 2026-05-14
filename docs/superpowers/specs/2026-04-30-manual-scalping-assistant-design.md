# Manual Scalping Assistant Design

## Goal

Extend the existing Binance live heatmap into a one-symbol manual scalping assistant.

The trader enters positions manually in an external terminal. The assistant reads live market data, evaluates pre-entry conditions with `adaptive-sdk`, tracks the real Binance USD-M Futures position, and closes the position with a market order when exit conditions are met.

The assistant is deliberately not a full trading bot. It must never open, add to, or reverse a position.

## Context

The existing `heatmap` project already provides:

- Binance USD-M Futures public market streams for depth and aggregate trades
- synchronized local order book state
- a `TradeBuffer` with Binance `aggTrade` data including `is_buyer_maker`
- a `FrameBuilder` that emits heatmap columns and trade overlays
- a browser UI over a FastAPI WebSocket control channel

The `adaptive-sdk` project provides:

- `AdaptiveAnalyticsSDK`
- VPIN toxicity filtering
- buy/sell exhaustion signals
- order book imbalance support via `BookSnapshot`
- typed `Signal`, `Outcome`, and state snapshots

This design combines both projects around the existing heatmap UI.

## Product Decisions

- One symbol at a time.
- Binance USDT-M perpetual futures only.
- One-way position mode only.
- Manual entry only.
- Automatic exit is allowed.
- Exit order type is market.
- Max loss is configured in USDT per trade.
- API keys are real Binance keys stored in `.env`.
- Sub-account isolation is recommended later, but out of scope for v1.

## Non-Negotiable Safety Rules

The trading executor must expose only one trading capability: close the current position.

It must not contain public methods for:

- opening long
- opening short
- increasing position size
- reversing position
- placing arbitrary orders

Before sending a close order, the assistant must verify:

- auto-exit is enabled for the current session
- one-way mode is assumed/configured
- the active symbol matches the configured symbol
- the tracked position amount is non-zero
- no close attempt is already in progress
- the close side reduces the current position

For one-way mode:

- if `positionAmt > 0`, close with `SELL MARKET` for `abs(positionAmt)`
- if `positionAmt < 0`, close with `BUY MARKET` for `abs(positionAmt)`

The executor should submit a reduce-only market close when supported by the selected account mode and request shape. Binance USD-M Futures `MARKET` orders require `quantity`; `reduceOnly` is available in one-way mode and cannot be sent in Hedge Mode according to the Binance USD-M Futures New Order documentation.

## API Keys

Market data remains public and does not require keys.

Account and exit functionality require:

- Binance API key
- Binance API secret
- USD-M Futures account access
- futures trade permission, because the assistant must close positions
- withdrawals disabled on the key

Configuration lives in `.env`, never in the browser UI:

```env
BINANCE_API_KEY=...
BINANCE_API_SECRET=...
BINANCE_FAPI_BASE_URL=https://fapi.binance.com
AUTO_EXIT_ENABLED=false
```

`AUTO_EXIT_ENABLED` defaults to `false`; the UI can enable auto-exit for the current running session.

## UI Scope

The existing heatmap remains the primary screen.

Add a compact assistant panel with:

- connection/account status
- selected symbol
- auto-exit toggle
- position side
- position size
- entry price
- unrealized PnL
- holding time
- assistant state
- latest entry filter state
- latest SDK signal
- latest exit reason

Minimal user-configurable controls:

- `Auto-exit ON/OFF`
- `Symbol`
- `Max loss USDT`
- `Max holding time sec`
- `Confirmation ms`
- `Opposite signal exit ON/OFF`
- `Toxic VPIN exit ON/OFF`

Do not expose raw SDK calibration values in the trading UI for v1:

- `z_thresholds`
- `flow_lambda`
- `vpin_mid`
- `vpin_high`
- bucket sizing
- classification mode

Those belong in code/config presets until live behavior is understood.

## Assistant States

The assistant has a small state machine:

- `NO_POSITION`: no active position on the configured symbol
- `ENTRY_FILTERING`: no position; SDK is evaluating pre-entry conditions
- `TRACKING`: position exists and no exit pressure is active
- `WARNING`: soft exit pressure is active but not confirmed
- `EXIT_ARMED`: confirmed exit condition exists
- `CLOSING`: market close order has been sent or is being confirmed
- `CLOSED`: position reached zero after an assistant close
- `ERROR`: recoverable API or stream error
- `DESYNC`: account state is ambiguous; automatic trading is blocked

`DESYNC` blocks new close orders unless a REST fallback confirms a non-zero position for the configured symbol and the close path can be made unambiguous.

## Pre-Entry Filter

Before a position exists, `adaptive-sdk` is used as a filter, not as an entry bot.

The pre-entry output should answer:

- is the SDK warmed up?
- is the market toxic?
- is long currently acceptable?
- is short currently acceptable?
- what was the latest exhaustion signal?
- how strong was it?

Suggested states:

- `WARMING`: SDK has not met warmup gates
- `TOXIC`: VPIN is above the toxic threshold
- `LONG_OK`: long entries can be considered
- `SHORT_OK`: short entries can be considered
- `WAIT`: no strong edge or conflicting signals
- `BLOCKED`: conditions are actively poor for both directions

The pre-entry filter never opens a trade. It only informs the trader before manual entry.

## In-Position Exit Engine

Once a real position appears, the assistant switches from entry filtering to position management.

Exit conditions are split into hard exits and soft exits.

Hard exits close immediately:

- `max_loss`: unrealized PnL is less than or equal to `-Max loss USDT`
- `opposite_signal_high_confidence`: SDK emits a strong opposite signal
- `toxic_vpin`: VPIN reaches or exceeds the toxic threshold while in position

Soft exits require confirmation for `Confirmation ms`:

- `flow_against`: aggressive flow moves against the current position
- `obi_against`: order book imbalance moves against the current position
- `opposite_signal_weak`: SDK emits a weaker opposite signal
- `max_holding_time`: holding time exceeds `Max holding time sec`

For a long position:

- opposite SDK pressure is sell-side exhaustion / sell pressure depending on the exact SDK signal interpretation selected during implementation
- negative OBI or sell-flow dominance is adverse

For a short position:

- opposite SDK pressure is buy-side exhaustion / buy pressure depending on the exact SDK signal interpretation selected during implementation
- positive OBI or buy-flow dominance is adverse

The exact mapping between SDK exhaustion type and trade direction should be implemented conservatively and covered by tests, because exhaustion can mean local reversal rather than continuation.

## Data Flow

1. Browser connects to one symbol.
2. Backend starts Binance public market stream as it does today.
3. Backend starts Binance user-data/account tracking using API keys.
4. Market trades are converted to `TradeTick` and fed into `AdaptiveAnalyticsSDK`.
5. Top-of-book snapshots are converted to `BookSnapshot` and fed into `AdaptiveAnalyticsSDK`.
6. If no position exists, `EntryFilterEngine` publishes pre-entry status to the UI.
7. If a position appears, `PositionTracker` publishes side, size, entry, PnL, and holding time.
8. `ExitEngine` evaluates SDK state, position state, and UI risk settings.
9. If an exit is triggered and auto-exit is enabled, `OrderExecutor` submits a market close.
10. User-data updates confirm whether the position reached zero.
11. UI displays the final close state and reason.

## Backend Components

### `AdaptiveMarketService`

Owns one `AdaptiveAnalyticsSDK` instance for the active symbol.

Responsibilities:

- register symbol with a chosen SDK config preset
- convert Binance trades to `TradeTick`
- convert top-of-book to `BookSnapshot`
- expose latest SDK state and latest emitted signal

### `EntryFilterEngine`

Consumes SDK state and latest signal while no position is active.

Outputs:

- market state
- long filter state
- short filter state
- latest reason

### `BinanceAccountClient`

Handles signed Binance USD-M Futures account access.

Responsibilities:

- create/maintain user-data stream
- process `ACCOUNT_UPDATE`
- process order updates if used
- provide REST fallback for current position
- expose account connectivity status

### `PositionTracker`

Maintains normalized one-way position state for the configured symbol.

Fields:

- symbol
- position amount
- side
- entry price
- break-even price if available
- unrealized PnL
- update timestamp
- holding start timestamp

### `ExitEngine`

Evaluates hard and soft exits.

Inputs:

- position state
- SDK state
- latest SDK signal
- top-of-book imbalance
- configured risk settings

Outputs:

- assistant state
- exit reason
- whether close should be sent

### `OrderExecutor`

Only closes the current position.

Responsibilities:

- build signed `POST /fapi/v1/order`
- send `MARKET` close with correct side and quantity
- include a unique client order id
- prevent duplicate close attempts while one is pending
- surface Binance rejection codes to the UI

## Frontend Changes

### `static/index.html`

Add a compact assistant panel near the current controls.

### `static/app.js`

Extend WebSocket command/payload handling for:

- assistant settings
- account status
- position status
- entry filter status
- exit status
- close order status

### `static/renderer.js`

Keep the existing heatmap renderer. Optionally add signal markers later, but v1 can display SDK state in the assistant panel without changing canvas rendering.

## WebSocket Protocol Additions

Backend-to-frontend events:

- `assistant_status`
- `entry_filter`
- `position`
- `sdk_signal`
- `exit_status`
- `order_status`
- `account_error`

Frontend-to-backend commands:

- `set_assistant_settings`
- `enable_auto_exit`
- `disable_auto_exit`

The existing `connect`, `start_heatmap`, and `stop_heatmap` commands remain.

## Error Handling

The assistant must enter `ERROR` or `DESYNC` instead of guessing when:

- API keys are missing
- signed request authentication fails
- user-data stream expires and cannot be restored
- REST position and stream position disagree materially
- symbol changes while a position is open
- Binance rejects a close order
- close order result is unknown after a network error

If order result is unknown, the system must query position state before deciding whether another close attempt is safe.

## Testing Strategy

### Unit Tests

Add tests for:

- Binance position payload parsing
- one-way long/short side derivation from `positionAmt`
- hard max-loss exit
- toxic VPIN exit
- opposite-signal exit
- soft-exit confirmation timing
- duplicate-close prevention
- no close when auto-exit is disabled
- no close when no position exists
- long close side is `SELL`
- short close side is `BUY`

### Integration Tests

Use mocked Binance account endpoints and mocked user-data events.

Cover:

- manual position appears after account update
- assistant transitions from entry filtering to tracking
- exit condition produces exactly one close request
- position-zero update transitions to `CLOSED`
- rejected close transitions to `ERROR`

### Manual Live Checks

On minimum real order size:

- connect to one symbol
- confirm pre-entry panel updates before any position
- enter manually in external terminal
- confirm assistant detects position
- confirm max-loss close works
- confirm auto-exit toggle blocks close when off
- confirm no order is sent for entry signals while flat

## Success Criteria

- The heatmap still works for one live Binance symbol.
- SDK pre-entry states are visible before any position exists.
- Manual position entry is detected without user action in the assistant.
- Position state is visible in the UI.
- Auto-exit can close a real one-way position with a market order.
- The assistant never opens, increases, or reverses a position.
- Missing keys, API errors, and desync states are visible and block unsafe trading.

## References

- Binance USD-M Futures New Order: https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Order
- Binance USD-M Futures Account Update event: https://developers.binance.com/docs/derivatives/usds-margined-futures/user-data-streams/Event-Balance-and-Position-Update
