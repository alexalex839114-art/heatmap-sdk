import test from "node:test";
import assert from "node:assert/strict";

import {
  confluenceVisualState,
  formatEntryFilter,
  formatPosition,
  formatTradingStatus,
  signalVisualState,
} from "../static/assistant_view.js";

test("formatEntryFilter shows market and directional filters", () => {
  const text = formatEntryFilter({
    market_state: "READY",
    long_filter: "OK",
    short_filter: "WAIT",
    reason: "buy_exhaustion",
  });

  assert.equal(text, "READY | L:OK S:WAIT | buy_exhaustion");
});

test("formatEntryFilter shows detailed warming progress", () => {
  const text = formatEntryFilter({
    market_state: "WARMING",
    long_filter: "WAIT",
    short_filter: "WAIT",
    reason: "warming 12/50 buckets, 80/200 trades",
  });

  assert.equal(text, "WARMING | L:WAIT S:WAIT | warming 12/50 buckets, 80/200 trades");
});

test("formatEntryFilter handles missing payload", () => {
  assert.equal(formatEntryFilter(null), "entry: -");
});

test("formatPosition shows side quantity pnl and entry", () => {
  const text = formatPosition({
    symbol: "BTCUSDT",
    side: "LONG",
    quantity: 0.012,
    entry_price: 65000,
    unrealized_pnl: 3.5,
  });

  assert.equal(text, "BTCUSDT LONG 0.012 @ 65000 | PnL 3.50");
});

test("formatPosition shows RV stop and take levels when present", () => {
  const text = formatPosition({
    symbol: "BTCUSDT",
    side: "LONG",
    quantity: 0.012,
    entry_price: 65000,
    unrealized_pnl: 3.5,
    rv_stop_price: 64800,
    rv_take_price: 65300,
  });

  assert.equal(text, "BTCUSDT LONG 0.012 @ 65000 | PnL 3.50 | SL 64800 TP 65300");
});

test("formatPosition handles no position", () => {
  assert.equal(formatPosition(null), "position: flat");
});

test("formatTradingStatus shows cooldown seconds", () => {
  assert.equal(
    formatTradingStatus({
      state: "COOLDOWN",
      cooldown_remaining_ms: 29_400,
      message: "post-close pause",
    }),
    "trading: COOLDOWN 30s | post-close pause",
  );
});

test("formatTradingStatus includes readiness detail", () => {
  assert.equal(
    formatTradingStatus({
      state: "ARMED",
      message: "binance READY no_signal | bybit RISKY adaptive_elevated_vpin",
    }),
    "trading: ARMED | binance READY no_signal | bybit RISKY adaptive_elevated_vpin",
  );
});

test("formatTradingStatus handles missing payload", () => {
  assert.equal(formatTradingStatus(null), "trading: OFF");
});

test("signalVisualState maps long permission to buy", () => {
  assert.deepEqual(
    signalVisualState({ market_state: "READY", long_filter: "OK", short_filter: "WAIT" }),
    { mode: "buy", label: "BUY", reason: "Long conditions" },
  );
});

test("signalVisualState maps short permission to sell", () => {
  assert.deepEqual(
    signalVisualState({ market_state: "READY", long_filter: "WAIT", short_filter: "OK" }),
    { mode: "sell", label: "SELL", reason: "Short conditions" },
  );
});

test("signalVisualState maps toxic to risk", () => {
  assert.deepEqual(
    signalVisualState({ market_state: "TOXIC", reason: "toxic_vpin_watch_only" }),
    { mode: "risk", label: "RISK", reason: "toxic_vpin_watch_only" },
  );
});

test("signalVisualState defaults to wait", () => {
  assert.deepEqual(
    signalVisualState({ market_state: "READY", reason: "no_signal" }),
    { mode: "wait", label: "WAIT", reason: "no_signal" },
  );
});

test("confluenceVisualState highlights matching buy signals", () => {
  assert.deepEqual(
    confluenceVisualState({
      binance: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
      bybit: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    }),
    { mode: "buy", label: "BUY x2", reason: "Binance + Bybit" },
  );
});

test("confluenceVisualState waits when exchanges disagree", () => {
  assert.deepEqual(
    confluenceVisualState({
      binance: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
      bybit: { market_state: "READY", long_filter: "WAIT", short_filter: "OK" },
    }),
    { mode: "wait", label: "MIXED", reason: "Signals diverge" },
  );
});


import { multiExchangeVisualState } from "../static/assistant_view.js";

test("multiExchangeVisualState reports x4 when all four exchanges agree", () => {
  const states = {
    binance: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    bybit: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    coinbase: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    kraken: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
  };
  assert.deepEqual(multiExchangeVisualState(states), {
    mode: "buy",
    label: "BUY x4",
    reason: "Binance + Bybit + Coinbase + Kraken",
  });
});

test("multiExchangeVisualState falls back to x2 when only two exchanges agree", () => {
  const states = {
    binance: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    bybit: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    coinbase: null,
    kraken: { market_state: "READY", long_filter: "WAIT", short_filter: "WAIT", reason: "no_signal" },
  };
  assert.deepEqual(multiExchangeVisualState(states), {
    mode: "buy",
    label: "BUY x2",
    reason: "Binance + Bybit",
  });
});

test("multiExchangeVisualState reports MIXED when buy and sell coexist", () => {
  const states = {
    binance: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    bybit: { market_state: "READY", long_filter: "WAIT", short_filter: "OK" },
    coinbase: null,
    kraken: null,
  };
  assert.deepEqual(multiExchangeVisualState(states), {
    mode: "wait",
    label: "MIXED",
    reason: "Signals diverge",
  });
});

test("signalVisualState returns off mode for missing payload", () => {
  assert.deepEqual(signalVisualState(null), {
    mode: "off",
    label: "WAIT",
    reason: "No data",
  });
});
