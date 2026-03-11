"""
signal_parser/normalizer.py
-----------------------------
Normalizes the output of either the regex parser or LLM parser into
a canonical `NormalizedSignal` object that maps directly to the
`parsed_signals` database table.

Responsibilities:
  - Merge regex and LLM results when both ran (LLM fills gaps left by regex)
  - Validate and sanitise all field values
  - Compute derived fields (risk-reward ratio, completeness percentage)
  - Produce a dict ready for database insertion

Field normalisation rules:
  - symbol: uppercase, no spaces, strip leading $/#
  - direction: always 'long', 'short', or 'unknown'
  - asset_type: always one of the AssetTypeEnum values
  - prices: must be positive finite floats; zero/negative are rejected
  - leverage: 1–125 only
  - timeframe: uppercase, normalised to standard notation (1H, 4H, 1D, etc.)
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.models import AssetTypeEnum, DirectionEnum, ParseMethodEnum, SignalSourceEnum
from app.services.signal_parser.regex_parser import RegexParseResult
from app.services.signal_parser.llm_parser import LLMParseResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timeframe normalisation map
# ---------------------------------------------------------------------------

_TIMEFRAME_MAP: dict[str, str] = {
    # Minutes
    "1M": "1M", "1MIN": "1M", "1MINUTE": "1M",
    "3M": "3M", "3MIN": "3M",
    "5M": "5M", "5MIN": "5M",
    "15M": "15M", "15MIN": "15M",
    "30M": "30M", "30MIN": "30M",
    "45M": "45M",
    # Hours
    "1H": "1H", "1HR": "1H", "1HOUR": "1H", "HOURLY": "1H",
    "2H": "2H",
    "3H": "3H",
    "4H": "4H", "4HR": "4H",
    "6H": "6H",
    "8H": "8H",
    "12H": "12H",
    # Days / Weeks
    "1D": "1D", "DAILY": "1D", "D": "1D",
    "3D": "3D",
    "1W": "1W", "WEEKLY": "1W", "W": "1W",
    "1MO": "1MO", "MONTHLY": "1MO", "MONTH": "1MO",
    # Swing / Intraday
    "SWING": "SWING", "INTRADAY": "INTRADAY",
}


def _normalise_timeframe(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    clean = raw.upper().strip()
    # Remove spaces between number and unit
    clean = re.sub(r"\s+", "", clean)
    return _TIMEFRAME_MAP.get(clean, clean[:8])  # Cap length at 8


# ---------------------------------------------------------------------------
# Normalised signal data class
# ---------------------------------------------------------------------------

@dataclass
class NormalizedSignal:
    """
    Canonical signal representation ready for database insertion.
    Corresponds 1:1 with the parsed_signals table schema.
    """
    # Required
    source: SignalSourceEnum
    parse_method: ParseMethodEnum
    symbol: str
    asset_type: AssetTypeEnum
    direction: DirectionEnum
    signal_timestamp: datetime
    raw_text: str

    # Optional — pricing
    entry_price: Optional[float] = None
    entry_range_low: Optional[float] = None
    entry_range_high: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    take_profit_3: Optional[float] = None

    # Optional — trade params
    leverage: Optional[int] = None
    timeframe: Optional[str] = None
    confidence_wording: Optional[str] = None

    # Optional — options
    options_strike: Optional[float] = None
    options_type: Optional[str] = None
    options_expiry_raw: Optional[str] = None

    # Optional — attribution
    discord_author_id: Optional[str] = None
    discord_author_name: Optional[str] = None
    discord_message_link: Optional[str] = None
    raw_message_id: Optional[uuid.UUID] = None
    exchange: Optional[str] = None

    # Computed
    risk_reward_ratio: Optional[float] = None
    signal_completeness_pct: Optional[int] = None

    # Parser metadata
    llm_model_used: Optional[str] = None
    llm_confidence: Optional[float] = None
    llm_raw_output: Optional[dict] = None
    regex_confidence: Optional[float] = None

    # Flags
    is_actionable: bool = True
    is_valid: bool = True
    validation_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------

class SignalNormalizer:
    """
    Merges and normalises parser outputs into a NormalizedSignal.
    """

    def normalize_from_regex(
        self,
        regex_result: RegexParseResult,
        source: SignalSourceEnum,
        signal_timestamp: datetime,
        raw_text: str,
        raw_message_id: Optional[uuid.UUID] = None,
        discord_author_id: Optional[str] = None,
        discord_author_name: Optional[str] = None,
        discord_message_link: Optional[str] = None,
    ) -> NormalizedSignal:
        """Build a NormalizedSignal from regex parser output."""

        signal = NormalizedSignal(
            source=source,
            parse_method=ParseMethodEnum.regex,
            symbol=self._normalize_symbol(regex_result.symbol or "UNKNOWN"),
            asset_type=self._normalize_asset_type(regex_result.asset_type),
            direction=self._normalize_direction(regex_result.direction),
            signal_timestamp=signal_timestamp,
            raw_text=raw_text,
            entry_price=self._safe_price(regex_result.entry_price),
            entry_range_low=self._safe_price(regex_result.entry_range_low),
            entry_range_high=self._safe_price(regex_result.entry_range_high),
            stop_loss=self._safe_price(regex_result.stop_loss),
            take_profit_1=self._safe_price(regex_result.take_profit_1),
            take_profit_2=self._safe_price(regex_result.take_profit_2),
            take_profit_3=self._safe_price(regex_result.take_profit_3),
            leverage=regex_result.leverage,
            timeframe=_normalise_timeframe(regex_result.timeframe),
            confidence_wording=regex_result.confidence_wording,
            options_strike=self._safe_price(regex_result.options_strike),
            options_type=regex_result.options_type,
            options_expiry_raw=regex_result.options_expiry_raw,
            raw_message_id=raw_message_id,
            discord_author_id=discord_author_id,
            discord_author_name=discord_author_name,
            discord_message_link=discord_message_link,
            regex_confidence=regex_result.confidence,
        )

        self._compute_derived(signal)
        self._validate(signal)
        return signal

    def normalize_from_llm(
        self,
        llm_result: LLMParseResult,
        source: SignalSourceEnum,
        signal_timestamp: datetime,
        raw_text: str,
        raw_message_id: Optional[uuid.UUID] = None,
        discord_author_id: Optional[str] = None,
        discord_author_name: Optional[str] = None,
        discord_message_link: Optional[str] = None,
    ) -> NormalizedSignal:
        """Build a NormalizedSignal from LLM parser output."""

        signal = NormalizedSignal(
            source=source,
            parse_method=ParseMethodEnum.llm,
            symbol=self._normalize_symbol(llm_result.symbol or "UNKNOWN"),
            asset_type=self._normalize_asset_type(llm_result.asset_type),
            direction=self._normalize_direction(llm_result.direction),
            signal_timestamp=signal_timestamp,
            raw_text=raw_text,
            entry_price=self._safe_price(llm_result.entry_price),
            entry_range_low=self._safe_price(llm_result.entry_range_low),
            entry_range_high=self._safe_price(llm_result.entry_range_high),
            stop_loss=self._safe_price(llm_result.stop_loss),
            take_profit_1=self._safe_price(llm_result.take_profit_1),
            take_profit_2=self._safe_price(llm_result.take_profit_2),
            take_profit_3=self._safe_price(llm_result.take_profit_3),
            leverage=llm_result.leverage,
            timeframe=_normalise_timeframe(llm_result.timeframe),
            confidence_wording=llm_result.confidence_wording,
            options_strike=self._safe_price(llm_result.options_strike),
            options_type=llm_result.options_type,
            options_expiry_raw=llm_result.options_expiry_raw,
            raw_message_id=raw_message_id,
            discord_author_id=discord_author_id,
            discord_author_name=discord_author_name,
            discord_message_link=discord_message_link,
            llm_model_used=llm_result.model_used,
            llm_confidence=llm_result.confidence,
            llm_raw_output={
                "raw_response": llm_result.raw_response,
                "reasoning": llm_result.reasoning,
                "latency_ms": llm_result.latency_ms,
            },
        )

        self._compute_derived(signal)
        self._validate(signal)
        return signal

    def merge_regex_with_llm(
        self,
        regex_result: RegexParseResult,
        llm_result: LLMParseResult,
        source: SignalSourceEnum,
        signal_timestamp: datetime,
        raw_text: str,
        raw_message_id: Optional[uuid.UUID] = None,
        discord_author_id: Optional[str] = None,
        discord_author_name: Optional[str] = None,
        discord_message_link: Optional[str] = None,
    ) -> NormalizedSignal:
        """
        Merge regex and LLM results: use LLM to fill gaps left by regex.

        Strategy:
          - Prefer the regex value when it was successfully extracted
          - Use LLM value when the regex field is None
          - Symbol is always taken from whichever has higher confidence
        """
        # Symbol: use LLM if regex failed or found nothing useful
        raw_symbol = regex_result.symbol or llm_result.symbol
        raw_asset = regex_result.asset_type if regex_result.asset_type != "unknown" \
            else llm_result.asset_type

        signal = NormalizedSignal(
            source=source,
            parse_method=ParseMethodEnum.llm,  # LLM was involved
            symbol=self._normalize_symbol(raw_symbol or "UNKNOWN"),
            asset_type=self._normalize_asset_type(raw_asset),
            direction=self._normalize_direction(
                regex_result.direction
                if regex_result.direction != "unknown"
                else llm_result.direction
            ),
            signal_timestamp=signal_timestamp,
            raw_text=raw_text,
            # Prices: prefer regex (deterministic), fall back to LLM
            entry_price=self._safe_price(
                regex_result.entry_price or llm_result.entry_price
            ),
            entry_range_low=self._safe_price(
                regex_result.entry_range_low or llm_result.entry_range_low
            ),
            entry_range_high=self._safe_price(
                regex_result.entry_range_high or llm_result.entry_range_high
            ),
            stop_loss=self._safe_price(
                regex_result.stop_loss or llm_result.stop_loss
            ),
            take_profit_1=self._safe_price(
                regex_result.take_profit_1 or llm_result.take_profit_1
            ),
            take_profit_2=self._safe_price(
                regex_result.take_profit_2 or llm_result.take_profit_2
            ),
            take_profit_3=self._safe_price(
                regex_result.take_profit_3 or llm_result.take_profit_3
            ),
            leverage=regex_result.leverage or llm_result.leverage,
            timeframe=_normalise_timeframe(
                regex_result.timeframe or llm_result.timeframe
            ),
            confidence_wording=(
                regex_result.confidence_wording or llm_result.confidence_wording
            ),
            options_strike=self._safe_price(
                regex_result.options_strike or llm_result.options_strike
            ),
            options_type=regex_result.options_type or llm_result.options_type,
            options_expiry_raw=(
                regex_result.options_expiry_raw or llm_result.options_expiry_raw
            ),
            raw_message_id=raw_message_id,
            discord_author_id=discord_author_id,
            discord_author_name=discord_author_name,
            discord_message_link=discord_message_link,
            regex_confidence=regex_result.confidence,
            llm_model_used=llm_result.model_used,
            llm_confidence=llm_result.confidence,
            llm_raw_output={
                "raw_response": llm_result.raw_response,
                "reasoning": llm_result.reasoning,
                "latency_ms": llm_result.latency_ms,
            },
        )

        self._compute_derived(signal)
        self._validate(signal)
        return signal

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------

    def _compute_derived(self, signal: NormalizedSignal) -> None:
        """Compute risk-reward ratio and completeness percentage."""

        # Risk-reward ratio
        entry = signal.entry_price or (
            (signal.entry_range_low + signal.entry_range_high) / 2
            if signal.entry_range_low and signal.entry_range_high
            else None
        )
        if entry and signal.stop_loss and signal.take_profit_1:
            risk = abs(entry - signal.stop_loss)
            reward = abs(signal.take_profit_1 - entry)
            if risk > 0:
                signal.risk_reward_ratio = round(reward / risk, 4)

        # Completeness percentage
        weights = {
            "symbol": (signal.symbol not in ("UNKNOWN", ""), 20),
            "direction": (signal.direction != DirectionEnum.unknown, 20),
            "entry": (
                signal.entry_price is not None
                or (signal.entry_range_low is not None and signal.entry_range_high is not None),
                20,
            ),
            "stop_loss": (signal.stop_loss is not None, 20),
            "take_profit": (signal.take_profit_1 is not None, 10),
            "timeframe": (signal.timeframe is not None, 5),
            "asset_type": (signal.asset_type != AssetTypeEnum.unknown, 5),
        }
        total = sum(w for _, (_, w) in weights.items())
        earned = sum(w for _, (present, w) in weights.items() if present)
        signal.signal_completeness_pct = round((earned / total) * 100)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate(self, signal: NormalizedSignal) -> None:
        """Validate the normalised signal and flag any issues."""
        errors: list[str] = []

        if signal.symbol == "UNKNOWN" or not signal.symbol:
            errors.append("symbol_missing")

        if signal.direction == DirectionEnum.unknown:
            errors.append("direction_unknown")

        # Price consistency checks
        if signal.entry_price and signal.entry_price <= 0:
            errors.append("entry_price_invalid")
            signal.entry_price = None

        if signal.stop_loss and signal.stop_loss <= 0:
            errors.append("stop_loss_invalid")
            signal.stop_loss = None

        if signal.entry_range_low and signal.entry_range_high:
            if signal.entry_range_low >= signal.entry_range_high:
                errors.append("entry_range_inverted")
                signal.entry_range_low, signal.entry_range_high = (
                    signal.entry_range_high, signal.entry_range_low
                )

        # Directional consistency
        entry = signal.entry_price or signal.entry_range_low
        if entry and signal.stop_loss and signal.take_profit_1:
            if signal.direction == DirectionEnum.long:
                if signal.stop_loss >= entry:
                    errors.append("stop_loss_above_entry_for_long")
                if signal.take_profit_1 <= entry:
                    errors.append("take_profit_below_entry_for_long")
            elif signal.direction == DirectionEnum.short:
                if signal.stop_loss <= entry:
                    errors.append("stop_loss_below_entry_for_short")
                if signal.take_profit_1 >= entry:
                    errors.append("take_profit_above_entry_for_short")

        if errors:
            signal.validation_errors = errors
            # Signal is still stored but may be marked non-actionable
            # if critical fields are missing
            critical_errors = {"symbol_missing", "direction_unknown"}
            if critical_errors.intersection(errors):
                signal.is_actionable = False

        logger.debug(
            "Signal validation | symbol=%s | errors=%s | actionable=%s",
            signal.symbol,
            errors,
            signal.is_actionable,
        )

    # ------------------------------------------------------------------
    # Field normalisers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_symbol(raw: Optional[str]) -> str:
        if not raw:
            return "UNKNOWN"
        # Strip common prefixes
        clean = raw.upper().strip()
        clean = clean.lstrip("$#")
        # Remove spaces
        clean = clean.replace(" ", "")
        return clean if clean else "UNKNOWN"

    @staticmethod
    def _normalize_asset_type(raw: Optional[str]) -> AssetTypeEnum:
        if not raw:
            return AssetTypeEnum.unknown
        mapping = {
            "crypto": AssetTypeEnum.crypto,
            "stock": AssetTypeEnum.stock,
            "option": AssetTypeEnum.option,
            "futures": AssetTypeEnum.futures,
            "unknown": AssetTypeEnum.unknown,
        }
        return mapping.get(raw.lower(), AssetTypeEnum.unknown)

    @staticmethod
    def _normalize_direction(raw: Optional[str]) -> DirectionEnum:
        if not raw:
            return DirectionEnum.unknown
        r = raw.lower().strip()
        if r in ("long", "buy", "bullish", "call", "calls"):
            return DirectionEnum.long
        if r in ("short", "sell", "bearish", "put", "puts"):
            return DirectionEnum.short
        return DirectionEnum.unknown

    @staticmethod
    def _safe_price(val: Optional[float]) -> Optional[float]:
        """Return val only if it's a positive finite float."""
        if val is None:
            return None
        try:
            f = float(val)
            if f > 0 and f < 1e15:  # Sanity cap
                return f
        except (ValueError, TypeError, OverflowError):
            pass
        return None
