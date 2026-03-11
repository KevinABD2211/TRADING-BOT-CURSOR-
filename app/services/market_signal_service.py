"""
Live market signals from public APIs (no API key required).
Uses Binance 24h ticker for crypto movers; can be extended to other sources.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

BINANCE_TICKER_24H = "https://api.binance.com/api/v3/ticker/24hr"


def _fetch_binance_sync() -> list[dict[str, Any]]:
    """Sync fetch (no extra deps)."""
    try:
        with urllib.request.urlopen(BINANCE_TICKER_24H, timeout=15) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        logger.warning("Binance 24h ticker fetch failed: %s", e)
        return []
    return data if isinstance(data, list) else []


async def fetch_binance_24h_ticker() -> list[dict[str, Any]]:
    """Fetch 24h price change for all symbols from Binance (public, no key)."""
    import asyncio
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _fetch_binance_sync)

    # Only USDT pairs, exclude low volume
    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        symbol = item.get("symbol") or ""
        if not symbol.endswith("USDT"):
            continue
        quote_vol = float(item.get("quoteVolume") or 0)
        if quote_vol < 100_000:
            continue
        try:
            pct = float(item.get("priceChangePercent") or 0)
        except (TypeError, ValueError):
            continue
        last_price = None
        try:
            last_price = float(item.get("lastPrice") or 0)
        except (TypeError, ValueError):
            pass
        base = symbol.replace("USDT", "")
        pct_val = round(pct, 2)
        confidence_pct = min(100, int(abs(pct_val)))  # 0-100 from |move%|
        out.append({
            "symbol": base,
            "direction": "long" if pct >= 0 else "short",
            "entry_price": last_price,
            "price_change_pct": pct_val,
            "confidence_pct": confidence_pct,
            "quote_volume": quote_vol,
            "raw_text_preview": f"24h {pct:+.2f}% (Binance {symbol})",
            "signal_timestamp": now.isoformat(),
            "parse_method": "market_api",
            "source": "binance",
        })
    out.sort(key=lambda x: abs(x["price_change_pct"]), reverse=True)
    return out


async def get_market_signals(limit: int = 50) -> list[dict[str, Any]]:
    """Return market-derived signals (top movers from Binance)."""
    rows = await fetch_binance_24h_ticker()
    return rows[:limit]


# --- CoinGecko (additional free source for crypto) ---
COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"


def _fetch_coingecko_sync() -> list[dict[str, Any]]:
    """Sync fetch CoinGecko top coins by volume (no API key)."""
    try:
        import urllib.parse
        params = urllib.parse.urlencode({
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": 30,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
        })
        with urllib.request.urlopen(f"{COINGECKO_MARKETS}?{params}", timeout=15) as r:
            data = json.loads(r.read().decode())
    except Exception as e:
        logger.warning("CoinGecko markets fetch failed: %s", e)
        return []
    return data if isinstance(data, list) else []


async def fetch_coingecko_movers() -> list[dict[str, Any]]:
    """Fetch top crypto by volume from CoinGecko as signal-like items (source=coingecko)."""
    import asyncio
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _fetch_coingecko_sync)
    now = datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        sym = (item.get("symbol") or "").upper()
        if not sym:
            continue
        try:
            price = float(item.get("current_price") or 0)
            pct = float(item.get("price_change_percentage_24h") or 0)
        except (TypeError, ValueError):
            continue
        out.append({
            "symbol": sym,
            "direction": "long" if pct >= 0 else "short",
            "entry_price": price,
            "price_change_pct": round(pct, 2),
            "confidence_pct": min(100, int(abs(pct))),
            "raw_text_preview": f"24h {pct:+.2f}% (CoinGecko {sym})",
            "signal_timestamp": now.isoformat(),
            "parse_method": "market_api",
            "source": "coingecko",
        })
    out.sort(key=lambda x: abs(x["price_change_pct"]), reverse=True)
    return out
