FRAME_INTERVAL_MS = 100
HEATMAP_HEIGHT = 768
HEATMAP_WIDTH = 2000
HEATMAP_BUFFER_LEVELS = 128
HEATMAP_RECENTER_MARGIN_LEVELS = 32

BINANCE_FAPI_REST_URL = "https://fapi.binance.com"
BINANCE_FAPI_PUBLIC_WS_URL = "wss://fstream.binance.com/public/stream"
BINANCE_FAPI_MARKET_WS_URL = "wss://fstream.binance.com/market/stream"
BINANCE_FAPI_USER_WS_URL = "wss://fstream.binance.com/ws"

# Position state refresh policy.
# User data WebSocket pushes ACCOUNT_UPDATE in real time. The REST endpoint
# /fapi/v3/positionRisk is kept as a fallback / sanity check and is polled at
# this slower cadence to stay well under Binance's per-IP rate limits
# (weight 5 per call, IP limit 2400/min).
POSITION_REST_FALLBACK_INTERVAL_MS = 10_000
# Binance recommends keepalive every 30 minutes; the listen key expires after
# 60 minutes of no keepalive.
LISTEN_KEY_KEEPALIVE_INTERVAL_MS = 30 * 60 * 1000
BYBIT_LINEAR_PUBLIC_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_REST_URL = "https://api.bybit.com"
# Coinbase and Kraken clients remain in the codebase but their connections are
# disabled — the third indicator slot is now OKX.
COINBASE_ADVANCED_WS_URL = "wss://advanced-trade-ws.coinbase.com"
KRAKEN_PUBLIC_WS_V2_URL = "wss://ws.kraken.com/v2"
OKX_PUBLIC_WS_V5_URL = "wss://ws.okx.com:8443/ws/v5/public"
