"""
Core SQLAlchemy ORM models for the trading assistant.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AssetTypeEnum(str, enum.Enum):
    crypto = "crypto"
    stock = "stock"
    option = "option"
    futures = "futures"
    unknown = "unknown"


class DirectionEnum(str, enum.Enum):
    long = "long"
    short = "short"
    unknown = "unknown"


class SignalSourceEnum(str, enum.Enum):
    discord = "discord"
    tradingview = "tradingview"
    opportunity_scanner = "opportunity_scanner"
    manual = "manual"
    x_twitter = "x_twitter"


class ParseMethodEnum(str, enum.Enum):
    regex = "regex"
    llm = "llm"
    manual = "manual"


class RawDiscordMessage(Base):
    """Stores every Discord message verbatim before parsing."""

    __tablename__ = "raw_discord_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    channel_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    guild_id: Mapped[str] = mapped_column(String(32), nullable=False)
    author_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    author_username: Mapped[str] = mapped_column(String(128), nullable=False)
    author_display_name: Mapped[Optional[str]] = mapped_column(String(128))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embeds: Mapped[Optional[dict]] = mapped_column(JSONB)
    attachments: Mapped[Optional[dict]] = mapped_column(JSONB)
    message_link: Mapped[str] = mapped_column(Text, nullable=False)
    discord_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    parse_attempted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    parse_succeeded: Mapped[Optional[bool]] = mapped_column(Boolean)
    raw_metadata: Mapped[Optional[dict]] = mapped_column(JSONB)

    parsed_signals: Mapped[list["ParsedSignal"]] = relationship(
        "ParsedSignal", back_populates="raw_message", lazy="select"
    )


class ParsedSignal(Base):
    """Structured trade signal extracted from a raw message."""

    __tablename__ = "parsed_signals"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    raw_message_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("raw_discord_messages.id", ondelete="SET NULL"),
        index=True,
    )
    source: Mapped[SignalSourceEnum] = mapped_column(
        Enum(SignalSourceEnum, name="signal_source"), nullable=False
    )
    parse_method: Mapped[ParseMethodEnum] = mapped_column(
        Enum(ParseMethodEnum, name="parse_method"), nullable=False
    )

    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    asset_type: Mapped[AssetTypeEnum] = mapped_column(
        Enum(AssetTypeEnum, name="asset_type"), nullable=False
    )
    exchange: Mapped[Optional[str]] = mapped_column(String(32))

    direction: Mapped[DirectionEnum] = mapped_column(
        Enum(DirectionEnum, name="direction"), nullable=False
    )

    entry_price: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))
    entry_range_low: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))
    entry_range_high: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))
    stop_loss: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))
    take_profit_1: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))
    take_profit_2: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))
    take_profit_3: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))

    timeframe: Mapped[Optional[str]] = mapped_column(String(16))
    leverage: Mapped[Optional[int]] = mapped_column(SmallInteger)
    confidence_wording: Mapped[Optional[str]] = mapped_column(String(128))

    options_expiry: Mapped[Optional[datetime]] = mapped_column(DateTime)
    options_strike: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    options_type: Mapped[Optional[str]] = mapped_column(String(4))

    discord_author_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    discord_author_name: Mapped[Optional[str]] = mapped_column(String(128))
    discord_message_link: Mapped[Optional[str]] = mapped_column(Text)

    risk_reward_ratio: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    signal_completeness_pct: Mapped[Optional[int]] = mapped_column(SmallInteger)

    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duplicate_of_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parsed_signals.id")
    )
    is_actionable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    signal_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    llm_model_used: Mapped[Optional[str]] = mapped_column(String(64))
    llm_confidence: Mapped[Optional[float]] = mapped_column(Numeric(4, 3))
    llm_raw_output: Mapped[Optional[dict]] = mapped_column(JSONB)

    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    raw_message: Mapped[Optional["RawDiscordMessage"]] = relationship(
        "RawDiscordMessage", back_populates="parsed_signals", lazy="select"
    )

