"""
discord_ingestor/message_store.py
----------------------------------
Persists raw Discord messages to the `raw_discord_messages` table.

Handles:
  - Insert with conflict resolution (upsert on message_id)
  - Marking records as parse_attempted / parse_succeeded
  - Bulk batch inserts for historical backfill
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RawDiscordMessage

logger = logging.getLogger(__name__)


class MessageStore:
    """
    Data-access layer for raw_discord_messages.
    All methods accept an AsyncSession to participate in the caller's
    transaction scope.
    """

    async def store(
        self,
        db: AsyncSession,
        data: dict,
    ) -> RawDiscordMessage:
        """
        Insert a single raw message.

        Uses PostgreSQL INSERT ... ON CONFLICT DO NOTHING to safely handle
        duplicate deliveries (e.g., reconnect storms, edit events).

        Returns the existing or newly inserted record.
        """
        stmt = (
            pg_insert(RawDiscordMessage)
            .values(
                id=uuid.uuid4(),
                message_id=data["message_id"],
                channel_id=data["channel_id"],
                guild_id=data["guild_id"],
                author_id=data["author_id"],
                author_username=data["author_username"],
                author_display_name=data.get("author_display_name"),
                content=data["content"],
                embeds=data.get("embeds"),
                attachments=data.get("attachments"),
                message_link=data["message_link"],
                discord_timestamp=data["discord_timestamp"],
                ingested_at=datetime.now(tz=timezone.utc),
                parse_attempted=False,
                raw_metadata=data.get("raw_metadata"),
            )
            .on_conflict_do_nothing(index_elements=["message_id"])
            .returning(RawDiscordMessage)
        )

        result = await db.execute(stmt)
        row = result.fetchone()

        if row is None:
            # Conflict: message already exists — fetch the existing record
            existing = await self.get_by_message_id(db, data["message_id"])
            logger.debug(
                "Message already stored (conflict), id=%s", data["message_id"]
            )
            return existing  # type: ignore[return-value]

        logger.debug("Stored raw message id=%s", data["message_id"])
        return row[0]

    async def store_batch(
        self,
        db: AsyncSession,
        messages: list[dict],
    ) -> int:
        """
        Bulk insert a list of raw message dicts.
        Skips duplicates via ON CONFLICT DO NOTHING.

        Returns the number of newly inserted records.
        """
        if not messages:
            return 0

        records = [
            {
                "id": uuid.uuid4(),
                "message_id": m["message_id"],
                "channel_id": m["channel_id"],
                "guild_id": m["guild_id"],
                "author_id": m["author_id"],
                "author_username": m["author_username"],
                "author_display_name": m.get("author_display_name"),
                "content": m["content"],
                "embeds": m.get("embeds"),
                "attachments": m.get("attachments"),
                "message_link": m["message_link"],
                "discord_timestamp": m["discord_timestamp"],
                "ingested_at": datetime.now(tz=timezone.utc),
                "parse_attempted": False,
                "raw_metadata": m.get("raw_metadata"),
            }
            for m in messages
        ]

        stmt = (
            pg_insert(RawDiscordMessage)
            .values(records)
            .on_conflict_do_nothing(index_elements=["message_id"])
        )
        result = await db.execute(stmt)
        inserted = result.rowcount
        logger.info("Batch stored %d/%d messages", inserted, len(messages))
        return inserted

    async def get_by_message_id(
        self,
        db: AsyncSession,
        message_id: str,
    ) -> Optional[RawDiscordMessage]:
        """Fetch a raw message record by its Discord message ID."""
        result = await db.execute(
            select(RawDiscordMessage).where(
                RawDiscordMessage.message_id == message_id
            )
        )
        return result.scalar_one_or_none()

    async def get_unparsed(
        self,
        db: AsyncSession,
        limit: int = 100,
    ) -> Sequence[RawDiscordMessage]:
        """
        Fetch raw messages that have not yet had a parse attempt.
        Used by the parsing worker to process any missed messages.
        """
        result = await db.execute(
            select(RawDiscordMessage)
            .where(RawDiscordMessage.parse_attempted == False)  # noqa: E712
            .order_by(RawDiscordMessage.discord_timestamp.asc())
            .limit(limit)
        )
        return result.scalars().all()

    async def mark_parse_attempted(
        self,
        db: AsyncSession,
        raw_message_id: uuid.UUID,
        succeeded: bool,
    ) -> None:
        """
        Update a raw message record after a parse attempt.
        Called by the parser regardless of whether parsing succeeded.
        """
        await db.execute(
            update(RawDiscordMessage)
            .where(RawDiscordMessage.id == raw_message_id)
            .values(
                parse_attempted=True,
                parse_succeeded=succeeded,
            )
        )
        logger.debug(
            "Marked raw_message_id=%s as parse_attempted=%s succeeded=%s",
            raw_message_id,
            True,
            succeeded,
        )

    async def exists(self, db: AsyncSession, message_id: str) -> bool:
        """Fast existence check using a count query."""
        from sqlalchemy import func, select

        result = await db.execute(
            select(func.count())
            .select_from(RawDiscordMessage)
            .where(RawDiscordMessage.message_id == message_id)
        )
        return (result.scalar_one() or 0) > 0
