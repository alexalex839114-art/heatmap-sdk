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
    """Map 'BTCUSDT' to OKX spot ``instId`` ``BTC-USDT``.

    OKX natively lists USDT, USDC and USD spot pairs, so the quote is passed
    through unchanged (no USDT -> USD remapping like Coinbase/Kraken).
    """
    parts = split_symbol(symbol)
    if parts is None:
        return None
    base, quote = parts
    return f"{base}-{quote}"
