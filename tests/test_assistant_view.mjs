import test from "node:test";
import assert from "node:assert/strict";

import {
  TOXIC_DIRECTION_THRESHOLD,
  confluenceVisualState,
  deriveToxicDirection,
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
    { mode: "buy", label: "BUY", reason: "Long conditions", toxicDirection: null },
  );
});

test("signalVisualState maps short permission to sell", () => {
  assert.deepEqual(
    signalVisualState({ market_state: "READY", long_filter: "WAIT", short_filter: "OK" }),
    { mode: "sell", label: "SELL", reason: "Short conditions", toxicDirection: null },
  );
});

test("signalVisualState maps toxic to risk (no direction when signed_vpin missing)", () => {
  assert.deepEqual(
    signalVisualState({ market_state: "TOXIC", reason: "toxic_vpin_watch_only" }),
    {
      mode: "risk",
      label: "RISK",
      reason: "toxic_vpin_watch_only",
      toxicDirection: null,
      signedVpin: null,
    },
  );
});

test("signalVisualState shows BUY arrow when toxic flow is buy-side", () => {
  assert.deepEqual(
    signalVisualState({
      market_state: "TOXIC",
      reason: "toxic_vpin",
      signed_vpin: 0.42,
    }),
    {
      mode: "risk",
      label: "RISK \u2191",
      reason: "toxic_vpin (toxic BUY)",
      toxicDirection: "BUY",
      signedVpin: 0.42,
    },
  );
});

test("signalVisualState shows SELL arrow when toxic flow is sell-side", () => {
  assert.deepEqual(
    signalVisualState({
      market_state: "RISKY",
      reason: "adaptive_elevated_vpin",
      signed_vpin: -0.18,
    }),
    {
      mode: "risk",
      label: "RISK \u2193",
      reason: "adaptive_elevated_vpin (toxic SELL)",
      toxicDirection: "SELL",
      signedVpin: -0.18,
    },
  );
});

test("signalVisualState prefers backend toxic_direction over signed_vpin sign", () => {
  // Backend already classified; UI must trust the server's call rather than
  // re-deriving from a possibly stale signed_vpin sample.
  const visual = signalVisualState({
    market_state: "TOXIC",
    reason: "toxic_vpin",
    signed_vpin: -0.5,
    toxic_direction: "BUY",
  });
  assert.equal(visual.toxicDirection, "BUY");
  assert.equal(visual.label, "RISK \u2191");
});

test("signalVisualState shows no arrow inside the dead-zone", () => {
  const visual = signalVisualState({
    market_state: "TOXIC",
    reason: "toxic_vpin",
    signed_vpin: TOXIC_DIRECTION_THRESHOLD / 2,
  });
  assert.equal(visual.toxicDirection, null);
  assert.equal(visual.label, "RISK");
});

test("signalVisualState defaults to wait", () => {
  assert.deepEqual(
    signalVisualState({ market_state: "READY", reason: "no_signal" }),
    { mode: "wait", label: "WAIT", reason: "no_signal", toxicDirection: null },
  );
});

test("deriveToxicDirection respects the threshold both sides", () => {
  assert.equal(deriveToxicDirection({ signed_vpin: TOXIC_DIRECTION_THRESHOLD }), "BUY");
  assert.equal(deriveToxicDirection({ signed_vpin: -TOXIC_DIRECTION_THRESHOLD }), "SELL");
  assert.equal(deriveToxicDirection({ signed_vpin: 0 }), null);
  assert.equal(deriveToxicDirection(null), null);
  assert.equal(deriveToxicDirection({}), null);
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
    okx: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    gate: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
  };
  assert.deepEqual(multiExchangeVisualState(states), {
    mode: "buy",
    label: "BUY x4",
    reason: "Binance + Bybit + OKX + Gate",
  });
});

test("multiExchangeVisualState reports x3 when three of four exchanges agree", () => {
  const states = {
    binance: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    bybit: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    okx: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    gate: null,
  };
  assert.deepEqual(multiExchangeVisualState(states), {
    mode: "buy",
    label: "BUY x3",
    reason: "Binance + Bybit + OKX",
  });
});

test("multiExchangeVisualState stays WAIT when only two exchanges agree (sub-confluence)", () => {
  const states = {
    binance: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    bybit: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    okx: { market_state: "READY", long_filter: "WAIT", short_filter: "WAIT", reason: "no_signal" },
    gate: null,
  };
  assert.deepEqual(multiExchangeVisualState(states), {
    mode: "wait",
    label: "WAIT (2/3)",
    reason: "Binance + Bybit BUY",
  });
});

test("multiExchangeVisualState stays WAIT when only one exchange signals", () => {
  const states = {
    binance: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    bybit: { market_state: "READY", long_filter: "WAIT", short_filter: "WAIT", reason: "no_signal" },
    okx: null,
    gate: null,
  };
  assert.deepEqual(multiExchangeVisualState(states), {
    mode: "wait",
    label: "WAIT (1/3)",
    reason: "Binance BUY",
  });
});

test("multiExchangeVisualState reports MIXED when buy and sell coexist", () => {
  const states = {
    binance: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    bybit: { market_state: "READY", long_filter: "WAIT", short_filter: "OK" },
    okx: null,
    gate: null,
  };
  assert.deepEqual(multiExchangeVisualState(states), {
    mode: "wait",
    label: "MIXED",
    reason: "Signals diverge",
  });
});

test("multiExchangeVisualState shows BUY x3 even with one TOXIC venue (permissive confluence)", () => {
  // Under the user-specified permissive policy, TOXIC on a minority of
  // venues does not veto a 3-of-4 same-side consensus. The light shows
  // BUY x3 to match the backend's actual entry decision, but the reason
  // still surfaces which venue is risky so the trader has visibility.
  const states = {
    binance: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    bybit: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    okx: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    gate: { market_state: "TOXIC", reason: "toxic_vpin_watch_only" },
  };
  assert.deepEqual(multiExchangeVisualState(states), {
    mode: "buy",
    label: "BUY x3",
    reason: "Binance + Bybit + OKX \u2022 risk: Gate",
  });
});

test("multiExchangeVisualState reports RISK when there is no 3-of-same and some venue is toxic", () => {
  // Only 2 BUY (not 3-of-same) + 1 TOXIC + 1 WAIT -> not tradeable, but
  // still RISK-flagged so the trader sees the toxic flow.
  const states = {
    binance: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    bybit: { market_state: "READY", long_filter: "OK", short_filter: "WAIT" },
    okx: { market_state: "READY", long_filter: "WAIT", short_filter: "WAIT", reason: "no_signal" },
    gate: { market_state: "TOXIC", reason: "toxic_vpin_watch_only", signed_vpin: 0.5 },
  };
  const visual = multiExchangeVisualState(states);
  assert.equal(visual.mode, "risk");
  assert.equal(visual.label, "RISK \u2191");
  assert.equal(visual.toxicDirection, "BUY");
  assert.equal(visual.reason, "Gate\u2191");
});

test("signalVisualState returns off mode for missing payload", () => {
  assert.deepEqual(signalVisualState(null), {
    mode: "off",
    label: "WAIT",
    reason: "No data",
    toxicDirection: null,
  });
});

test("formatEntryFilter appends toxic-flow direction in RISK states", () => {
  assert.equal(
    formatEntryFilter({
      market_state: "TOXIC",
      long_filter: "BLOCKED",
      short_filter: "BLOCKED",
      reason: "toxic_vpin",
      signed_vpin: 0.6,
    }),
    "TOXIC | L:BLOCKED S:BLOCKED | toxic_vpin | flow \u2191BUY",
  );
  assert.equal(
    formatEntryFilter({
      market_state: "RISKY",
      long_filter: "WAIT",
      short_filter: "WAIT",
      reason: "adaptive_elevated_vpin",
      signed_vpin: -0.3,
    }),
    "RISKY | L:WAIT S:WAIT | adaptive_elevated_vpin | flow \u2193SELL",
  );
});

test("formatEntryFilter does not add direction outside RISK states", () => {
  assert.equal(
    formatEntryFilter({
      market_state: "READY",
      long_filter: "OK",
      short_filter: "WAIT",
      reason: "buy_exhaustion",
      signed_vpin: 0.9,
    }),
    "READY | L:OK S:WAIT | buy_exhaustion",
  );
});
