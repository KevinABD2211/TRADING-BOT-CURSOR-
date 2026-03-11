"""
discord_ingestor/bot.py
-----------------------
Discord bot client built on discord.py.

Responsibilities:
  1. Connect to the Discord Gateway using the bot token
  2. Subscribe to the target guild/channel
  3. Process new messages in real time (on_message event)
  4. Optionally backfill historical messages on startup
  5. Persist raw messages to the database
  6. Forward suspected trading signals to the parsing pipeline

The bot runs as a standalone asyncio process and communicates with the
main application via the shared PostgreSQL database and Redis pub/sub.

Requires:
  - DISCORD_BOT_TOKEN, DISCORD_GUILD_ID, DISCORD_CHANNEL_ID env vars
  - Message Content Intent enabled in the Discord Developer Portal
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

from app.config import get_settings
from app.database import get_db_context
from app.services.discord_ingestor.message_store import MessageStore
from app.services.discord_ingestor.historical_fetcher import HistoricalFetcher
from app.services.signal_detector import SignalDetector

logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Intents configuration
# ---------------------------------------------------------------------------

def build_intents() -> discord.Intents:
    """
    Configure Discord Gateway intents.

    IMPORTANT: Message Content Intent must be enabled in the
    Discord Developer Portal under Bot > Privileged Gateway Intents.
    Without it, `message.content` will be empty for non-@mention messages.
    """
    intents = discord.Intents.default()
    intents.message_content = True  # Privileged — must be enabled in portal
    intents.guilds = True
    intents.messages = True
    intents.members = False  # Not needed; reduces payload size
    return intents


# ---------------------------------------------------------------------------
# Bot class
# ---------------------------------------------------------------------------

class TradingBot(commands.Bot):
    """
    Main Discord bot for the trading assistant.

    Monitors a single target channel for trading signals.
    All message processing is async and non-blocking.
    """

    def __init__(self) -> None:
        super().__init__(
            command_prefix="!ta ",     # Prefix for any admin commands
            intents=build_intents(),
            help_command=None,          # Disable default help
        )

        self.target_guild_id: int = settings.discord.guild_id
        self.target_channel_id: int = settings.discord.channel_id
        self.include_bot_messages: bool = settings.discord.include_bot_messages

        # Services (initialised after first ready event)
        self._message_store: Optional[MessageStore] = None
        self._signal_detector: Optional[SignalDetector] = None
        self._historical_fetcher: Optional[HistoricalFetcher] = None

        # Internal state
        self._ready: bool = False
        self._backfill_done: bool = False
        self._processed_message_ids: set[str] = set()   # In-memory dedup cache
        self._stats: dict[str, int] = {
            "messages_seen": 0,
            "messages_stored": 0,
            "signals_detected": 0,
            "errors": 0,
        }

    # ------------------------------------------------------------------
    # Lifecycle events
    # ------------------------------------------------------------------

    async def setup_hook(self) -> None:
        """Called when the bot is initialising, before on_ready."""
        logger.info("TradingBot setup_hook: initialising services")

        self._message_store = MessageStore()
        self._signal_detector = SignalDetector()
        self._historical_fetcher = HistoricalFetcher(
            bot=self,
            channel_id=self.target_channel_id,
            message_store=self._message_store,
            signal_detector=self._signal_detector,
        )

        # Register admin commands cog
        await self.add_cog(AdminCommands(self))

    async def on_ready(self) -> None:
        """Fired when bot is connected and ready."""
        logger.info(
            "TradingBot connected | user=%s | id=%s",
            self.user,
            self.user.id if self.user else "unknown",
        )

        # Validate target channel is accessible
        channel = self.get_channel(self.target_channel_id)
        if channel is None:
            logger.error(
                "Target channel %s not found. "
                "Check DISCORD_CHANNEL_ID and bot permissions.",
                self.target_channel_id,
            )
            await self.close()
            return

        if not isinstance(channel, discord.TextChannel):
            logger.error(
                "Channel %s is not a text channel (type=%s)",
                self.target_channel_id,
                type(channel).__name__,
            )
            await self.close()
            return

        logger.info(
            "Monitoring channel: #%s in guild: %s",
            channel.name,
            channel.guild.name,
        )

        self._ready = True

        # Trigger backfill as a background task (non-blocking)
        if settings.discord.historical_backfill_limit > 0 and not self._backfill_done:
            asyncio.create_task(
                self._run_historical_backfill(),
                name="discord_historical_backfill",
            )

        # Start the stats-logging heartbeat
        self._stats_reporter.start()

    async def on_disconnect(self) -> None:
        logger.warning("TradingBot disconnected from Discord Gateway")

    async def on_resumed(self) -> None:
        logger.info("TradingBot session resumed")

    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        logger.exception(
            "Unhandled exception in Discord event handler: event=%s",
            event_method,
        )
        self._stats["errors"] += 1

    # ------------------------------------------------------------------
    # Message events
    # ------------------------------------------------------------------

    async def on_message(self, message: discord.Message) -> None:
        """
        Fired for every message the bot can see.
        Only processes messages from the target channel.
        """
        # Process bot commands first (allows admin commands to work)
        await self.process_commands(message)

        # Filter: only target channel
        if message.channel.id != self.target_channel_id:
            return

        # Filter: ignore bot messages unless configured
        if message.author.bot and not self.include_bot_messages:
            return

        # Filter: ignore empty messages (attachments only, etc.)
        content = message.content.strip()
        if not content and not message.embeds:
            return

        self._stats["messages_seen"] += 1

        # Deduplication guard (protects against double delivery during reconnect)
        str_id = str(message.id)
        if str_id in self._processed_message_ids:
            logger.debug("Skipping duplicate real-time message id=%s", str_id)
            return
        self._processed_message_ids.add(str_id)

        # Keep the in-memory cache bounded
        if len(self._processed_message_ids) > 10_000:
            # Evict oldest 5000 entries
            self._processed_message_ids = set(
                list(self._processed_message_ids)[-5_000:]
            )

        await self._handle_message(message, source="realtime")

    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        """
        If a signal message is edited, re-process the new version.
        The old raw record is kept; a new one is inserted with the updated content.
        """
        if after.channel.id != self.target_channel_id:
            return
        if after.author.bot and not self.include_bot_messages:
            return

        content_changed = before.content != after.content
        if not content_changed:
            return

        logger.debug("Message edited: id=%s — reprocessing", after.id)
        await self._handle_message(after, source="edit")

    # ------------------------------------------------------------------
    # Core message handler
    # ------------------------------------------------------------------

    async def _handle_message(
        self, message: discord.Message, source: str = "realtime"
    ) -> None:
        """
        Central handler for all messages.

        Flow:
        1. Build a structured dict from the discord.Message object
        2. Persist raw message to database
        3. Run signal detection heuristics
        4. If signal detected → dispatch to parsing pipeline
        """
        try:
            raw_data = self._build_raw_data(message)

            async with get_db_context() as db:
                raw_record = await self._message_store.store(db, raw_data)

            logger.debug(
                "Raw message stored | id=%s | author=%s | source=%s",
                message.id,
                message.author.display_name,
                source,
            )
            self._stats["messages_stored"] += 1

            # Signal detection
            is_signal, confidence, keywords = self._signal_detector.detect(
                message.content
            )

            if is_signal:
                self._stats["signals_detected"] += 1
                logger.info(
                    "Signal detected | message_id=%s | confidence=%.2f | keywords=%s",
                    message.id,
                    confidence,
                    keywords,
                )
                # Dispatch to parsing pipeline (Celery task in full implementation)
                # For now, inline dispatch
                await self._dispatch_to_parser(raw_record.id, message.content)

        except Exception as exc:
            self._stats["errors"] += 1
            logger.exception(
                "Error handling message id=%s: %s", message.id, exc
            )

    async def _dispatch_to_parser(
        self, raw_message_id: str, content: str
    ) -> None:
        """
        Dispatches a raw message to the signal parsing pipeline.

        In production this publishes a Celery task.
        The parser_router handles stage 1 (regex) → stage 2 (LLM fallback).
        """
        try:
            # Import here to avoid circular imports
            from app.services.signal_parser.parser_router import ParserRouter
            router = ParserRouter()

            async with get_db_context() as db:
                await router.parse_and_store(
                    db=db,
                    raw_message_id=raw_message_id,
                    raw_text=content,
                )
        except Exception as exc:
            logger.exception(
                "Error dispatching message %s to parser: %s",
                raw_message_id,
                exc,
            )

    # ------------------------------------------------------------------
    # Data builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_raw_data(message: discord.Message) -> dict:
        """
        Transforms a discord.Message object into a plain dict
        suitable for database insertion.
        """
        guild_id = str(message.guild.id) if message.guild else "0"
        channel_id = str(message.channel.id)
        message_id = str(message.id)

        message_link = (
            f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
        )

        embeds_data = [e.to_dict() for e in message.embeds] if message.embeds else []
        attachments_data = (
            [
                {
                    "id": str(a.id),
                    "filename": a.filename,
                    "url": a.url,
                    "content_type": a.content_type,
                    "size": a.size,
                }
                for a in message.attachments
            ]
            if message.attachments
            else []
        )

        return {
            "message_id": message_id,
            "channel_id": channel_id,
            "guild_id": guild_id,
            "author_id": str(message.author.id),
            "author_username": message.author.name,
            "author_display_name": message.author.display_name,
            "content": message.content or "",
            "embeds": embeds_data if embeds_data else None,
            "attachments": attachments_data if attachments_data else None,
            "message_link": message_link,
            "discord_timestamp": message.created_at.replace(tzinfo=timezone.utc),
            "raw_metadata": {
                "pinned": message.pinned,
                "mention_everyone": message.mention_everyone,
                "tts": message.tts,
                "type": str(message.type),
            },
        }

    # ------------------------------------------------------------------
    # Historical backfill
    # ------------------------------------------------------------------

    async def _run_historical_backfill(self) -> None:
        """Runs historical message ingestion as a background task."""
        logger.info(
            "Starting historical backfill | limit=%d | channel=%d",
            settings.discord.historical_backfill_limit,
            self.target_channel_id,
        )
        try:
            count = await self._historical_fetcher.fetch()
            self._backfill_done = True
            logger.info("Historical backfill complete | messages_processed=%d", count)
        except Exception as exc:
            logger.exception("Historical backfill failed: %s", exc)

    # ------------------------------------------------------------------
    # Heartbeat / stats
    # ------------------------------------------------------------------

    @tasks.loop(minutes=30)
    async def _stats_reporter(self) -> None:
        """Logs operational statistics every 30 minutes."""
        logger.info(
            "TradingBot stats | seen=%d | stored=%d | signals=%d | errors=%d",
            self._stats["messages_seen"],
            self._stats["messages_stored"],
            self._stats["signals_detected"],
            self._stats["errors"],
        )

    @_stats_reporter.before_loop
    async def _before_stats(self) -> None:
        await self.wait_until_ready()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def is_monitoring(self) -> bool:
        return self._ready and not self.is_closed()

    def get_stats(self) -> dict:
        return dict(self._stats)


# ---------------------------------------------------------------------------
# Admin Commands Cog
# ---------------------------------------------------------------------------

class AdminCommands(commands.Cog):
    """
    Bot management commands prefixed with `!ta `.
    Only accessible to server admins or the bot owner.
    """

    def __init__(self, bot: TradingBot) -> None:
        self.bot = bot

    @commands.command(name="status")
    @commands.has_permissions(administrator=True)
    async def status(self, ctx: commands.Context) -> None:
        """Returns current bot statistics."""
        stats = self.bot.get_stats()
        await ctx.send(
            f"**TradingBot Status**\n"
            f"Messages seen: {stats['messages_seen']}\n"
            f"Messages stored: {stats['messages_stored']}\n"
            f"Signals detected: {stats['signals_detected']}\n"
            f"Errors: {stats['errors']}\n"
            f"Monitoring: {'✅' if self.bot.is_monitoring else '❌'}"
        )

    @commands.command(name="backfill")
    @commands.has_permissions(administrator=True)
    async def backfill(self, ctx: commands.Context, limit: int = 100) -> None:
        """Manually trigger a historical backfill."""
        if limit < 1 or limit > 1000:
            await ctx.send("Limit must be between 1 and 1000.")
            return

        await ctx.send(f"Starting backfill of last {limit} messages...")
        try:
            count = await self.bot._historical_fetcher.fetch(limit=limit)
            await ctx.send(f"Backfill complete. Processed {count} messages.")
        except Exception as exc:
            await ctx.send(f"Backfill failed: {exc}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run_bot() -> None:
    """
    Starts the Discord bot.
    Called from the application startup or as a standalone process.
    """
    bot = TradingBot()

    try:
        logger.info("Starting TradingBot...")
        async with bot:
            await bot.start(settings.discord.bot_token)
    except discord.LoginFailure:
        logger.critical(
            "Discord login failed. Check DISCORD_BOT_TOKEN in your environment."
        )
        raise
    except discord.PrivilegedIntentsRequired:
        logger.critical(
            "Message Content Intent is not enabled. "
            "Enable it in the Discord Developer Portal under "
            "Bot > Privileged Gateway Intents > Message Content Intent."
        )
        raise
    except KeyboardInterrupt:
        logger.info("TradingBot shutdown requested")
    finally:
        if not bot.is_closed():
            await bot.close()
        logger.info("TradingBot stopped")


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    asyncio.run(run_bot())
