"""
signal_parser/parser_router.py
--------------------------------
Orchestrates the two-stage parsing pipeline:

  Stage 1: Regex parser (fast, deterministic, zero cost)
  Stage 2: LLM fallback (only when regex confidence < threshold)

Also handles:
  - Database persistence of parsed signals
  - Marking raw messages as parse_attempted
  - Duplicate signal detection
  - Structured logging of every parse decision

Flow:
  parse_and_store(raw_text)
      │
      ▼
  RegexParser.parse(text) → RegexParseResult
      │
      ├── confidence >= threshold → normalize → store (regex method)
      │
      └── confidence < threshold
              │
              ▼
          LLMParser.parse(text) → LLMParseResult
              │
              ├── LLM succeeded → merge regex + LLM → normalize → store
              │
              └── LLM failed → store partial regex result (if any symbol found)
                              OR mark as unparseable
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    AssetTypeEnum,
    DirectionEnum,
    ParsedSignal,
    ParseMethodEnum,
    RawDiscordMessage,
    SignalSourceEnum,
)
from app.services.discord_ingestor.message_store import MessageStore
from app.services.signal_parser.regex_parser import RegexParser
from app.services.signal_parser.llm_parser import LLMParser
from app.services.signal_parser.normalizer import SignalNormalizer, NormalizedSignal

logger = logging.getLogger(__name__)
settings = get_settings()


class ParserRouter:
    """
    Two-stage signal parsing orchestrator.

    Instantiate once per application lifetime (lazy singletons for parsers)
    or create fresh per request — the parsers themselves are stateless.
    """

    def __init__(
        self,
        regex_parser: Optional[RegexParser] = None,
        llm_parser: Optional[LLMParser] = None,
        normalizer: Optional[SignalNormalizer] = None,
        message_store: Optional[MessageStore] = None,
    ) -> None:
        self._regex = regex_parser or RegexParser()
        self._llm = llm_parser or LLMParser()
        self._normalizer = normalizer or SignalNormalizer()
        self._message_store = message_store or MessageStore()

        self._confidence_threshold = settings.llm.fallback_confidence_threshold

    async def parse_and_store(
        self,
        db: AsyncSession,
        raw_message_id: uuid.UUID,
        raw_text: str,
        source: SignalSourceEnum = SignalSourceEnum.discord,
        signal_timestamp: Optional[datetime] = None,
        discord_author_id: Optional[str] = None,
        discord_author_name: Optional[str] = None,
        discord_message_link: Optional[str] = None,
    ) -> Optional[ParsedSignal]:
        """
        Full parse-and-store pipeline for a single message.

        Args:
            db:                  Async DB session (caller-owned transaction)
            raw_message_id:      UUID of the raw_discord_messages record
            raw_text:            The message content to parse
            source:              Origin of the signal
            signal_timestamp:    Message timestamp (defaults to now if None)
            discord_author_id:   Discord author snowflake ID
            discord_author_name: Discord author display name
            discord_message_link: Full Discord message URL

        Returns:
            The stored ParsedSignal record, or None if parsing failed entirely.
        """
        ts = signal_timestamp or datetime.now(tz=timezone.utc)
        parse_succeeded = False

        try:
            normalized = await self._run_pipeline(
                raw_text=raw_text,
                source=source,
                signal_timestamp=ts,
                raw_message_id=raw_message_id,
                discord_author_id=discord_author_id,
                discord_author_name=discord_author_name,
                discord_message_link=discord_message_link,
            )

            if normalized is None:
                logger.info(
                    "Message produced no parseable signal | raw_id=%s",
                    raw_message_id,
                )
                await self._message_store.mark_parse_attempted(
                    db, raw_message_id, succeeded=False
                )
                return None

            # Duplicate detection before inserting
            if await self._is_duplicate(db, normalized):
                logger.info(
                    "Duplicate signal detected | symbol=%s | skip insert",
                    normalized.symbol,
                )
                await self._message_store.mark_parse_attempted(
                    db, raw_message_id, succeeded=True
                )
                return None

            # Persist
            record = await self._store_signal(db, normalized)
            parse_succeeded = True

            logger.info(
                "Signal stored | id=%s | symbol=%s | direction=%s | "
                "method=%s | completeness=%d%%",
                record.id,
                record.symbol,
                record.direction,
                record.parse_method,
                record.signal_completeness_pct or 0,
            )

            return record

        except Exception as exc:
            logger.exception(
                "ParserRouter error for raw_id=%s: %s", raw_message_id, exc
            )
            parse_succeeded = False
            return None

        finally:
            # Always mark the raw message regardless of outcome
            try:
                await self._message_store.mark_parse_attempted(
                    db, raw_message_id, succeeded=parse_succeeded
                )
            except Exception:
                logger.warning(
                    "Failed to mark parse_attempted for raw_id=%s", raw_message_id
                )

    async def _run_pipeline(
        self,
        raw_text: str,
        source: SignalSourceEnum,
        signal_timestamp: datetime,
        raw_message_id: uuid.UUID,
        discord_author_id: Optional[str],
        discord_author_name: Optional[str],
        discord_message_link: Optional[str],
    ) -> Optional[NormalizedSignal]:
        """
        Execute the two-stage parsing pipeline.
        Returns a NormalizedSignal or None if nothing useful was extracted.
        """
        # ----------------------------------------------------------------
        # Stage 1: Regex parser
        # ----------------------------------------------------------------
        regex_result = self._regex.parse(raw_text)

        logger.debug(
            "Stage 1 (regex) | symbol=%s | confidence=%.2f | "
            "entry=%s | sl=%s | tp1=%s",
            regex_result.symbol,
            regex_result.confidence,
            regex_result.entry_price or regex_result.entry_range_low,
            regex_result.stop_loss,
            regex_result.take_profit_1,
        )

        # ----------------------------------------------------------------
        # Decision: is regex sufficient?
        # ----------------------------------------------------------------
        if regex_result.confidence >= self._confidence_threshold:
            logger.debug(
                "Stage 1 sufficient | confidence=%.2f >= threshold=%.2f",
                regex_result.confidence,
                self._confidence_threshold,
            )
            return self._normalizer.normalize_from_regex(
                regex_result=regex_result,
                source=source,
                signal_timestamp=signal_timestamp,
                raw_text=raw_text,
                raw_message_id=raw_message_id,
                discord_author_id=discord_author_id,
                discord_author_name=discord_author_name,
                discord_message_link=discord_message_link,
            )

        # ----------------------------------------------------------------
        # Stage 2: LLM fallback
        # ----------------------------------------------------------------
        logger.info(
            "Stage 1 confidence=%.2f < threshold=%.2f — triggering LLM fallback",
            regex_result.confidence,
            self._confidence_threshold,
        )

        llm_result = await self._llm.parse(raw_text)

        if llm_result.error:
            logger.warning(
                "LLM fallback failed: %s — using partial regex result",
                llm_result.error,
            )
            # Use partial regex result if symbol was found
            if regex_result.symbol:
                return self._normalizer.normalize_from_regex(
                    regex_result=regex_result,
                    source=source,
                    signal_timestamp=signal_timestamp,
                    raw_text=raw_text,
                    raw_message_id=raw_message_id,
                    discord_author_id=discord_author_id,
                    discord_author_name=discord_author_name,
                    discord_message_link=discord_message_link,
                )
            return None

        logger.debug(
            "Stage 2 (LLM) | symbol=%s | confidence=%.2f | model=%s",
            llm_result.symbol,
            llm_result.confidence,
            llm_result.model_used,
        )

        # LLM also returned nothing useful
        if not llm_result.symbol and llm_result.confidence < 0.1:
            logger.info("LLM also found no signal | skipping storage")
            return None

        # Merge regex and LLM results (LLM fills gaps regex missed)
        return self._normalizer.merge_regex_with_llm(
            regex_result=regex_result,
            llm_result=llm_result,
            source=source,
            signal_timestamp=signal_timestamp,
            raw_text=raw_text,
            raw_message_id=raw_message_id,
            discord_author_id=discord_author_id,
            discord_author_name=discord_author_name,
            discord_message_link=discord_message_link,
        )

    async def _is_duplicate(
        self,
        db: AsyncSession,
        signal: NormalizedSignal,
    ) -> bool:
        """
        Basic duplicate detection.

        A signal is considered a duplicate if an identical symbol + direction
        + entry_price combination was already stored within the last 30 minutes
        from the same author.

        This is a Phase 1 implementation. Phase 2 will add fuzzy matching
        using pg_trgm on raw_text.
        """
        from datetime import timedelta
        from sqlalchemy import and_, func

        window_start = signal.signal_timestamp - timedelta(minutes=30)

        conditions = [
            ParsedSignal.symbol == signal.symbol,
            ParsedSignal.direction == signal.direction,
            ParsedSignal.signal_timestamp >= window_start,
            ParsedSignal.is_duplicate == False,  # noqa: E712
        ]

        # Match on entry price if both have one
        if signal.entry_price:
            conditions.append(ParsedSignal.entry_price == signal.entry_price)

        # Match on author if known
        if signal.discord_author_id:
            conditions.append(
                ParsedSignal.discord_author_id == signal.discord_author_id
            )

        result = await db.execute(
            select(ParsedSignal.id)
            .where(and_(*conditions))
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def _store_signal(
        self,
        db: AsyncSession,
        signal: NormalizedSignal,
    ) -> ParsedSignal:
        """Insert a NormalizedSignal into the parsed_signals table."""

        record = ParsedSignal(
            id=uuid.uuid4(),
            raw_message_id=signal.raw_message_id,
            source=signal.source,
            parse_method=signal.parse_method,
            symbol=signal.symbol,
            asset_type=signal.asset_type,
            exchange=signal.exchange,
            direction=signal.direction,
            entry_price=signal.entry_price,
            entry_range_low=signal.entry_range_low,
            entry_range_high=signal.entry_range_high,
            stop_loss=signal.stop_loss,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            take_profit_3=signal.take_profit_3,
            timeframe=signal.timeframe,
            leverage=signal.leverage,
            confidence_wording=signal.confidence_wording,
            options_strike=signal.options_strike,
            options_type=signal.options_type,
            discord_author_id=signal.discord_author_id,
            discord_author_name=signal.discord_author_name,
            discord_message_link=signal.discord_message_link,
            risk_reward_ratio=signal.risk_reward_ratio,
            signal_completeness_pct=signal.signal_completeness_pct,
            is_duplicate=False,
            is_actionable=signal.is_actionable,
            signal_timestamp=signal.signal_timestamp,
            llm_model_used=signal.llm_model_used,
            llm_confidence=signal.llm_confidence,
            llm_raw_output=signal.llm_raw_output,
            raw_text=signal.raw_text,
        )

        db.add(record)
        await db.flush()  # Get the ID without full commit (caller commits)
        return record


# ---------------------------------------------------------------------------
# Convenience function for use outside FastAPI (e.g. CLI, Celery tasks)
# ---------------------------------------------------------------------------

async def parse_message(
    raw_text: str,
    raw_message_id: uuid.UUID,
    signal_timestamp: Optional[datetime] = None,
    source: SignalSourceEnum = SignalSourceEnum.discord,
    discord_author_id: Optional[str] = None,
    discord_author_name: Optional[str] = None,
    discord_message_link: Optional[str] = None,
) -> Optional[ParsedSignal]:
    """
    Standalone convenience wrapper for parsing a single message.
    Opens its own DB session.
    """
    from app.database import get_db_context

    router = ParserRouter()

    async with get_db_context() as db:
        return await router.parse_and_store(
            db=db,
            raw_message_id=raw_message_id,
            raw_text=raw_text,
            source=source,
            signal_timestamp=signal_timestamp,
            discord_author_id=discord_author_id,
            discord_author_name=discord_author_name,
            discord_message_link=discord_message_link,
        )
