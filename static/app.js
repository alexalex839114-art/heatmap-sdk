import { createRenderer } from "/static/renderer.js";
import {
  formatEntryFilter,
  formatPosition,
  formatTradingStatus,
  multiExchangeVisualState,
  signalVisualState,
} from "/static/assistant_view.js";

const symbolInput = document.getElementById("symbol");
const compressionInput = document.getElementById("compression");
const connectButton = document.getElementById("connect-btn");
const stopWsButton = document.getElementById("stop-ws-btn");
const toggleButton = document.getElementById("toggle-btn");
const statusNode = document.getElementById("status");
const displayStepNode = document.getElementById("display-step");
const tradingToggleButton = document.getElementById("trading-toggle-btn");
const emergencyFlattenButton = document.getElementById("emergency-flatten-btn");
const tradingStatusNode = document.getElementById("trading-status");
const tradeNotionalInput = document.getElementById("trade-notional");
const autoExitInput = document.getElementById("auto-exit");
const maxLossInput = document.getElementById("max-loss");
const maxHoldingInput = document.getElementById("max-holding");
const confirmationInput = document.getElementById("confirmation-ms");
const stopRvMultiplierInput = document.getElementById("stop-rv-multiplier");
const takeRvMultiplierInput = document.getElementById("take-rv-multiplier");
const oppositeExitInput = document.getElementById("opposite-exit");
const toxicExitInput = document.getElementById("toxic-exit");
const assistantStatusNode = document.getElementById("assistant-status");
const entryFilterNode = document.getElementById("entry-filter");
const positionStateNode = document.getElementById("position-state");
const exitStateNode = document.getElementById("exit-state");
const signalCardNode = document.getElementById("signal-card");
const signalLabelNode = document.getElementById("signal-label");
const signalReasonNode = document.getElementById("signal-reason");
const signalDetailNode = document.getElementById("signal-detail");
const buyLightNode = document.getElementById("buy-light");
const waitLightNode = document.getElementById("wait-light");
const sellLightNode = document.getElementById("sell-light");
const minExcursionBpsInput = document.getElementById("min-excursion-bps");
const rvMultiplierInput = document.getElementById("rv-multiplier");
const requireExtremaInput = document.getElementById("require-extrema");
const heatmapCanvas = document.getElementById("heatmap");
const overlayCanvas = document.getElementById("overlay");

const renderer = createRenderer(heatmapCanvas, overlayCanvas);

let socket;
let streaming = false;
let tradingActive = false;
const exchangeSignals = {
  binance: null,
  bybit: null,
  okx: null,
  gate: null,
};
const exchangeStatus = {
  binance: null,
  bybit: null,
  okx: null,
  gate: null,
};

function setStatus(text) {
  statusNode.textContent = text;
}

function setDisplayStep(displayStep, tickSize) {
  if (!displayStep) {
    displayStepNode.textContent = "display step: -";
    return;
  }
  const tickText = tickSize ? ` (tick ${tickSize})` : "";
  displayStepNode.textContent = `display step: ${displayStep}${tickText}`;
}

function formatDetailLine(exchange, label) {
  const signal = exchangeSignals[exchange];
  const status = exchangeStatus[exchange];
  if (signal) {
    return `${label}: ${formatEntryFilter(signal)}`;
  }
  if (status && status.state && status.state !== "ready") {
    const product = status.product ? ` ${status.product}` : "";
    const message = status.message ? ` (${status.message})` : "";
    return `${label}: ${status.state}${product}${message}`;
  }
  if (status && status.state === "ready" && status.product) {
    return `${label}: warming ${status.product}`;
  }
  return `${label}: -`;
}

function setSignalVisual(payload) {
  const exchange = payload?.exchange || "binance";
  const filterPayload = payload && payload.market_state !== undefined ? payload : null;
  exchangeSignals[exchange] = filterPayload;
  const visual = multiExchangeVisualState(exchangeSignals);
  signalCardNode.className = `signal-card signal-${visual.mode}`;
  signalLabelNode.textContent = visual.label;
  signalReasonNode.textContent = visual.reason;
  signalDetailNode.textContent = [
    formatDetailLine("binance", "BIN"),
    formatDetailLine("bybit", "BYB"),
    formatDetailLine("okx", "OKX"),
    formatDetailLine("gate", "GATE"),
  ].join("\n");

  for (const name of ["binance", "bybit", "okx", "gate"]) {
    updateExchangeHalf(name, signalVisualState(exchangeSignals[name]));
  }
}

function updateExchangeHalf(exchange, visual) {
  const arrow = visual.mode === "risk" && visual.toxicDirection === "BUY"
    ? "\u2191"
    : visual.mode === "risk" && visual.toxicDirection === "SELL"
      ? "\u2193"
      : "";
  for (const row of ["buy", "wait", "sell"]) {
    const rowNode = document.querySelector(`[data-signal-row="${row}"]`);
    const halfNode = rowNode?.querySelector(`[data-exchange-half="${exchange}"]`);
    if (!halfNode) {
      continue;
    }
    const isActive = (
      (row === "buy" && visual.mode === "buy") ||
      (row === "sell" && visual.mode === "sell") ||
      (row === "wait" && (visual.mode === "wait" || visual.mode === "risk"))
    );
    halfNode.classList.toggle("active", isActive);
    halfNode.classList.toggle("risk", visual.mode === "risk" && row === "wait");
    halfNode.classList.toggle("off", visual.mode === "off");
    // Show the toxic-flow direction arrow only on the lit RISK cell (in the
    // WAIT row, which is where TOXIC/RISKY renders). Other rows clear it so
    // the arrow doesn't bleed across rows when state flips.
    if (row === "wait" && arrow) {
      halfNode.dataset.arrow = arrow;
    } else {
      delete halfNode.dataset.arrow;
    }
  }
  buyLightNode.classList.toggle("active", Boolean(buyLightNode.querySelector(".active")));
  waitLightNode.classList.toggle("active", Boolean(waitLightNode.querySelector(".active")));
  sellLightNode.classList.toggle("active", Boolean(sellLightNode.querySelector(".active")));
}

function ensureSocket() {
  if (socket && socket.readyState <= WebSocket.OPEN) {
    return socket;
  }

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${window.location.host}/ws`);
  socket.addEventListener("message", (event) => {
    const payload = JSON.parse(event.data);
    if (payload.type === "status") {
      setStatus(payload.state);
      toggleButton.disabled = !["live_ready", "streaming", "stopped"].includes(payload.state);
      tradingToggleButton.disabled = !["live_ready", "streaming", "stopped"].includes(payload.state);
      emergencyFlattenButton.disabled = !["live_ready", "streaming", "stopped"].includes(payload.state);
      // Stop WS button is enabled any time the user has initiated a connect;
      // includes 'connecting' so a still-handshaking session can be aborted.
      stopWsButton.disabled = !["connecting", "live_ready", "streaming", "stopped"].includes(payload.state);
      if (payload.state === "streaming") {
        streaming = true;
        toggleButton.textContent = "Stop Heatmap";
      } else {
        streaming = false;
        toggleButton.textContent = "Start Heatmap";
      }
    }
    if (payload.type === "frame") {
      renderer.appendColumn(payload.column);
    }
    if (payload.type === "trades") {
      renderer.drawTrades(payload.items || []);
    }
    if (payload.type === "reset") {
      setDisplayStep(payload.display_step, payload.tick_size);
      renderer.reset();
      for (const name of Object.keys(exchangeSignals)) {
        exchangeStatus[name] = null;
        setSignalVisual({ exchange: name });
      }
    }
    if (payload.type === "error") {
      setStatus(`error: ${payload.message}`);
    }
    if (payload.type === "assistant_status") {
      tradeNotionalInput.value = payload.trade_notional_usdt ?? tradeNotionalInput.value;
      autoExitInput.checked = Boolean(payload.auto_exit_enabled);
      maxLossInput.value = payload.max_loss_usdt ?? maxLossInput.value;
      maxHoldingInput.value = payload.max_holding_time_sec ?? maxHoldingInput.value;
      confirmationInput.value = payload.confirmation_ms ?? confirmationInput.value;
      stopRvMultiplierInput.value = payload.stop_rv_multiplier ?? stopRvMultiplierInput.value;
      takeRvMultiplierInput.value = payload.take_rv_multiplier ?? takeRvMultiplierInput.value;
      oppositeExitInput.checked = Boolean(payload.opposite_signal_exit_enabled);
      toxicExitInput.checked = Boolean(payload.toxic_vpin_exit_enabled);
      minExcursionBpsInput.value = payload.min_price_excursion_bps ?? minExcursionBpsInput.value;
      rvMultiplierInput.value = (
        payload.min_price_excursion_vol_multiplier ?? rvMultiplierInput.value
      );
      if (typeof payload.require_price_extrema_progress === "boolean") {
        requireExtremaInput.checked = payload.require_price_extrema_progress;
      }
      assistantStatusNode.textContent = [
        `exit: ${payload.auto_exit_enabled ? "AUTO" : "WATCH"}`,
      ].join(" | ");
    }
    if (payload.type === "trading_status") {
      tradingStatusNode.textContent = formatTradingStatus(payload);
      tradingActive = ["WARMING", "ARMED", "IN_POSITION", "COOLDOWN"].includes(payload.state);
      tradingToggleButton.textContent = tradingActive ? "STOP Trading" : "START Trading";
      tradingToggleButton.disabled = !["live_ready", "streaming", "stopped"].includes(statusNode.textContent);
    }
    if (payload.type === "entry_filter") {
      entryFilterNode.textContent = formatEntryFilter(payload);
      setSignalVisual(payload);
    }
    if (payload.type === "position") {
      positionStateNode.textContent = formatPosition(payload);
    }
    if (payload.type === "exit_status") {
      exitStateNode.textContent = `exit: ${payload.state || "-"} ${payload.reason || ""}`;
    }
    if (payload.type === "order_status") {
      exitStateNode.textContent = (
        `order: ${payload.action || "close"} ${payload.side || ""} ${payload.status || "-"}`
      );
    }
    if (payload.type === "account_error") {
      exitStateNode.textContent = `account: ${payload.message || "error"}`;
    }
    if (payload.type === "indicator_status") {
      const exchange = payload.exchange;
      if (exchange && exchange in exchangeSignals) {
        exchangeStatus[exchange] = {
          state: payload.state,
          product: payload.product,
          message: payload.message,
        };
        if (payload.state !== "ready") {
          setSignalVisual({ exchange });
        } else {
          setSignalVisual(exchangeSignals[exchange] ? { ...exchangeSignals[exchange], exchange } : { exchange });
        }
      }
    }
  });
  socket.addEventListener("close", () => {
    setStatus("disconnected");
    setDisplayStep(null, null);
    toggleButton.disabled = true;
    tradingToggleButton.disabled = true;
    emergencyFlattenButton.disabled = true;
    stopWsButton.disabled = true;
    streaming = false;
    tradingActive = false;
    toggleButton.textContent = "Start Heatmap";
    tradingToggleButton.textContent = "START Trading";
    tradingStatusNode.textContent = "trading: OFF";
  });
  return socket;
}

function currentAssistantSettings() {
  return {
    trade_notional_usdt: Number.parseFloat(tradeNotionalInput.value) || 0,
    max_loss_usdt: Number.parseFloat(maxLossInput.value) || 0,
    max_holding_time_sec: Number.parseFloat(maxHoldingInput.value) || 0,
    confirmation_ms: Number.parseInt(confirmationInput.value, 10) || 0,
    stop_rv_multiplier: Number.parseFloat(stopRvMultiplierInput.value) || 0,
    take_rv_multiplier: Number.parseFloat(takeRvMultiplierInput.value) || 0,
    opposite_signal_exit_enabled: oppositeExitInput.checked,
    toxic_vpin_exit_enabled: toxicExitInput.checked,
    min_price_excursion_bps: Number.parseFloat(minExcursionBpsInput.value) || 0,
    min_price_excursion_vol_multiplier: Number.parseFloat(rvMultiplierInput.value) || 0,
    require_price_extrema_progress: requireExtremaInput.checked,
  };
}

function sendAssistantSettings() {
  const ws = ensureSocket();
  const send = () => {
    ws.send(
      JSON.stringify({
        type: "set_assistant_settings",
        settings: currentAssistantSettings(),
      }),
    );
  };
  if (ws.readyState === WebSocket.OPEN) {
    send();
  } else {
    ws.addEventListener("open", send, { once: true });
  }
}

function sendCommand(type) {
  const ws = ensureSocket();
  const send = () => {
    ws.send(JSON.stringify({ type }));
  };
  if (ws.readyState === WebSocket.OPEN) {
    send();
  } else {
    ws.addEventListener("open", send, { once: true });
  }
}

connectButton.addEventListener("click", () => {
  const ws = ensureSocket();
  const sendConnect = () => {
    setStatus("connecting");
    ws.send(
      JSON.stringify({
        type: "connect",
        symbol: symbolInput.value.trim().toUpperCase(),
        compression: Math.max(1, Number.parseInt(compressionInput.value, 10) || 1),
      }),
    );
  };
  if (ws.readyState === WebSocket.OPEN) {
    sendConnect();
  } else {
    ws.addEventListener("open", sendConnect, { once: true });
  }
});

stopWsButton.addEventListener("click", () => {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  socket.send(JSON.stringify({ type: "disconnect" }));
  // Optimistic local reset so lights / heatmap go cold immediately. The
  // authoritative 'status' broadcast that follows keeps us in sync.
  setStatus("disconnecting");
  stopWsButton.disabled = true;
  toggleButton.disabled = true;
  tradingToggleButton.disabled = true;
  emergencyFlattenButton.disabled = true;
  streaming = false;
  tradingActive = false;
  toggleButton.textContent = "Start Heatmap";
  tradingToggleButton.textContent = "START Trading";
  setDisplayStep(null, null);
  renderer.reset();
  for (const name of Object.keys(exchangeSignals)) {
    exchangeSignals[name] = null;
    exchangeStatus[name] = null;
    setSignalVisual({ exchange: name });
  }
  entryFilterNode.textContent = "entry: -";
  positionStateNode.textContent = "position: flat";
  exitStateNode.textContent = "exit: -";
});

toggleButton.addEventListener("click", () => {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  if (streaming) {
    socket.send(JSON.stringify({ type: "stop_heatmap" }));
    streaming = false;
    toggleButton.textContent = "Start Heatmap";
    return;
  }
  socket.send(JSON.stringify({ type: "start_heatmap" }));
  streaming = true;
  toggleButton.textContent = "Stop Heatmap";
  setStatus("streaming");
});

tradingToggleButton.addEventListener("click", () => {
  sendAssistantSettings();
  sendCommand(tradingActive ? "stop_trading" : "start_trading");
});

emergencyFlattenButton.addEventListener("click", () => {
  sendCommand("emergency_flatten");
});

autoExitInput.addEventListener("change", () => {
  const ws = ensureSocket();
  const send = () => {
    ws.send(JSON.stringify({ type: autoExitInput.checked ? "enable_auto_exit" : "disable_auto_exit" }));
    sendAssistantSettings();
  };
  if (ws.readyState === WebSocket.OPEN) {
    send();
  } else {
    ws.addEventListener("open", send, { once: true });
  }
});

for (const input of [
  tradeNotionalInput,
  maxLossInput,
  maxHoldingInput,
  confirmationInput,
  stopRvMultiplierInput,
  takeRvMultiplierInput,
  oppositeExitInput,
  toxicExitInput,
  minExcursionBpsInput,
  rvMultiplierInput,
  requireExtremaInput,
]) {
  input.addEventListener("change", sendAssistantSettings);
}

setStatus("disconnected");
