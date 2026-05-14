const COLOR_STOPS = [
  [0.0, [16, 40, 120]],
  [0.25, [35, 104, 214]],
  [0.5, [118, 72, 208]],
  [0.75, [214, 66, 118]],
  [1.0, [255, 72, 18]],
];
const COLUMN_WIDTH = 6;
const TRADE_HISTORY_LIMIT = 70;

function lerp(a, b, t) {
  return a + (b - a) * t;
}

export function colorForIntensity(intensity) {
  const normalized = Math.max(0, Math.min(255, intensity)) / 255;

  for (let index = 1; index < COLOR_STOPS.length; index += 1) {
    const [rightStop, rightColor] = COLOR_STOPS[index];
    const [leftStop, leftColor] = COLOR_STOPS[index - 1];
    if (normalized <= rightStop) {
      const span = rightStop - leftStop || 1;
      const localT = (normalized - leftStop) / span;
      return [
        Math.round(lerp(leftColor[0], rightColor[0], localT)),
        Math.round(lerp(leftColor[1], rightColor[1], localT)),
        Math.round(lerp(leftColor[2], rightColor[2], localT)),
        255,
      ];
    }
  }

  const lastColor = COLOR_STOPS.at(-1)[1];
  return [lastColor[0], lastColor[1], lastColor[2], 255];
}

export function tradeColor(trade) {
  return trade?.is_buyer_maker
    ? "rgba(248,113,113,0.95)"
    : "rgba(74,222,128,0.95)";
}

export function tradeRadius(trade) {
  const qty = Math.max(0, Number(trade?.qty || 0));
  return Math.max(1.5, Math.min(5, 1.3 + Math.sqrt(qty) * 0.9));
}

export function createRenderer(heatmapCanvas, overlayCanvas) {
  const context = heatmapCanvas.getContext("2d", { alpha: false });
  const overlay = overlayCanvas.getContext("2d");
  const width = heatmapCanvas.width;
  const height = heatmapCanvas.height;
  let imageBuffer = new Uint8ClampedArray(width * height * 4);
  let tradeMarkers = [];

  function paint() {
    context.putImageData(new ImageData(imageBuffer, width, height), 0, 0);
  }

  function appendColumn(column) {
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width - COLUMN_WIDTH; x += 1) {
        const currentIndex = (y * width + x) * 4;
        const nextIndex = (y * width + x + COLUMN_WIDTH) * 4;
        imageBuffer[currentIndex] = imageBuffer[nextIndex];
        imageBuffer[currentIndex + 1] = imageBuffer[nextIndex + 1];
        imageBuffer[currentIndex + 2] = imageBuffer[nextIndex + 2];
        imageBuffer[currentIndex + 3] = 255;
      }

      const intensity = column[y] ?? 0;
      const [r, g, b, a] = colorForIntensity(intensity);

      for (let fill = 0; fill < COLUMN_WIDTH; fill += 1) {
        const writeIndex = (y * width + (width - COLUMN_WIDTH + fill)) * 4;
        imageBuffer[writeIndex] = r;
        imageBuffer[writeIndex + 1] = g;
        imageBuffer[writeIndex + 2] = b;
        imageBuffer[writeIndex + 3] = a;
      }
    }

    shiftTradeMarkers();
    paint();
    paintTrades();
  }

  function drawTrades(trades) {
    for (const trade of trades) {
      tradeMarkers.push({
        x: width - Math.max(8, COLUMN_WIDTH),
        y: trade.y ?? 0,
        qty: trade.qty ?? 0,
        is_buyer_maker: Boolean(trade.is_buyer_maker),
      });
    }
    if (tradeMarkers.length > TRADE_HISTORY_LIMIT) {
      tradeMarkers = tradeMarkers.slice(-TRADE_HISTORY_LIMIT);
    }
    paintTrades();
  }

  function shiftTradeMarkers() {
    tradeMarkers = tradeMarkers
      .map((trade) => ({ ...trade, x: trade.x - COLUMN_WIDTH }))
      .filter((trade) => trade.x >= 0);
  }

  function paintTrades() {
    overlay.clearRect(0, 0, width, height);

    for (const trade of tradeMarkers) {
      const y = trade.y ?? 0;
      const alpha = Math.max(0.05, Math.min(0.9, (trade.x / width) ** 1.8));
      overlay.globalAlpha = alpha;
      overlay.fillStyle = tradeColor(trade);
      overlay.beginPath();
      overlay.arc(trade.x, y, tradeRadius(trade), 0, Math.PI * 2);
      overlay.fill();
    }
    overlay.globalAlpha = 1;
  }

  function reset() {
    imageBuffer = new Uint8ClampedArray(width * height * 4);
    for (let i = 0; i < imageBuffer.length; i += 4) {
      imageBuffer[i] = 6;
      imageBuffer[i + 1] = 8;
      imageBuffer[i + 2] = 11;
      imageBuffer[i + 3] = 255;
    }
    tradeMarkers = [];
    overlay.clearRect(0, 0, width, height);
    paint();
  }

  reset();
  return { appendColumn, drawTrades, reset };
}
