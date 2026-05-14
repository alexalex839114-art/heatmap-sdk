# Trading Start Stop Cooldown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit trading START/STOP lifecycle, emergency flatten command, and 30 second post-close cooldown.

**Architecture:** Keep heatmap streaming separate from trading authorization. `LiveHeatmapService` owns runtime lifecycle and uses the existing account/order abstractions for exchange actions.

**Tech Stack:** Python FastAPI service, httpx signed Binance REST client, browser WebSocket UI, Node test runner for pure JS helpers.

---

### Task 1: Exchange Cancel-All

**Files:**
- Modify: `app/binance_account.py`
- Modify: `app/order_executor.py`
- Test: `tests/test_binance_account.py`
- Test: `tests/test_order_executor.py`

- [ ] Write tests for `DELETE /fapi/v1/allOpenOrders` with signed `symbol`.
- [ ] Add `BinanceAccountClient.cancel_all_open_orders(symbol)`.
- [ ] Add `OrderExecutor.cancel_all_open_orders(symbol)`.
- [ ] Run targeted tests.

### Task 2: Trading Lifecycle

**Files:**
- Modify: `app/ws_session.py`
- Test: `tests/test_ws_session.py`

- [ ] Write failing tests for START warmup, STOP soft-disable, entry gate, cooldown, flat-close cancel-all, and emergency flatten.
- [ ] Add lifecycle fields and `trading_status_event()`.
- [ ] Add commands `start_trading`, `stop_trading`, `emergency_flatten`.
- [ ] Gate `_evaluate_auto_trade()` on lifecycle and cooldown.
- [ ] On close confirmation, cancel all open orders and start cooldown.
- [ ] Run targeted tests.

### Task 3: UI Controls

**Files:**
- Modify: `static/index.html`
- Modify: `static/app.js`
- Modify: `static/style.css`
- Test: `tests/test_assistant_view.mjs`

- [ ] Add pure status formatter tests.
- [ ] Add START/STOP Trading and Emergency Flatten controls.
- [ ] Render backend trading status and cooldown seconds.
- [ ] Run Node tests.

### Task 4: Full Verification

**Files:**
- Test only.

- [ ] Run `.\.venv\Scripts\python.exe -m pytest tests tests_adaptive_sdk`.
- [ ] Run `node --test tests\test_assistant_view.mjs tests\test_renderer_palette.mjs tests\test_signal_css.mjs`.
