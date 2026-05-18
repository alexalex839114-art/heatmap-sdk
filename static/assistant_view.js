// Minimum absolute |signed_vpin| at which we call a side. Matches the
// backend threshold (TOXIC_DIRECTION_MIN_SIGNED_VPIN) so UI and server
// agree on what counts as "directional" toxic flow.
export const TOXIC_DIRECTION_THRESHOLD = 0.05;

export function deriveToxicDirection(payload) {
  if (!payload) {
    return null;
  }
  if (payload.toxic_direction === "BUY" || payload.toxic_direction === "SELL") {
    return payload.toxic_direction;
  }
  const signed = Number(payload.signed_vpin);
  if (!Number.isFinite(signed)) {
    return null;
  }
  if (signed >= TOXIC_DIRECTION_THRESHOLD) {
    return "BUY";
  }
  if (signed <= -TOXIC_DIRECTION_THRESHOLD) {
    return "SELL";
  }
  return null;
}

function directionArrow(direction) {
  if (direction === "BUY") return "\u2191";
  if (direction === "SELL") return "\u2193";
  return "";
}

export function formatEntryFilter(payload) {
  if (!payload) {
    return "entry: -";
  }
  const isRisk = payload.market_state === "TOXIC" || payload.market_state === "RISKY";
  let suffix = "";
  if (isRisk) {
    const direction = deriveToxicDirection(payload);
    const arrow = directionArrow(direction);
    if (arrow) {
      suffix = ` | flow ${arrow}${direction}`;
    } else if (Number.isFinite(Number(payload.signed_vpin))) {
      suffix = ` | flow ~0`;
    }
  }
  return `${payload.market_state} | L:${payload.long_filter} S:${payload.short_filter} | ${payload.reason}${suffix}`;
}

export function formatPosition(payload) {
  if (!payload) {
    return "position: flat";
  }
  const pnl = Number(payload.unrealized_pnl || 0).toFixed(2);
  const rvLevels = (
    payload.rv_stop_price != null && payload.rv_take_price != null
      ? ` | SL ${payload.rv_stop_price} TP ${payload.rv_take_price}`
      : ""
  );
  return `${payload.symbol} ${payload.side} ${payload.quantity} @ ${payload.entry_price} | PnL ${pnl}${rvLevels}`;
}

export function formatTradingStatus(payload) {
  if (!payload) {
    return "trading: OFF";
  }
  const state = payload.state || "OFF";
  const remainingMs = Number(payload.cooldown_remaining_ms || 0);
  const cooldown = remainingMs > 0 ? ` ${Math.ceil(remainingMs / 1000)}s` : "";
  const message = payload.message ? ` | ${payload.message}` : "";
  return `trading: ${state}${cooldown}${message}`;
}

export function signalVisualState(payload) {
  if (!payload) {
    return { mode: "off", label: "WAIT", reason: "No data", toxicDirection: null };
  }
  if (payload.market_state === "TOXIC" || payload.market_state === "RISKY") {
    const direction = deriveToxicDirection(payload);
    const arrow = directionArrow(direction);
    const label = arrow ? `RISK ${arrow}` : "RISK";
    const reason = direction
      ? `${payload.reason || payload.market_state} (toxic ${direction})`
      : (payload.reason || payload.market_state);
    return {
      mode: "risk",
      label,
      reason,
      toxicDirection: direction,
      signedVpin: Number.isFinite(Number(payload.signed_vpin)) ? Number(payload.signed_vpin) : null,
    };
  }
  if (payload.long_filter === "OK" && payload.short_filter !== "OK") {
    return { mode: "buy", label: "BUY", reason: "Long conditions", toxicDirection: null };
  }
  if (payload.short_filter === "OK" && payload.long_filter !== "OK") {
    return { mode: "sell", label: "SELL", reason: "Short conditions", toxicDirection: null };
  }
  return { mode: "wait", label: "WAIT", reason: payload.reason || "No signal", toxicDirection: null };
}

export function confluenceVisualState(exchangeStates) {
  const binance = signalVisualState(exchangeStates?.binance);
  const bybit = signalVisualState(exchangeStates?.bybit);
  if (binance.mode === "buy" && bybit.mode === "buy") {
    return { mode: "buy", label: "BUY x2", reason: "Binance + Bybit" };
  }
  if (binance.mode === "sell" && bybit.mode === "sell") {
    return { mode: "sell", label: "SELL x2", reason: "Binance + Bybit" };
  }
  if (binance.mode === "risk" || bybit.mode === "risk") {
    return { mode: "risk", label: "RISK", reason: "One exchange risky" };
  }
  const binanceDecisive = binance.mode === "buy" || binance.mode === "sell";
  const bybitDecisive = bybit.mode === "buy" || bybit.mode === "sell";
  if (binanceDecisive && bybitDecisive && binance.mode !== bybit.mode) {
    return { mode: "wait", label: "MIXED", reason: "Signals diverge" };
  }
  if (binanceDecisive) {
    return { mode: binance.mode, label: binance.label, reason: "Binance only" };
  }
  if (bybitDecisive) {
    return { mode: bybit.mode, label: bybit.label, reason: "Bybit only" };
  }
  return { mode: "wait", label: "WAIT", reason: "No signal" };
}

const EXCHANGE_LABELS = {
  binance: "Binance",
  bybit: "Bybit",
  okx: "OKX",
  gate: "Gate",
};

export const CONFLUENCE_MIN_AGREE = 3;

export function multiExchangeVisualState(exchangeStates) {
  const order = ["binance", "bybit", "okx", "gate"];
  const visuals = order.map((name) => ({
    name,
    visual: signalVisualState(exchangeStates?.[name]),
  }));

  const buy = visuals.filter((v) => v.visual.mode === "buy");
  const sell = visuals.filter((v) => v.visual.mode === "sell");
  const risk = visuals.filter((v) => v.visual.mode === "risk");

  // RISK takes priority — any exchange showing TOXIC/RISKY blocks the
  // confluence light from showing BUY/SELL, mirroring the backend's
  // _confluence_entry_side ruleset.
  if (risk.length > 0) {
    const buyDir = risk.filter((v) => v.visual.toxicDirection === "BUY").length;
    const sellDir = risk.filter((v) => v.visual.toxicDirection === "SELL").length;
    let summaryDirection = null;
    if (buyDir > sellDir) summaryDirection = "BUY";
    else if (sellDir > buyDir) summaryDirection = "SELL";
    const arrow = directionArrow(summaryDirection);
    const exchangeList = risk
      .map((v) => {
        const exArrow = directionArrow(v.visual.toxicDirection);
        return exArrow ? `${EXCHANGE_LABELS[v.name]}${exArrow}` : EXCHANGE_LABELS[v.name];
      })
      .join(" / ");
    return {
      mode: "risk",
      label: arrow ? `RISK ${arrow}` : "RISK",
      reason: exchangeList,
      toxicDirection: summaryDirection,
    };
  }
  if (buy.length && sell.length) {
    return { mode: "wait", label: "MIXED", reason: "Signals diverge" };
  }
  if (buy.length >= CONFLUENCE_MIN_AGREE) {
    return {
      mode: "buy",
      label: `BUY x${buy.length}`,
      reason: buy.map((v) => EXCHANGE_LABELS[v.name]).join(" + "),
    };
  }
  if (sell.length >= CONFLUENCE_MIN_AGREE) {
    return {
      mode: "sell",
      label: `SELL x${sell.length}`,
      reason: sell.map((v) => EXCHANGE_LABELS[v.name]).join(" + "),
    };
  }
  // Partial alignment (1-2 exchanges agree). Light stays grey/WAIT so the
  // trader does not act on a sub-confluence signal, but the reason hints
  // which way the partial pressure is.
  if (buy.length > 0) {
    return {
      mode: "wait",
      label: `WAIT (${buy.length}/${CONFLUENCE_MIN_AGREE})`,
      reason: `${buy.map((v) => EXCHANGE_LABELS[v.name]).join(" + ")} BUY`,
    };
  }
  if (sell.length > 0) {
    return {
      mode: "wait",
      label: `WAIT (${sell.length}/${CONFLUENCE_MIN_AGREE})`,
      reason: `${sell.map((v) => EXCHANGE_LABELS[v.name]).join(" + ")} SELL`,
    };
  }
  return { mode: "wait", label: "WAIT", reason: "No signal" };
}
