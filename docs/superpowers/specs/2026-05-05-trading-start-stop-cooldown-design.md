# Trading Start Stop Cooldown Design

## Goal

Add an explicit trading lifecycle separate from heatmap streaming: the user arms trading with START, disarms it with STOP, and has a separate emergency flatten path.

## Design

Trading has these states: `OFF`, `WARMING`, `ARMED`, `IN_POSITION`, `COOLDOWN`, and `ERROR`. Heatmap connection/streaming remains the market data transport. START requires an active symbol, active market clients, account credentials, an order executor, a fresh position poll, and warmed Binance/Bybit entry filters before auto-entry is allowed. If a position is already open, START enters `IN_POSITION` so exit management can continue without adding exposure.

STOP is soft: it disables new entries immediately and leaves an already-open position under the existing exit engine. The UI also gets an emergency flatten button that cancels all open symbol orders and submits a reduce-only market close if a position exists.

After any close confirmation where the account is flat, the service cancels all open symbol orders, clears pending close state, and starts a minimum 30 second cooldown. Auto-entry is blocked while cooldown is active. Cooldown state and remaining seconds are broadcast to the UI.

## Components

- `app/binance_account.py`: signed `DELETE /fapi/v1/allOpenOrders` helper for USD-M Futures cancel-all.
- `app/order_executor.py`: `cancel_all_open_orders(symbol)` delegates to the account client.
- `app/ws_session.py`: owns trading lifecycle state, START/STOP/emergency commands, readiness checks, cooldown, and broadcasts.
- `static/index.html`, `static/app.js`, `static/style.css`: add START/STOP Trading, emergency flatten, and a clear status line.

## Testing

Backend tests cover command transitions, START blocking until warm, auto-entry gating, cooldown after flat close confirmation, cancel-all request wiring, and emergency flatten behavior. JS tests cover pure trading status formatting. Full Python and Node test suites should pass.
