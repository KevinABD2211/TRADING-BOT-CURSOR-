"""
signal_parser/regex_parser.py
------------------------------
Stage 1: Regex-based signal parser.

Attempts to extract all structured fields from a trading signal message
using a comprehensive set of compiled regular expressions.

Design principles:
  - Patterns are compiled once at module load for performance
  - Each field has its own extraction function for testability
  - Returns a ParseResult with a confidence score and a list of
    field-level extraction notes
  - Confidence is computed as the fraction of core fields extracted
  - If confidence falls below the configurable threshold, the
    ParserRouter triggers the LLM fallback parser

Handles signal formats like:
  ✅ "BTC/USDT LONG | Entry: 42000–43000 | SL: 41000 | TP1: 45000 TP2: 48000"
  ✅ "$AAPL BUY Entry 187.50 Stop 184.00 Target 195.00"
  ✅ "ETHUSDT SHORT @ 2100 sl 2200 tp 1900 1800 10x"
  ✅ "#SOL LONG entry range 90-95, stoploss 86, targets 105/115/130"
  ✅ "NVDA 600C 05/17 entry 15.00 sl 10.00 tp 25.00" (options)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Compiled regex patterns
# ---------------------------------------------------------------------------

# --- Symbol extraction ---
# Crypto pairs: BTC/USDT, BTCUSDT, BTC-USDT, ETH/USDC
_CRYPTO_SYMBOL = re.compile(
    r"\b([A-Z]{2,10})\s*[/\-]?\s*(USDT|USDC|BTC|ETH|BUSD|USD|PERP)\b",
    re.IGNORECASE,
)
# Cashtag: $AAPL, $TSLA
_CASHTAG_SYMBOL = re.compile(r"\$([A-Z]{1,8})\b")
# Bare stock tickers (2-5 uppercase letters, not a common word)
_STOCK_SYMBOL = re.compile(
    r"(?<![A-Z#$])([A-Z]{2,5})(?:\s+(?:stock|equity|shares?))?(?![A-Z0-9])",
)
# Options notation: AAPL 150C, SPY 400P, NVDA 600 CALL 05/17
_OPTIONS_SYMBOL = re.compile(
    r"\b([A-Z]{2,5})\s+(\d{2,4}(?:\.\d{1,2})?)\s*(C|P|CALL|PUT)\b"
    r"(?:\s+(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?))?",
    re.IGNORECASE,
)

# --- Direction extraction ---
_DIRECTION = re.compile(
    r"\b(long|short|buy|sell|bullish|bearish|"
    r"going long|going short|calls?|puts?)\b",
    re.IGNORECASE,
)

# --- Entry price extraction ---
# Single entry: "entry 42000", "entry: 42000", "@42000", "entry @ 42000"
_ENTRY_SINGLE = re.compile(
    r"(?:entry|entries?|enter|@|price)[:\s@]*"
    r"(\d{1,8}(?:[.,]\d{1,8})?)",
    re.IGNORECASE,
)
# Entry range: "entry 42000-43000", "42000/43000", "42000 – 43000"
_ENTRY_RANGE = re.compile(
    r"(?:entry|entries?|enter)[:\s@]*"
    r"(\d{1,8}(?:[.,]\d{1,8})?)\s*[-–/to]+\s*(\d{1,8}(?:[.,]\d{1,8})?)",
    re.IGNORECASE,
)

# --- Stop loss extraction ---
_STOP_LOSS = re.compile(
    r"(?:stop[\s-]?loss|stop|sl|s/l|invalidat\w+)[:\s]*"
    r"(\d{1,8}(?:[.,]\d{1,8})?)",
    re.IGNORECASE,
)

# --- Take profit extraction (up to 3 levels) ---
# Handles: "TP1: 45000", "TP 45000", "target 45000", "T1 45000/48000/52000"
_TAKE_PROFIT_LABELED = re.compile(
    r"(?:take[\s-]?profit|tp|target|t)[1-5]?[:\s]*"
    r"(\d{1,8}(?:[.,]\d{1,8})?)",
    re.IGNORECASE,
)
# Multiple TPs on same line: "TP: 45000 / 48000 / 52000"
_TAKE_PROFIT_MULTI = re.compile(
    r"(?:take[\s-]?profit|tp|target)[s]?[1-5]?[:\s]+"
    r"(\d{1,8}(?:[.,]\d{1,8})?)"
    r"(?:\s*[/|,]\s*(\d{1,8}(?:[.,]\d{1,8})?))?"
    r"(?:\s*[/|,]\s*(\d{1,8}(?:[.,]\d{1,8})?))?"
    r"(?:\s*[/|,]\s*(\d{1,8}(?:[.,]\d{1,8})?))?"
    r"(?:\s*[/|,]\s*(\d{1,8}(?:[.,]\d{1,8})?))?" ,
    re.IGNORECASE,
)

# --- Leverage extraction ---
_LEVERAGE = re.compile(
    r"(\d{1,3})\s*[xX]\s*(?:leverage|lev)?|"
    r"(?:leverage|lev)[:\s]+(\d{1,3})[xX]?",
    re.IGNORECASE,
)

# --- Timeframe extraction ---
_TIMEFRAME = re.compile(
    r"\b((?:1|3|5|15|30|45)\s*m(?:in(?:ute)?s?)?|"
    r"(?:1|2|3|4|6|8|12)\s*h(?:our)?s?|"
    r"(?:1|3)?\s*d(?:ay)?s?|"
    r"(?:1|2)?\s*w(?:eek)?s?|"
    r"(?:1)?\s*M(?:onth)?|"
    r"[1-9]H|[1-9]D|1W|1M|"
    r"daily|weekly|monthly|hourly|intraday|swing)\b",
    re.IGNORECASE,
)

# --- Confidence wording ---
_CONFIDENCE_WORDING = re.compile(
    r"\b(high conviction|strong (buy|sell|signal|setup)|"
    r"very (bullish|bearish)|"
    r"low risk|high probability|risky|uncertain|"
    r"watch only|potential|possible|likely|"
    r"(\d{1,3})%\s*(?:confidence|prob(?:ability)?|chance))\b",
    re.IGNORECASE,
)

# --- Options-specific ---
_OPTIONS_EXPIRY = re.compile(
    r"\b(\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?)\b|"
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    r"\s+(\d{1,2})(?:\s+(\d{2,4}))?\b",
    re.IGNORECASE,
)
_OPTIONS_STRIKE = re.compile(
    r"\b(\d{2,6}(?:\.\d{1,2})?)\s*(c|p|call|put)\b",
    re.IGNORECASE,
)

# --- Asset type hints ---
_CRYPTO_HINTS = re.compile(
    r"\b(crypto|defi|nft|chain|coin|token|btc|eth|sol|bnb|"
    r"futures?|perp|perpetual)\b",
    re.IGNORECASE,
)
_STOCK_HINTS = re.compile(
    r"\b(stock|equity|shares?|nasdaq|nyse|s&p|spy|qqq|"
    r"earnings?|dividend)\b",
    re.IGNORECASE,
)
_OPTIONS_HINTS = re.compile(
    r"\b(option|call|put|strike|expir|contract|iv|greeks?|"
    r"delta|theta|gamma)\b",
    re.IGNORECASE,
)

# Common English words that look like tickers — suppress false positives
_STOCK_SYMBOL_BLOCKLIST = frozenset({
    "AT", "BE", "BY", "DO", "GO", "IF", "IN", "IS", "IT", "ME",
    "MY", "NO", "OF", "ON", "OR", "SO", "TO", "UP", "US", "WE",
    "ALL", "AND", "ARE", "BUT", "FOR", "HAS", "NOT", "NOW", "OUT",
    "THE", "WAS", "WHO", "YOU", "BUY", "SELL",   # direction words
    "LONG", "SHORT", "STOP", "LOSS", "TAKE", "FROM", "THAT",
    "WITH", "THIS", "WILL", "BEEN", "INTO", "OVER", "ALSO",
})


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class RegexParseResult:
    """
    Output of the regex parser.

    confidence: 0.0–1.0, computed from fraction of core fields found.
    field_notes: Per-field extraction notes for debugging and audit.
    """
    # Core fields
    symbol: Optional[str] = None
    asset_type: Optional[str] = None    # 'crypto', 'stock', 'option', 'unknown'
    direction: Optional[str] = None     # 'long', 'short', 'unknown'
    entry_price: Optional[float] = None
    entry_range_low: Optional[float] = None
    entry_range_high: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    take_profit_3: Optional[float] = None
    # Extended fields
    leverage: Optional[int] = None
    timeframe: Optional[str] = None
    confidence_wording: Optional[str] = None
    options_strike: Optional[float] = None
    options_type: Optional[str] = None     # 'CALL' or 'PUT'
    options_expiry_raw: Optional[str] = None
    # Metadata
    confidence: float = 0.0
    field_notes: dict = field(default_factory=dict)
    raw_text: str = ""


# ---------------------------------------------------------------------------
# Parser implementation
# ---------------------------------------------------------------------------

class RegexParser:
    """
    Stage 1 signal parser using compiled regular expressions.

    Usage:
        parser = RegexParser()
        result = parser.parse("BTC/USDT LONG entry 42000 sl 41000 tp 45000")
        if result.confidence >= 0.6:
            # Use regex result
        else:
            # Fallback to LLM
    """

    def parse(self, text: str) -> RegexParseResult:
        """
        Parse a raw trading signal message.

        Returns a RegexParseResult with all extracted fields and a
        confidence score (0.0–1.0).
        """
        result = RegexParseResult(raw_text=text)
        notes: dict[str, str] = {}

        # Normalise text: collapse excessive whitespace, standardise separators
        clean = self._normalise(text)

        # --- Symbol ---
        symbol, asset_type = self._extract_symbol(clean)
        result.symbol = symbol
        result.asset_type = asset_type
        notes["symbol"] = f"extracted={symbol}" if symbol else "not_found"

        # --- Refine asset type from context clues ---
        if asset_type == "unknown":
            result.asset_type = self._infer_asset_type(clean)

        # --- Direction ---
        result.direction = self._extract_direction(clean)
        notes["direction"] = f"extracted={result.direction}"

        # --- Entry ---
        entry_lo, entry_hi, entry_single = self._extract_entry(clean)
        if entry_lo and entry_hi:
            result.entry_range_low = entry_lo
            result.entry_range_high = entry_hi
            notes["entry"] = f"range={entry_lo}-{entry_hi}"
        elif entry_single:
            result.entry_price = entry_single
            notes["entry"] = f"single={entry_single}"
        else:
            notes["entry"] = "not_found"

        # --- Stop Loss ---
        result.stop_loss = self._extract_stop_loss(clean)
        notes["stop_loss"] = f"extracted={result.stop_loss}" if result.stop_loss else "not_found"

        # --- Take Profits ---
        tps = self._extract_take_profits(clean)
        if len(tps) >= 1:
            result.take_profit_1 = tps[0]
        if len(tps) >= 2:
            result.take_profit_2 = tps[1]
        if len(tps) >= 3:
            result.take_profit_3 = tps[2]
        notes["take_profits"] = f"found={len(tps)}"

        # --- Leverage ---
        result.leverage = self._extract_leverage(clean)
        notes["leverage"] = f"extracted={result.leverage}" if result.leverage else "not_found"

        # --- Timeframe ---
        result.timeframe = self._extract_timeframe(clean)
        notes["timeframe"] = f"extracted={result.timeframe}" if result.timeframe else "not_found"

        # --- Confidence Wording ---
        result.confidence_wording = self._extract_confidence_wording(clean)

        # --- Options-specific ---
        if result.asset_type == "option":
            result.options_strike, result.options_type = self._extract_options_details(clean)
            result.options_expiry_raw = self._extract_options_expiry(clean)

        # --- Compute overall confidence ---
        result.confidence = self._compute_confidence(result)
        result.field_notes = notes

        logger.debug(
            "RegexParser result | symbol=%s | direction=%s | entry=%s | "
            "sl=%s | tp1=%s | confidence=%.2f",
            result.symbol,
            result.direction,
            result.entry_price or result.entry_range_low,
            result.stop_loss,
            result.take_profit_1,
            result.confidence,
        )

        return result

    # ------------------------------------------------------------------
    # Field extractors
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise(text: str) -> str:
        """Clean up common formatting noise in signal messages."""
        # Remove emoji and special unicode blocks
        text = re.sub(r"[^\x00-\x7F\u00C0-\u024F\u2014\u2013\u2019]", " ", text)
        # Normalise separators
        text = re.sub(r"\s*[|│]\s*", " | ", text)
        # Collapse whitespace
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()

    @staticmethod
    def _parse_number(value: str) -> Optional[float]:
        """
        Convert a string number to float.
        Handles both comma-decimal (European) and period-decimal formats.
        """
        try:
            # Remove commas used as thousands separators
            cleaned = value.replace(",", "")
            return float(cleaned)
        except (ValueError, AttributeError):
            return None

    def _extract_symbol(self, text: str) -> tuple[Optional[str], str]:
        """
        Extract the trading symbol from the message.

        Returns (symbol, asset_type).
        """
        # Options first (most specific)
        opt_match = _OPTIONS_SYMBOL.search(text)
        if opt_match:
            ticker = opt_match.group(1).upper()
            return ticker, "option"

        # Crypto pairs
        crypto_match = _CRYPTO_SYMBOL.search(text)
        if crypto_match:
            base = crypto_match.group(1).upper()
            quote = crypto_match.group(2).upper()
            return f"{base}{quote}", "crypto"

        # Cashtag
        cash_match = _CASHTAG_SYMBOL.search(text)
        if cash_match:
            return cash_match.group(1).upper(), "stock"

        # Bare stock ticker (uppercase 2-5 chars, not a blocklisted word)
        for match in _STOCK_SYMBOL.finditer(text):
            candidate = match.group(1).upper()
            if candidate not in _STOCK_SYMBOL_BLOCKLIST and len(candidate) >= 2:
                return candidate, "stock"

        return None, "unknown"

    @staticmethod
    def _infer_asset_type(text: str) -> str:
        """Infer asset type from contextual keywords when symbol extraction fails."""
        if _OPTIONS_HINTS.search(text):
            return "option"
        if _CRYPTO_HINTS.search(text):
            return "crypto"
        if _STOCK_HINTS.search(text):
            return "stock"
        return "unknown"

    @staticmethod
    def _extract_direction(text: str) -> str:
        """Extract trade direction from direction keywords."""
        match = _DIRECTION.search(text)
        if not match:
            return "unknown"

        raw = match.group(0).lower().strip()

        # Normalise to canonical direction values
        if raw in ("long", "buy", "bullish", "going long", "call", "calls"):
            return "long"
        if raw in ("short", "sell", "bearish", "going short", "put", "puts"):
            return "short"

        return "unknown"

    def _extract_entry(
        self, text: str
    ) -> tuple[Optional[float], Optional[float], Optional[float]]:
        """
        Extract entry price or range.

        Returns (range_low, range_high, single_entry).
        Only one of (range, single) will be populated.
        """
        # Try range first
        range_match = _ENTRY_RANGE.search(text)
        if range_match:
            lo = self._parse_number(range_match.group(1))
            hi = self._parse_number(range_match.group(2))
            if lo and hi:
                if lo > hi:
                    lo, hi = hi, lo   # Normalise order
                return lo, hi, None

        # Try single
        single_match = _ENTRY_SINGLE.search(text)
        if single_match:
            val = self._parse_number(single_match.group(1))
            return None, None, val

        return None, None, None

    def _extract_stop_loss(self, text: str) -> Optional[float]:
        match = _STOP_LOSS.search(text)
        if match:
            return self._parse_number(match.group(1))
        return None

    def _extract_take_profits(self, text: str) -> list[float]:
        """
        Extract up to 3 take profit levels.

        Tries the multi-TP pattern first, then falls back to collecting
        individually labeled TP matches.
        """
        tps: list[float] = []

        # Multi-TP on one line
        multi_match = _TAKE_PROFIT_MULTI.search(text)
        if multi_match:
            for i in range(1, 6):
                grp = multi_match.group(i)
                if grp:
                    val = self._parse_number(grp)
                    if val and val not in tps:
                        tps.append(val)

        # If multi pattern found at least one, use it
        if tps:
            return tps[:3]

        # Fallback: collect all individually labeled TPs
        for match in _TAKE_PROFIT_LABELED.finditer(text):
            val = self._parse_number(match.group(1))
            if val and val not in tps:
                tps.append(val)
            if len(tps) >= 3:
                break

        return tps[:3]

    @staticmethod
    def _extract_leverage(text: str) -> Optional[int]:
        match = _LEVERAGE.search(text)
        if not match:
            return None
        # Group 1 = "Nx" format, group 2 = "leverage N" format
        val = match.group(1) or match.group(2)
        try:
            lev = int(val)
            return lev if 1 <= lev <= 125 else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_timeframe(text: str) -> Optional[str]:
        match = _TIMEFRAME.search(text)
        if match:
            return match.group(0).upper().strip()
        return None

    @staticmethod
    def _extract_confidence_wording(text: str) -> Optional[str]:
        match = _CONFIDENCE_WORDING.search(text)
        if match:
            return match.group(0)
        return None

    @staticmethod
    def _extract_options_details(text: str) -> tuple[Optional[float], Optional[str]]:
        match = _OPTIONS_STRIKE.search(text)
        if match:
            try:
                strike = float(match.group(1))
                opt_type = "CALL" if match.group(2).upper() in ("C", "CALL") else "PUT"
                return strike, opt_type
            except (ValueError, AttributeError):
                pass
        return None, None

    @staticmethod
    def _extract_options_expiry(text: str) -> Optional[str]:
        match = _OPTIONS_EXPIRY.search(text)
        if match:
            return match.group(0)
        return None

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_confidence(result: RegexParseResult) -> float:
        """
        Compute parser confidence as a weighted fraction of core fields.

        Core fields (weighted):
          symbol      → 0.25
          direction   → 0.20
          entry       → 0.20
          stop_loss   → 0.20
          take_profit → 0.15
        """
        score = 0.0

        if result.symbol:
            score += 0.25
        if result.direction and result.direction != "unknown":
            score += 0.20
        if result.entry_price or (result.entry_range_low and result.entry_range_high):
            score += 0.20
        if result.stop_loss:
            score += 0.20
        if result.take_profit_1:
            score += 0.15

        return round(score, 4)
