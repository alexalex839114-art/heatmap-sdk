# Binance Live Heatmap SDK Assistant

Live-only Binance USDT Futures heatmap prototype with an embedded
`adaptive_sdk` manual scalping assistant.

The assistant is not an entry bot. It filters manual entries, tracks one
one-way position, and can close that position with a market order when exit
rules trigger.

## Run

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## One-Command Start

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

The script creates `.venv` if needed, installs dependencies on first run, and starts the server.

You can also use the batch launcher:

```bat
start.bat
```

## Usage

1. Enter a Binance USDT Futures symbol such as `BTCUSDT`.
2. Set `Compression`.
3. Click `Connect WS`.
4. Wait until the status becomes `live_ready`.
5. Click `Start Heatmap`.
6. Watch the live heatmap scroll from right to left.

## Assistant Scope

- One symbol at a time.
- Binance USD-M Futures only.
- One-way mode only.
- Manual entries only.
- No averaging, no position increase, no reversal.
- The only trading action is a market close for the current position.
- `adaptive_sdk` signals are shown before entry as a filter: `LONG_OK`,
  `SHORT_OK`, `WAIT`, `BLOCKED`, `TOXIC`, or `WARMING`.
- While in position, hard exits include max loss, toxic VPIN, and high
  confidence opposite signal.
- Soft exits currently include max holding time confirmation.

## API Keys

Market data is public. Position tracking and market close require real Binance
API keys with USD-M Futures trade permission.

Create a local `.env` from `.env.example`:

```env
BINANCE_API_KEY=
BINANCE_API_SECRET=
BINANCE_FAPI_BASE_URL=https://fapi.binance.com
AUTO_EXIT_ENABLED=false
```

Keep withdrawals disabled on the key. Keys are read by the backend only; the UI
does not ask for them or display them.

`AUTO_EXIT_ENABLED=false` is the safe default. You can enable auto-exit for the
running session from the UI.

## Assistant Controls

- `Auto-exit`: allows or blocks actual market close orders.
- `Max loss USDT`: hard exit threshold per trade.
- `Max holding sec`: soft exit after confirmation.
- `Confirmation ms`: delay for soft exits.
- `Opposite signal`: enables high-confidence opposite SDK signal exit.
- `Toxic VPIN`: enables VPIN toxicity exit.

## Minimum-Size Live Checklist

Use minimum order size when validating on real Binance:

1. Start the app and connect one symbol.
2. Confirm the entry filter updates while flat.
3. Confirm account/position status is visible after a manual external entry.
4. Enable `Auto-exit` only when you are ready for the assistant to close.
5. Set a very small `Max loss USDT` or short `Max holding sec` for the test.
6. Confirm the assistant sends at most one market close.
7. Confirm the position reaches zero in the external terminal.
8. Disable `Auto-exit` after the test.

## Compression

`Compression` is display aggregation in exchange ticks.

- `1` means one display step equals one exchange tick
- `2` means one display step equals two exchange ticks
- `3` means one display step equals three exchange ticks

The UI also shows the current `display step` after connect. Internally the backend reads the symbol `tick_size` from Binance metadata and builds the heatmap on a buffered discrete price grid.

## Current Scope

- Binance only
- USDT perpetual futures only
- One symbol at a time
- Live-only, no history
- Browser UI with WebSocket control channel
- Canvas heatmap with trade overlay
- Raw Binance `@trade` stream for current trade markers and SDK flow input
- Tick-based aggregation via `Compression`
- Buffered display grid for more stable heatmap edges
- Embedded `adaptive_sdk` entry filtering
- REST-polled position tracking for v1
- Market close executor for current one-way position
