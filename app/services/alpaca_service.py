"""
Alpaca trading integration: account, quotes, and order execution.
Uses REST API with httpx. Set ALPACA_API_KEY and ALPACA_API_SECRET (and optional ALPACA_ENVIRONMENT=paper|live).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

TRADING_BASE = "https://paper-api.alpaca.markets"  # override from config
DATA_BASE = "https://data.alpaca.markets"


def _get_trading_base() -> str:
    try:
        return get_settings().alpaca.base_url
    except Exception:
        return TRADING_BASE


def _get_auth() -> tuple[str, str]:
    s = get_settings().alpaca
    return (s.api_key, s.api_secret)


def _headers() -> dict[str, str]:
    key, secret = _get_auth()
    return {
        "APCA-API-KEY-ID": key,
        "APCA-API-SECRET-KEY": secret,
        "Content-Type": "application/json",
    }


async def get_account() -> Optional[dict[str, Any]]:
    """Fetch Alpaca account (cash, buying_power, etc.). Returns None if not configured or request fails."""
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed")
        return None
    key, secret = _get_auth()
    if not key or not secret:
        return None
    base = _get_trading_base()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base}/v2/account", headers=_headers())
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.warning("Alpaca get_account failed: %s", e)
        return None


async def get_latest_trade(symbol: str) -> Optional[dict[str, Any]]:
    """Latest trade for symbol from Alpaca data API. Returns None if unavailable."""
    try:
        import httpx
    except ImportError:
        return None
    key, secret = _get_auth()
    if not key or not secret:
        return None
    sym = symbol.upper()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{DATA_BASE}/v2/stocks/{sym}/trades/latest",
                headers=_headers(),
            )
            if r.status_code != 200:
                return None
            return r.json()
    except Exception as e:
        logger.debug("Alpaca get_latest_trade %s: %s", symbol, e)
        return None


def _price_from_trade(trade: Optional[dict]) -> Optional[float]:
    if not trade or "trade" not in trade:
        return None
    t = trade["trade"]
    if isinstance(t, dict) and "p" in t:
        return float(t["p"])
    return None


async def get_latest_price(symbol: str) -> Optional[float]:
    """Current price for symbol (from latest trade). Returns None if not available."""
    trade = await get_latest_trade(symbol)
    return _price_from_trade(trade)


async def place_order(
    symbol: str,
    side: str,
    qty: Optional[float] = None,
    notional: Optional[float] = None,
    order_type: str = "market",
    time_in_force: str = "day",
) -> Optional[dict[str, Any]]:
    """
    Place an order on Alpaca. side is 'buy' or 'sell'.
    Provide qty (shares) or notional (USD). Returns order dict or None on failure.
    """
    try:
        import httpx
    except ImportError:
        logger.warning("httpx not installed")
        return None
    key, secret = _get_auth()
    if not key or not secret:
        logger.warning("Alpaca credentials not set")
        return None
    if (qty is None or qty <= 0) and (notional is None or notional <= 0):
        logger.warning("place_order requires qty or notional")
        return None
    base = _get_trading_base()
    payload: dict[str, Any] = {
        "symbol": symbol.upper(),
        "side": side.lower(),
        "type": order_type.lower(),
        "time_in_force": time_in_force,
    }
    if qty is not None and qty > 0:
        settings = get_settings().alpaca
        if settings.fractional_shares_enabled and order_type == "market":
            payload["qty"] = round(qty, 8)
        else:
            payload["qty"] = int(qty)
    elif notional is not None and notional > 0:
        payload["notional"] = str(round(notional, 2))
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{base}/v2/orders", headers=_headers(), json=payload)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.exception("Alpaca place_order failed: %s", e)
        return None


async def is_alpaca_configured() -> bool:
    """True if API key/secret are set and non-empty."""
    try:
        key, secret = _get_auth()
        return bool(key and secret)
    except Exception:
        return False
