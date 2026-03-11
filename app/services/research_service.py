"""
Research from trusted finance sources (e.g. Finnhub) and confidence scoring.
Uses news/sentiment to assign a confidence level to a trade idea.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

FINNHUB_NEWS = "https://finnhub.io/api/v1/company-news"


async def fetch_company_news(symbol: str, limit: int = 10) -> list[dict[str, Any]]:
    """Fetch recent company news from Finnhub (trusted source). Requires RESEARCH_FINNHUB_API_KEY."""
    try:
        from app.config import get_settings
        key = get_settings().research.finnhub_api_key
        if not key:
            return []
    except Exception:
        return []
    try:
        import httpx
    except ImportError:
        return []
    from datetime import datetime, timedelta, timezone
    to_ = datetime.now(timezone.utc)
    from_ = (to_ - timedelta(days=7)).strftime("%Y-%m-%d")
    to_str = to_.strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(
                FINNHUB_NEWS,
                params={"symbol": symbol.upper(), "from": from_, "to": to_str, "token": key},
            )
            if r.status_code != 200:
                return []
            data = r.json()
            return (data[:limit] if isinstance(data, list) else [])
    except Exception as e:
        logger.warning("Finnhub news fetch failed for %s: %s", symbol, e)
        return []


async def get_research_confidence(
    symbol: str,
    direction: str,
    signal_summary: Optional[str] = None,
) -> dict[str, Any]:
    """
    Conduct research from trusted sources and return a confidence level (0-100) for the trade.
    Uses Finnhub news; optionally LLM to summarize and score.
    Returns: { confidence_pct, rationale, sources }
    """
    sources: list[str] = []
    news = await fetch_company_news(symbol, limit=8)
    if news:
        sources.append("Finnhub (company news)")
    headlines = []
    for n in news[:5]:
        if isinstance(n, dict) and n.get("headline"):
            headlines.append(n.get("headline", "")[:200])
    rationale = ""
    confidence_pct = 50  # default when no research
    if headlines:
        rationale = "Recent headlines: " + " | ".join(headelines[:3])
        # Simple heuristic: more news = slightly higher confidence; could be replaced by LLM
        confidence_pct = min(85, 50 + len(headlines) * 5)
    try:
        from app.config import get_settings
        if get_settings().research.use_llm_for_confidence and headlines:
            # Optional: call LLM to score confidence from headlines + signal
            confidence_pct, rationale = await _llm_confidence(symbol, direction, signal_summary or "", headlines)
    except Exception as e:
        logger.debug("LLM confidence skip: %s", e)
    return {
        "confidence_pct": max(0, min(100, confidence_pct)),
        "rationale": rationale or "No recent news. Set RESEARCH_FINNHUB_API_KEY for trusted source data.",
        "sources": sources if sources else ["None configured"],
    }


async def _llm_confidence(
    symbol: str,
    direction: str,
    signal_summary: str,
    headlines: list[str],
) -> tuple[int, str]:
    """Use LLM to produce confidence 0-100 and short rationale from news + signal."""
    from app.config import get_settings
    settings = get_settings()
    if settings.llm.provider == "disabled" or not getattr(settings.llm, "openai_api_key", None):
        return (55, "News available but LLM disabled for confidence scoring.")
    import json
    text = f"Symbol: {symbol}. Direction: {direction}. Signal: {signal_summary}. Recent headlines: " + " ".join(headlines[:5])
    prompt = (
        "Based on the following trading idea and recent news headlines, output a single JSON object with two keys: "
        '"confidence_pct" (integer 0-100, how confident you are in this trade given the news) and '
        '"rationale" (one short sentence). Be conservative. Output only the JSON, no markdown."
    )
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.llm.openai_api_key)
        r = await client.chat.completions.create(
            model=settings.llm.model_name,
            messages=[{"role": "user", "content": prompt + "\n\n" + text[:2000]}],
            max_tokens=150,
        )
        content = (r.choices[0].message.content or "").strip()
        if "```" in content:
            content = content.split("```")[1].replace("json", "").strip()
        data = json.loads(content)
        return (
            max(0, min(100, int(data.get("confidence_pct", 50)))),
            str(data.get("rationale", ""))[:500],
        )
    except Exception as e:
        logger.warning("LLM confidence failed: %s", e)
        return (50, "News available; LLM scoring failed.")
