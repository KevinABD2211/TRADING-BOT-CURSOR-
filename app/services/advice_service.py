"""
One advice per commodity: aggregate all sources (DB, Binance, CoinGecko, Finnhub),
analyze and return exactly one recommendation (LONG or SHORT) with target_price and stop_loss always set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AdviceItem:
    symbol: str
    direction: str  # "long" | "short"
    entry_price: float
    stop_loss: float
    target_price: float
    confidence_pct: int
    rationale: str
    sources_used: list[str]


def _get_tracked_symbols() -> list[str]:
    from app.config import get_settings
    raw = get_settings().advice.tracked_symbols or ""
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _default_sl_tp(entry: float, direction: str, sl_pct: float, tp_pct: float) -> tuple[float, float]:
    if direction == "long":
        return (
            entry * (1 - sl_pct / 100),
            entry * (1 + tp_pct / 100),
        )
    return (
        entry * (1 + sl_pct / 100),
        entry * (1 - tp_pct / 100),
    )


async def _signals_from_db(symbol: str, db) -> list[dict[str, Any]]:
    from sqlalchemy import desc, select
    from app.models import ParsedSignal
    q = (
        select(ParsedSignal)
        .where(ParsedSignal.symbol == symbol.upper())
        .order_by(desc(ParsedSignal.parsed_at))
        .limit(20)
    )
    result = await db.execute(q)
    rows = result.scalars().all()
    out = []
    for s in rows:
        entry = float(s.entry_price) if s.entry_price is not None else None
        if entry is None:
            continue
        out.append({
            "source": s.source.value if hasattr(s.source, "value") else str(s.source),
            "direction": s.direction.value if hasattr(s.direction, "value") else str(s.direction),
            "entry_price": entry,
            "stop_loss": float(s.stop_loss) if s.stop_loss is not None else None,
            "take_profit_1": float(s.take_profit_1) if s.take_profit_1 is not None else None,
            "confidence_pct": s.signal_completeness_pct or 50,
        })
    return out


async def _market_sources_for_symbol(symbol: str) -> list[dict[str, Any]]:
    from app.services.market_signal_service import get_market_signals, fetch_coingecko_movers
    out = []
    binance = await get_market_signals(limit=100)
    for r in binance:
        if (r.get("symbol") or "").upper() == symbol.upper():
            out.append({
                "source": "binance",
                "direction": r.get("direction", "long"),
                "entry_price": r.get("entry_price"),
                "stop_loss": None,
                "take_profit_1": None,
                "confidence_pct": r.get("confidence_pct", 50),
            })
    coingecko = await fetch_coingecko_movers()
    for r in coingecko:
        if (r.get("symbol") or "").upper() == symbol.upper():
            out.append({
                "source": "coingecko",
                "direction": r.get("direction", "long"),
                "entry_price": r.get("entry_price"),
                "stop_loss": None,
                "take_profit_1": None,
                "confidence_pct": r.get("confidence_pct", 50),
            })
    return out


async def _research_for_symbol(symbol: str, direction: str) -> dict[str, Any]:
    from app.services.research_service import get_research_confidence
    try:
        return await get_research_confidence(symbol, direction)
    except Exception as e:
        logger.debug("Research for %s failed: %s", symbol, e)
        return {"confidence_pct": 50, "rationale": "", "sources": []}


async def get_advice_for_symbol(symbol: str, db) -> AdviceItem:
    """
    Aggregate all info for one symbol and return a single advice: LONG or SHORT
    with entry_price, stop_loss and target_price always set.
    """
    from app.config import get_settings
    settings = get_settings()
    sl_pct = settings.advice.default_sl_pct
    tp_pct = settings.advice.default_tp_pct

    db_signals = await _signals_from_db(symbol, db)
    market = await _market_sources_for_symbol(symbol)
    all_inputs = db_signals + market

    sources_used = list({s["source"] for s in all_inputs})
    if not sources_used:
        sources_used = ["config_only"]

    # Decide direction: majority vote by confidence-weighted count
    long_score = sum(s.get("confidence_pct", 50) for s in all_inputs if (s.get("direction") or "").lower() == "long")
    short_score = sum(s.get("confidence_pct", 50) for s in all_inputs if (s.get("direction") or "").lower() == "short")
    direction = "long" if long_score >= short_score else "short"

    # Best entry: prefer DB (has SL/TP), then market
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None
    for s in db_signals:
        ep = s.get("entry_price")
        if ep is not None:
            entry_price = ep
            stop_loss = s.get("stop_loss")
            target_price = s.get("take_profit_1")
            break
    if entry_price is None and market:
        for s in market:
            ep = s.get("entry_price")
            if ep is not None:
                entry_price = ep
                break
    if entry_price is None:
        # No price anywhere: try Alpaca or use placeholder (advice still has SL/TP)
        try:
            from app.services.alpaca_service import get_latest_price
            entry_price = await get_latest_price(symbol)
        except Exception:
            pass
        if entry_price is None:
            entry_price = 0.0  # fallback; SL/TP will be 0

    if stop_loss is None or target_price is None:
        sl, tp = _default_sl_tp(entry_price or 0, direction, sl_pct, tp_pct)
        if stop_loss is None:
            stop_loss = sl
        if target_price is None:
            target_price = tp

    # Confidence: average of inputs, or research
    if all_inputs:
        confidence_pct = sum(s.get("confidence_pct", 50) for s in all_inputs) // max(1, len(all_inputs))
    else:
        confidence_pct = 50
    research = await _research_for_symbol(symbol, direction)
    if research.get("rationale"):
        confidence_pct = (confidence_pct + research["confidence_pct"]) // 2
        rationale = research["rationale"]
        if research.get("sources"):
            sources_used = list(set(sources_used + research["sources"]))
    else:
        rationale = f"Aggregated from {', '.join(sources_used)}. Direction from signal majority."

    return AdviceItem(
        symbol=symbol.upper(),
        direction=direction,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_price=target_price,
        confidence_pct=max(0, min(100, confidence_pct)),
        rationale=rationale or "No additional research.",
        sources_used=sources_used,
    )


async def get_all_advice(db) -> list[AdviceItem]:
    """One advice per tracked commodity (symbol)."""
    symbols = _get_tracked_symbols()
    if not symbols:
        symbols = ["AAPL", "MSFT", "BTC", "ETH"]
    out = []
    for sym in symbols:
        try:
            out.append(await get_advice_for_symbol(sym, db))
        except Exception as e:
            logger.warning("Advice for %s failed: %s", sym, e)
    return out
