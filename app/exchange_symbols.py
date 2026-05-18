"""Heuristic symbol mapping from Binance USDT perps to spot venues.

The app is driven by Binance-style `BTCUSDT` input; Coinbase and Kraken list
spot pairs with different conventions. We do a best-effort translation and
return ``None`` when the pair is not obviously listable.
"""

from __future__ import annotations


_QUOTE_CANDIDATES = ("USDT", "USDC", "USD", "BUSD", "FDUSD", "EUR", "GBP", "BTC", "ETH")


def split_symbol(symbol: str) -> tuple[str, str] | None:
    """Split a concatenated pair like 'BTCUSDT' into ('BTC', 'USDT')."""
    normalized = symbol.strip().upper()
    for quote in _QUOTE_CANDIDATES:
        if len(normalized) > len(quote) and normalized.endswith(quote):
            base = normalized[: -len(quote)]
            if base:
                return base, quote
    return None


def to_coinbase_product(symbol: str) -> str | None:
    """Map 'BTCUSDT' to 'BTC-USD'.

    Coinbase spot lists USD products; USDT/BUSD/FDUSD pairs are treated as
    their USD equivalents because Coinbase's docs note that -USD and -USDC
    surface the same underlying market.
    """
    parts = split_symbol(symbol)
    if parts is None:
        return None
    base, quote = parts
    if quote in {"USDT", "USD", "BUSD", "FDUSD"}:
        mapped_quote = "USD"
    elif quote == "USDC":
        mapped_quote = "USDC"
    else:
        mapped_quote = quote
    return f"{base}-{mapped_quote}"


def to_kraken_pair(symbol: str) -> str | None:
    """Map 'BTCUSDT' to 'BTC/USD'.

    Kraken's WebSocket v2 API uses the aligned pair format ``BTC/USD``,
    whereas v1 used ``XBT/USD``. We stay on v2 throughout this app.
    """
    parts = split_symbol(symbol)
    if parts is None:
        return None
    base, quote = parts
    if quote in {"USDT", "USD", "BUSD", "FDUSD"}:
        mapped_quote = "USD"
    elif quote == "USDC":
        mapped_quote = "USDC"
    else:
        mapped_quote = quote
    return f"{base}/{mapped_quote}"


def to_okx_inst_id(symbol: str) -> str | None:
    """Map 'BTCUSDT' to OKX **perpetual swap** ``instId`` ``BTC-USDT-SWAP``.

    The host app is driven by Binance USDⓈ-M perpetual futures, so the OKX
    indicator must read the matching market — a USDT-margined perpetual swap
    (OKX ``SWAP`` instrument type), not spot. OKX naming convention for the
    linear perp is ``<BASE>-<QUOTE>-SWAP`` (e.g. ``BTC-USDT-SWAP``,
    ``ETH-USDT-SWAP``).

    USDC-quoted Binance futures (rare today) map to the OKX USDC perpetual
    ``<BASE>-USDC-SWAP`` where listed. Any other quote currency is left as
    the bare ``<BASE>-<QUOTE>-SWAP`` form and will be reported as
    ``unavailable`` by the OKX client if OKX does not list that perp.

    The same v5 public WebSocket URL and ``books`` / ``trades`` channels
    accept SWAP instIds, so no other code needs to change.
    """
    parts = split_symbol(symbol)
    if parts is None:
        return None
    base, quote = parts
    if quote in {"USDT", "USD", "BUSD", "FDUSD"}:
        mapped_quote = "USDT"
    elif quote == "USDC":
        mapped_quote = "USDC"
    else:
        mapped_quote = quote
    return f"{base}-{mapped_quote}-SWAP"


def to_gate_contract(symbol: str) -> str | None:
    """Map 'BTCUSDT' to a Gate.io USDT-margined **perpetual** contract id.

    Gate.io futures use the ``<BASE>_<QUOTE>`` underscore convention (e.g.
    ``BTC_USDT``, ``ETH_USDT``, ``IRYS_USDT``), all served from the same
    USDT-margined linear perp WebSocket endpoint.

    USDC-quoted Binance symbols are not currently listed as USDT-margined
    perps on Gate, so we still emit ``<BASE>_USDC`` and let the client report
    the contract as ``unavailable`` when Gate rejects it. Any other quote is
    passed through unchanged.
    """
    parts = split_symbol(symbol)
    if parts is None:
        return None
    base, quote = parts
    if quote in {"USDT", "USD", "BUSD", "FDUSD"}:
        mapped_quote = "USDT"
    elif quote == "USDC":
        mapped_quote = "USDC"
    else:
        mapped_quote = quote
    return f"{base}_{mapped_quote}"
