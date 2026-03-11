"""
discord_ingestor/historical_fetcher.py
---------------------------------------
Fetches historical messages from a Discord channel.

Used on bot startup to ingest messages that arrived while the bot was
offline. Also callable manually via the `!ta backfill` admin command.

Rate limiting:
  Discord limits channel history requests to 200 messages per call.
  We respect the configurable delay between batches to avoid hitting
  Discord's rate limits (50 requests/second globally, but we stay well
  below that by fetching in batches with sleep intervals).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import discord

from app.config import get_settings
from app.database import get_db_context
from app.services.discord_ingestor.message_store import MessageStore
from app.services.signal_detector import SignalDetector

if TYPE_CHECKING:
    from app.services.discord_ingestor.bot import TradingBot

logger = logging.getLogger(__name__)
settings = get_settings()

# Discord's maximum messages per history request
DISCORD_HISTORY_BATCH_SIZE = 100


class HistoricalFetcher:
    """
    Fetches and processes historical Discord messages.

    Iterates backwards through channel history using discord.py's
    channel.history() async iterator, processes messages in batches,
    and stores them via MessageStore.
    """

    def __init__(
        self,
        bot: "TradingBot",
        channel_id: int,
        message_store: MessageStore,
        signal_detector: SignalDetector,
    ) -> None:
        self._bot = bot
        self._channel_id = channel_id
        self._message_store = message_store
        self._signal_detector = signal_detector
        self._delay = settings.discord.historical_fetch_delay_seconds

    async def fetch(
        self,
        limit: Optional[int] = None,
        after: Optional[datetime] = None,
        before: Optional[datetime] = None,
    ) -> int:
        """
        Fetch and store historical messages from the target channel.

        Args:
            limit:  Maximum number of messages to fetch.
                    Defaults to DISCORD_HISTORICAL_BACKFILL_LIMIT from config.
            after:  Only fetch messages after this datetime (UTC).
            before: Only fetch messages before this datetime (UTC).

        Returns:
            Total number of messages processed (not necessarily all new).
        """
        effective_limit = limit or settings.discord.historical_backfill_limit

        channel = self._bot.get_channel(self._channel_id)
        if channel is None:
            logger.error("Cannot fetch history: channel %d not found", self._channel_id)
            return 0

        if not isinstance(channel, discord.TextChannel):
            logger.error("Channel %d is not a text channel", self._channel_id)
            return 0

        logger.info(
            "Starting historical fetch | channel=%s | limit=%d",
            channel.name,
            effective_limit,
        )

        total_processed = 0
        total_new = 0
        batch: list[dict] = []
        signal_ids: list[tuple[str, str]] = []  # (raw_message_id, content)

        try:
            async for message in channel.history(
                limit=effective_limit,
                after=after,
                before=before,
                oldest_first=False,  # Start from most recent
            ):
                # Skip bots unless configured otherwise
                if message.author.bot and not settings.discord.include_bot_messages:
                    continue

                # Skip empty messages
                if not message.content.strip() and not message.embeds:
                    continue

                raw_data = self._build_raw_data(message)
                batch.append(raw_data)
                total_processed += 1

                # Process in batches to avoid large transactions
                if len(batch) >= DISCORD_HISTORY_BATCH_SIZE:
                    new_count = await self._flush_batch(batch)
                    total_new += new_count
                    batch.clear()
                    logger.debug(
                        "Flushed batch | total_processed=%d | new=%d",
                        total_processed,
                        total_new,
                    )
                    # Throttle to avoid hammering the parser
                    await asyncio.sleep(self._delay)

            # Flush remaining
            if batch:
                new_count = await self._flush_batch(batch)
                total_new += new_count

        except discord.Forbidden:
            logger.error(
                "Missing permissions to read history in channel %d. "
                "Ensure the bot has Read Message History permission.",
                self._channel_id,
            )
        except discord.HTTPException as exc:
            logger.error(
                "Discord API error during history fetch: %s (status=%s)",
                exc.text,
                exc.status,
            )
        except asyncio.CancelledError:
            logger.warning("Historical fetch cancelled")
            raise

        logger.info(
            "Historical fetch complete | total_processed=%d | newly_stored=%d",
            total_processed,
            total_new,
        )
        return total_processed

    async def _flush_batch(self, batch: list[dict]) -> int:
        """
        Stores a batch of raw messages and dispatches detected signals.
        Returns the count of newly inserted messages.
        """
        async with get_db_context() as db:
            new_count = await self._message_store.store_batch(db, batch)

        # Detect and dispatch signals from this batch
        for raw_data in batch:
            content = raw_data.get("content", "")
            if not content:
                continue

            is_signal, confidence, keywords = self._signal_detector.detect(content)
            if is_signal:
                logger.info(
                    "Historical signal detected | msg_id=%s | confidence=%.2f",
                    raw_data["message_id"],
                    confidence,
                )
                # Dispatch to parser asynchronously (don't block the batch loop)
                asyncio.create_task(
                    self._dispatch_to_parser(raw_data["message_id"], content),
                    name=f"parse_historical_{raw_data['message_id']}",
                )

        return new_count

    async def _dispatch_to_parser(self, message_id: str, content: str) -> None:
        """Looks up the stored record and dispatches it to the parser."""
        try:
            from app.services.signal_parser.parser_router import ParserRouter

            async with get_db_context() as db:
                record = await self._message_store.get_by_message_id(db, message_id)
                if record is None:
                    logger.warning("Cannot parse: no DB record for msg_id=%s", message_id)
                    return

                router = ParserRouter()
                await router.parse_and_store(
                    db=db,
                    raw_message_id=record.id,
                    raw_text=content,
                )
        except Exception as exc:
            logger.exception(
                "Error dispatching historical message %s to parser: %s",
                message_id,
                exc,
            )

    @staticmethod
    def _build_raw_data(message: discord.Message) -> dict:
        """
        Convert a discord.Message to a plain dict.
        Reuses the same structure as the real-time handler.
        """
        guild_id = str(message.guild.id) if message.guild else "0"
        channel_id = str(message.channel.id)
        message_id = str(message.id)

        return {
            "message_id": message_id,
            "channel_id": channel_id,
            "guild_id": guild_id,
            "author_id": str(message.author.id),
            "author_username": message.author.name,
            "author_display_name": message.author.display_name,
            "content": message.content or "",
            "embeds": [e.to_dict() for e in message.embeds] or None,
            "attachments": [
                {
                    "id": str(a.id),
                    "filename": a.filename,
                    "url": a.url,
                    "content_type": a.content_type,
                    "size": a.size,
                }
                for a in message.attachments
            ] or None,
            "message_link": (
                f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
            ),
            "discord_timestamp": message.created_at.replace(tzinfo=timezone.utc),
            "raw_metadata": {
                "source": "historical_backfill",
                "pinned": message.pinned,
                "type": str(message.type),
            },
        }
