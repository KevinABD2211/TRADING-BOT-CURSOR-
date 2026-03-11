"""
signal_parser/llm_parser.py
-----------------------------
Stage 2: LLM fallback parser.

Invoked when the regex parser confidence falls below the configured
threshold. Sends the raw message to a language model and requests a
structured JSON extraction of all signal fields.

Architecture:
  - LLMParser is a thin orchestrator
  - BaseLLMProvider defines the adapter interface
  - OpenAIProvider and AnthropicProvider are concrete implementations
  - The active provider is selected via the LLM_PROVIDER env var

The LLM is prompted to act as a financial signal extraction system
and return ONLY valid JSON — no prose, no markdown fences.

All LLM interactions are logged for audit purposes.
Failures return a result with confidence=0.0 rather than raising,
so the pipeline degrades gracefully.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import get_settings
from app.utils.retry import with_retry

logger = logging.getLogger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SIGNAL_EXTRACTION_SYSTEM_PROMPT = """
You are a financial trading signal extraction system.

Your sole job is to extract structured data from trading signal messages.
You must respond ONLY with a valid JSON object — no prose, no markdown fences,
no explanation before or after the JSON.

The JSON must conform exactly to this schema:

{
  "symbol": "string | null",
  "asset_type": "crypto | stock | option | futures | unknown",
  "direction": "long | short | unknown",
  "entry_price": number | null,
  "entry_range_low": number | null,
  "entry_range_high": number | null,
  "stop_loss": number | null,
  "take_profit_1": number | null,
  "take_profit_2": number | null,
  "take_profit_3": number | null,
  "leverage": integer | null,
  "timeframe": "string | null",
  "confidence_wording": "string | null",
  "options_strike": number | null,
  "options_type": "CALL | PUT | null",
  "options_expiry_raw": "string | null",
  "confidence": number,
  "reasoning": "string"
}

Rules:
1. symbol: uppercase ticker only (e.g. "BTCUSDT", "AAPL", "ETHUSDT")
   - For crypto pairs always append USDT if quote currency is unclear
   - Never include spaces in symbol
2. direction: normalise all buy/long/bullish variants → "long",
              all sell/short/bearish variants → "short"
3. All price values must be plain numbers (no currency symbols, no commas)
4. confidence: your own confidence in the extraction from 0.0 to 1.0
5. reasoning: brief single sentence explaining your confidence level
6. If the message is NOT a trading signal, set confidence to 0.0 and
   leave all other fields null
7. If you see a price range for entry, populate entry_range_low and
   entry_range_high, leave entry_price null
8. timeframe: normalise to formats like "1H", "4H", "1D", "15M", "SWING", "DAILY"
""".strip()


# ---------------------------------------------------------------------------
# Result data class
# ---------------------------------------------------------------------------

@dataclass
class LLMParseResult:
    """
    Structured output from the LLM parser.
    Mirrors RegexParseResult but includes LLM-specific metadata.
    """
    symbol: Optional[str] = None
    asset_type: Optional[str] = None
    direction: Optional[str] = None
    entry_price: Optional[float] = None
    entry_range_low: Optional[float] = None
    entry_range_high: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    take_profit_3: Optional[float] = None
    leverage: Optional[int] = None
    timeframe: Optional[str] = None
    confidence_wording: Optional[str] = None
    options_strike: Optional[float] = None
    options_type: Optional[str] = None
    options_expiry_raw: Optional[str] = None
    # Metadata
    confidence: float = 0.0
    reasoning: str = ""
    model_used: str = ""
    raw_response: str = ""
    latency_ms: int = 0
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.symbol is not None


# ---------------------------------------------------------------------------
# Provider interface
# ---------------------------------------------------------------------------

class BaseLLMProvider(ABC):
    """
    Abstract base for LLM provider adapters.

    Each provider must implement `complete(prompt: str) -> str`
    which returns the raw text response from the model.
    """

    @abstractmethod
    async def complete(self, user_message: str) -> str:
        """Send user_message to the LLM and return the text response."""
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model identifier string."""
        ...


# ---------------------------------------------------------------------------
# OpenAI provider
# ---------------------------------------------------------------------------

class OpenAIProvider(BaseLLMProvider):
    """
    OpenAI API adapter using the official openai Python SDK.
    Requires: OPENAI_API_KEY environment variable.
    Recommended model: gpt-4o-mini (fast, cheap, accurate for structured extraction).
    """

    def __init__(self) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise ImportError(
                "openai package is not installed. Run: pip install openai"
            ) from exc

        if not settings.llm.openai_api_key:
            raise ValueError(
                "OPENAI_API_KEY environment variable is not set. "
                "Configure it in your .env file."
            )

        self._client = AsyncOpenAI(
            api_key=settings.llm.openai_api_key,
            timeout=settings.llm.request_timeout_seconds,
        )
        self._model = settings.llm.model_name or "gpt-4o-mini"

    @property
    def model_name(self) -> str:
        return self._model

    @with_retry(max_attempts=3, base_delay=2.0, exceptions=(Exception,))
    async def complete(self, user_message: str) -> str:
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": SIGNAL_EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=settings.llm.temperature,
            max_tokens=settings.llm.max_tokens,
            response_format={"type": "json_object"},  # JSON mode
        )
        return response.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------

class AnthropicProvider(BaseLLMProvider):
    """
    Anthropic Claude adapter using the official anthropic Python SDK.
    Requires: ANTHROPIC_API_KEY environment variable.
    Recommended model: claude-haiku-3 (fast and cost-effective).
    """

    def __init__(self) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise ImportError(
                "anthropic package is not installed. Run: pip install anthropic"
            ) from exc

        if not settings.llm.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable is not set. "
                "Configure it in your .env file."
            )

        self._client = anthropic.AsyncAnthropic(
            api_key=settings.llm.anthropic_api_key,
        )
        self._model = settings.llm.model_name or "claude-haiku-4-5-20251001"

    @property
    def model_name(self) -> str:
        return self._model

    @with_retry(max_attempts=3, base_delay=2.0, exceptions=(Exception,))
    async def complete(self, user_message: str) -> str:
        import anthropic

        message = await self._client.messages.create(
            model=self._model,
            max_tokens=settings.llm.max_tokens,
            system=(
                SIGNAL_EXTRACTION_SYSTEM_PROMPT
                + "\n\nIMPORTANT: Respond ONLY with the raw JSON object. "
                "No markdown code blocks. No text before or after the JSON."
            ),
            messages=[
                {"role": "user", "content": user_message},
            ],
        )
        return message.content[0].text if message.content else ""


# ---------------------------------------------------------------------------
# No-op disabled provider
# ---------------------------------------------------------------------------

class DisabledProvider(BaseLLMProvider):
    """Placeholder when LLM fallback is disabled in configuration."""

    @property
    def model_name(self) -> str:
        return "disabled"

    async def complete(self, user_message: str) -> str:
        raise RuntimeError(
            "LLM provider is disabled (LLM_PROVIDER=disabled). "
            "Set LLM_PROVIDER=openai or LLM_PROVIDER=anthropic to enable."
        )


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def build_provider() -> BaseLLMProvider:
    """
    Instantiate the configured LLM provider.
    Called once and cached by LLMParser.
    """
    provider_name = settings.llm.provider

    if provider_name == "openai":
        logger.info("LLM provider: OpenAI | model=%s", settings.llm.model_name)
        return OpenAIProvider()

    if provider_name == "anthropic":
        logger.info("LLM provider: Anthropic | model=%s", settings.llm.model_name)
        return AnthropicProvider()

    if provider_name == "disabled":
        logger.warning("LLM fallback parser is DISABLED")
        return DisabledProvider()

    raise ValueError(
        f"Unknown LLM_PROVIDER: '{provider_name}'. "
        "Valid options: openai, anthropic, disabled"
    )


# ---------------------------------------------------------------------------
# LLM parser
# ---------------------------------------------------------------------------

class LLMParser:
    """
    Stage 2 fallback parser.

    Sends the raw message to the configured LLM provider and parses
    the JSON response into a structured LLMParseResult.

    Usage:
        parser = LLMParser()
        result = await parser.parse("messy signal text here")
    """

    def __init__(self, provider: Optional[BaseLLMProvider] = None) -> None:
        self._provider = provider or build_provider()

    @property
    def model_name(self) -> str:
        return self._provider.model_name

    async def parse(self, text: str) -> LLMParseResult:
        """
        Send text to LLM and extract structured signal data.

        Returns an LLMParseResult with error set if parsing failed.
        Never raises — all errors are captured in the result.
        """
        if not text or not text.strip():
            return LLMParseResult(
                error="Empty input text",
                model_used=self.model_name,
            )

        start_ms = int(time.monotonic() * 1000)

        try:
            raw_response = await self._provider.complete(text)
        except Exception as exc:
            latency = int(time.monotonic() * 1000) - start_ms
            logger.error("LLM API call failed: %s", exc)
            return LLMParseResult(
                error=f"LLM API error: {exc}",
                model_used=self.model_name,
                latency_ms=latency,
            )

        latency = int(time.monotonic() * 1000) - start_ms

        logger.debug(
            "LLM response received | model=%s | latency=%dms | len=%d",
            self.model_name,
            latency,
            len(raw_response),
        )

        return self._parse_response(raw_response, latency)

    def _parse_response(self, raw_response: str, latency_ms: int) -> LLMParseResult:
        """
        Parse the raw LLM text response into an LLMParseResult.

        Handles common LLM response issues:
          - Markdown code fences (```json ... ```)
          - Leading/trailing whitespace
          - Non-JSON preamble text
        """
        # Strip markdown code fences if present
        cleaned = raw_response.strip()
        if cleaned.startswith("```"):
            # Remove opening fence (```json or ```)
            cleaned = re.sub(r"^```[a-z]*\n?", "", cleaned, flags=re.IGNORECASE)
            # Remove closing fence
            cleaned = re.sub(r"\n?```$", "", cleaned)
            cleaned = cleaned.strip()

        # Find JSON object boundaries in case there's surrounding text
        json_start = cleaned.find("{")
        json_end = cleaned.rfind("}")
        if json_start == -1 or json_end == -1:
            logger.warning("LLM response contains no JSON object")
            return LLMParseResult(
                error="LLM response did not contain a JSON object",
                model_used=self.model_name,
                raw_response=raw_response[:500],
                latency_ms=latency_ms,
            )

        json_str = cleaned[json_start : json_end + 1]

        try:
            data: dict[str, Any] = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse LLM JSON response: %s", exc)
            return LLMParseResult(
                error=f"JSON parse error: {exc}",
                model_used=self.model_name,
                raw_response=raw_response[:500],
                latency_ms=latency_ms,
            )

        return self._map_to_result(data, raw_response, latency_ms)

    def _map_to_result(
        self,
        data: dict[str, Any],
        raw_response: str,
        latency_ms: int,
    ) -> LLMParseResult:
        """Map the parsed JSON dict to an LLMParseResult."""
        result = LLMParseResult(
            model_used=self.model_name,
            raw_response=raw_response[:1000],  # Truncate for storage
            latency_ms=latency_ms,
        )

        # --- Safe field extraction with type coercion ---
        result.symbol = self._safe_str(data.get("symbol"))
        result.asset_type = self._validate_enum(
            data.get("asset_type"), ["crypto", "stock", "option", "futures", "unknown"], "unknown"
        )
        result.direction = self._validate_enum(
            data.get("direction"), ["long", "short", "unknown"], "unknown"
        )

        result.entry_price = self._safe_float(data.get("entry_price"))
        result.entry_range_low = self._safe_float(data.get("entry_range_low"))
        result.entry_range_high = self._safe_float(data.get("entry_range_high"))
        result.stop_loss = self._safe_float(data.get("stop_loss"))
        result.take_profit_1 = self._safe_float(data.get("take_profit_1"))
        result.take_profit_2 = self._safe_float(data.get("take_profit_2"))
        result.take_profit_3 = self._safe_float(data.get("take_profit_3"))

        result.leverage = self._safe_int(data.get("leverage"))
        if result.leverage and not (1 <= result.leverage <= 125):
            result.leverage = None  # Reject invalid leverage values

        result.timeframe = self._safe_str(data.get("timeframe"))
        result.confidence_wording = self._safe_str(data.get("confidence_wording"))

        result.options_strike = self._safe_float(data.get("options_strike"))
        result.options_type = self._validate_enum(
            data.get("options_type"), ["CALL", "PUT", None], None
        )
        result.options_expiry_raw = self._safe_str(data.get("options_expiry_raw"))

        result.confidence = max(0.0, min(1.0, float(data.get("confidence", 0.0))))
        result.reasoning = self._safe_str(data.get("reasoning")) or ""

        logger.info(
            "LLM parse complete | symbol=%s | direction=%s | confidence=%.2f | model=%s",
            result.symbol,
            result.direction,
            result.confidence,
            self.model_name,
        )

        return result

    # ------------------------------------------------------------------
    # Type-safe helper methods
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_str(val: Any) -> Optional[str]:
        if val is None or val == "null":
            return None
        s = str(val).strip()
        return s if s else None

    @staticmethod
    def _safe_float(val: Any) -> Optional[float]:
        if val is None:
            return None
        try:
            f = float(val)
            return f if f > 0 else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_int(val: Any) -> Optional[int]:
        if val is None:
            return None
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _validate_enum(val: Any, allowed: list, default: Any) -> Any:
        if val in allowed:
            return val
        return default


# ---------------------------------------------------------------------------
# Import guard for re module (used in _parse_response)
# ---------------------------------------------------------------------------
import re  # noqa: E402 — must be at module scope for _parse_response
