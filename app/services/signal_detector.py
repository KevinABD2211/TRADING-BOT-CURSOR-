from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


_DIRECTION_PATTERN = re.compile(
    r"\b(buy|sell|long|short|enter|entry|going long|going short|"
    r"bullish|bearish|calls?|puts?)\b",
    re.IGNORECASE,
)

_PRICE_KEYWORD_PATTERN = re.compile(
    r"\b(entry|entries|sl|stop[\s-]?loss|tp[1-5]?|target[s]?|"
    r"take[\s-]?profit|t/p|s/l|invalidation|breakeven)\b",
    re.IGNORECASE,
)

_TICKER_PATTERNS = [
    re.compile(r"\$[A-Z]{1,8}\b"),
    re.compile(
        r"\b([A-Z]{2,10})(\/|-|)?(USDT|USDC|BTC|ETH|BUSD|USD|PERP)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b([A-Z]{2,5})\s+(stock|equity|shares?)\b", re.IGNORECASE),
    re.compile(r"\b([A-Z]{1,3})[1-9]!\B"),
    re.compile(r"\/[A-Z]{2,4}\b"),
    re.compile(
        r"\b[A-Z]{2,5}\s+\d{2,4}(\.\d{1,2})?\s?(C|P|CALL|PUT)\b",
        re.IGNORECASE,
    ),
]

_LEVERAGE_PATTERN = re.compile(
    r"\b(\d{1,3})[xX]\b|\b[xX](\d{1,3})\b|\b(\d{1,3})\s*[xX]\s*leverage\b",
    re.IGNORECASE,
)

_PRICE_VALUE_PATTERN = re.compile(
    r"\b\d{1,8}(\.\d{1,8})?\b"
)

_CONVICTION_PATTERN = re.compile(
    r"\b(high conviction|strong (buy|sell|signal)|"
    r"very bullish|very bearish|screaming|"
    r"major (support|resistance|level)|"
    r"breakout|breakdown|bounce|reversal|confluence)\b",
    re.IGNORECASE,
)

_NOISE_PATTERNS = [
    re.compile(r"^(gm|gn|lfg|ngmi|wagmi|ser|fren|anon)\b", re.IGNORECASE),
    re.compile(r"^\s*(lol|lmao|haha|😂|🚀|💎|🙌)\s*$"),
    re.compile(r"^https?://\S+$"),
    re.compile(r"^@\w+\s*$"),
]


SIGNAL_CONFIDENCE_THRESHOLD = 0.30


@dataclass
class DetectionResult:
    is_signal: bool
    confidence: float
    triggered_keywords: list[str] = field(default_factory=list)
    detected_ticker: Optional[str] = None
    detected_direction: Optional[str] = None
    has_price_levels: bool = False
    has_leverage: bool = False
    raw_score: float = 0.0
    reason: str = ""


class SignalDetector:
    def detect(self, content: str) -> tuple[bool, float, list[str]]:
        result = self.evaluate(content)
        return result.is_signal, result.confidence, result.triggered_keywords

    def evaluate(self, content: str) -> DetectionResult:
        if not content or not content.strip():
            return DetectionResult(is_signal=False, confidence=0.0, reason="empty")

        for noise_re in _NOISE_PATTERNS:
            if noise_re.search(content):
                return DetectionResult(
                    is_signal=False, confidence=0.0, reason="noise_pattern"
                )

        keywords: list[str] = []
        score: float = 0.0

        detected_ticker: Optional[str] = None
        for ticker_re in _TICKER_PATTERNS:
            match = ticker_re.search(content)
            if match:
                detected_ticker = match.group(0)
                keywords.append(f"ticker:{detected_ticker}")
                score += 0.35
                break

        detected_direction: Optional[str] = None
        direction_match = _DIRECTION_PATTERN.search(content)
        if direction_match:
            detected_direction = direction_match.group(0).lower()
            keywords.append(f"direction:{detected_direction}")
            score += 0.30

        price_keyword_matches = _PRICE_KEYWORD_PATTERN.findall(content)
        has_price_levels = len(price_keyword_matches) > 0
        if has_price_levels:
            level_bonus = min(0.25, len(price_keyword_matches) * 0.08)
            score += level_bonus
            for kw in price_keyword_matches[:5]:
                keywords.append(f"price_kw:{kw.lower()}")

        has_leverage = bool(_LEVERAGE_PATTERN.search(content))
        if has_leverage:
            keywords.append("leverage")
            score += 0.10

        conviction_match = _CONVICTION_PATTERN.search(content)
        if conviction_match:
            keywords.append(f"conviction:{conviction_match.group(0).lower()}")
            score += 0.10

        price_value_matches = _PRICE_VALUE_PATTERN.findall(content)
        if len(price_value_matches) >= 2:
            score += 0.05

        if len(content) < 20:
            score *= 0.4

        if detected_ticker is None and detected_direction is None:
            score *= 0.2

        confidence = min(1.0, max(0.0, score))
        is_signal = confidence >= SIGNAL_CONFIDENCE_THRESHOLD

        reason = (
            f"ticker={'yes' if detected_ticker else 'no'}, "
            f"direction={'yes' if detected_direction else 'no'}, "
            f"price_levels={len(price_keyword_matches)}, "
            f"leverage={'yes' if has_leverage else 'no'}"
        )

        return DetectionResult(
            is_signal=is_signal,
            confidence=round(confidence, 4),
            triggered_keywords=keywords,
            detected_ticker=detected_ticker,
            detected_direction=detected_direction,
            has_price_levels=has_price_levels,
            has_leverage=has_leverage,
            raw_score=round(score, 4),
            reason=reason,
        )

    def is_likely_noise(self, content: str) -> bool:
        result = self.evaluate(content)
        return not result.is_signal

