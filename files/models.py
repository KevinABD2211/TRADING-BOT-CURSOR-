"""
models.py
---------
SQLAlchemy ORM models for the trading assistant.

These models map directly to the PostgreSQL schema defined in Phase 0.
Only the models required for Phase 1 (ingestion + parsing) are
implemented here. Remaining models are defined in their own files
under app/models/.

Naming convention:
  - Table names: snake_case, plural
  - Column names: snake_case
  - Relationship names: snake_case
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# ---------------------------------------------------------------------------
# Enum definitions (mirror PostgreSQL CREATE TYPE statements)
# ---------------------------------------------------------------------------

import enum


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


class ExecutionModeEnum(str, enum.Enum):
    paper = "paper"
    live = "live"


class DecisionOutcomeEnum(str, enum.Enum):
    auto_executed = "auto_executed"
    queued_for_approval = "queued_for_approval"
    approved = "approved"
    rejected_risk = "rejected_risk"
    rejected_score = "rejected_score"
    rejected_manual = "rejected_manual"
    skipped_duplicate = "skipped_duplicate"


class OrderStatusEnum(str, enum.Enum):
    pending = "pending"
    submitted = "submitted"
    partially_filled = "partially_filled"
    filled = "filled"
    cancelled = "cancelled"
    rejected = "rejected"
    expired = "expired"


class OrderSideEnum(str, enum.Enum):
    buy = "buy"
    sell = "sell"


class OrderTypeEnum(str, enum.Enum):
    market = "market"
    limit = "limit"
    stop = "stop"
    stop_limit = "stop_limit"


class BrokerEnum(str, enum.Enum):
    binance_futures = "binance_futures"
    alpaca = "alpaca"
    paper_binance = "paper_binance"
    paper_alpaca = "paper_alpaca"


class ProviderStatusEnum(str, enum.Enum):
    healthy = "healthy"
    degraded = "degraded"
    down = "down"
    disabled = "disabled"


class RiskEventTypeEnum(str, enum.Enum):
    daily_loss_limit = "daily_loss_limit"
    max_positions = "max_positions"
    exposure_limit = "exposure_limit"
    cooldown_active = "cooldown_active"
    kill_switch = "kill_switch"
    spread_too_wide = "spread_too_wide"
    low_liquidity = "low_liquidity"
    missing_stop_loss = "missing_stop_loss"
    market_closed = "market_closed"
    duplicate_signal = "duplicate_signal"
    not_executable_under_cap = "not_executable_under_cap"


class MarketRegimeEnum(str, enum.Enum):
    bull = "bull"
    bear = "bear"
    sideways = "sideways"
    unknown = "unknown"


class NotificationChannelEnum(str, enum.Enum):
    telegram = "telegram"
    email = "email"
    slack = "slack"
    dashboard = "dashboard"


class NotificationEventEnum(str, enum.Enum):
    new_signal = "new_signal"
    trade_executed = "trade_executed"
    trade_rejected = "trade_rejected"
    opportunity_detected = "opportunity_detected"
    risk_violation = "risk_violation"
    provider_failure = "provider_failure"
    daily_summary = "daily_summary"
    kill_switch_triggered = "kill_switch_triggered"
    manual_approval_required = "manual_approval_required"


# ---------------------------------------------------------------------------
# Model: RawDiscordMessage
# ---------------------------------------------------------------------------


class RawDiscordMessage(Base):
    """
    Stores every Discord message verbatim before any parsing is attempted.
    This is the source-of-truth for all signals originating from Discord.
    """

    __tablename__ = "raw_discord_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Discord snowflake — globally unique across Discord
    message_id: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
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

    # Relationships
    parsed_signals: Mapped[list["ParsedSignal"]] = relationship(
        "ParsedSignal", back_populates="raw_message", lazy="select"
    )

    def __repr__(self) -> str:
        return (
            f"<RawDiscordMessage id={self.id} "
            f"message_id={self.message_id} "
            f"author={self.author_username}>"
        )


# ---------------------------------------------------------------------------
# Model: ParsedSignal
# ---------------------------------------------------------------------------


class ParsedSignal(Base):
    """
    Structured trade signal extracted from a raw message.
    One raw message may produce zero or one parsed signal.
    All pricing fields use Numeric(24, 8) to handle both crypto micro-prices
    and large stock prices without precision loss.
    """

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

    # --- Instrument ---
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    asset_type: Mapped[AssetTypeEnum] = mapped_column(
        Enum(AssetTypeEnum, name="asset_type"), nullable=False
    )
    exchange: Mapped[Optional[str]] = mapped_column(String(32))

    # --- Direction ---
    direction: Mapped[DirectionEnum] = mapped_column(
        Enum(DirectionEnum, name="direction"), nullable=False
    )

    # --- Pricing ---
    entry_price: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))
    entry_range_low: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))
    entry_range_high: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))
    stop_loss: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))
    take_profit_1: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))
    take_profit_2: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))
    take_profit_3: Mapped[Optional[float]] = mapped_column(Numeric(24, 8))

    # --- Trade Parameters ---
    timeframe: Mapped[Optional[str]] = mapped_column(String(16))
    leverage: Mapped[Optional[int]] = mapped_column(SmallInteger)
    confidence_wording: Mapped[Optional[str]] = mapped_column(String(128))

    # --- Options (if applicable) ---
    options_expiry: Mapped[Optional[datetime]] = mapped_column(DateTime)
    options_strike: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    options_type: Mapped[Optional[str]] = mapped_column(String(4))  # CALL / PUT

    # --- Source Attribution ---
    discord_author_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    discord_author_name: Mapped[Optional[str]] = mapped_column(String(128))
    discord_message_link: Mapped[Optional[str]] = mapped_column(Text)
    tradingview_alert_id: Mapped[Optional[str]] = mapped_column(String(64))

    # --- Computed Metrics ---
    risk_reward_ratio: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    signal_completeness_pct: Mapped[Optional[int]] = mapped_column(SmallInteger)

    # --- Deduplication Flags ---
    is_duplicate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    duplicate_of_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("parsed_signals.id")
    )
    is_actionable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # --- Timestamps ---
    signal_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    parsed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # --- LLM Metadata ---
    llm_model_used: Mapped[Optional[str]] = mapped_column(String(64))
    llm_confidence: Mapped[Optional[float]] = mapped_column(Numeric(4, 3))
    llm_raw_output: Mapped[Optional[dict]] = mapped_column(JSONB)

    raw_text: Mapped[str] = mapped_column(Text, nullable=False)

    # --- Relationships ---
    raw_message: Mapped[Optional["RawDiscordMessage"]] = relationship(
        "RawDiscordMessage", back_populates="parsed_signals", lazy="select"
    )

    def __repr__(self) -> str:
        return (
            f"<ParsedSignal id={self.id} "
            f"symbol={self.symbol} "
            f"direction={self.direction} "
            f"entry={self.entry_price}>"
        )

    @property
    def has_stop_loss(self) -> bool:
        return self.stop_loss is not None

    @property
    def has_take_profit(self) -> bool:
        return self.take_profit_1 is not None

    @property
    def has_entry(self) -> bool:
        return self.entry_price is not None or (
            self.entry_range_low is not None and self.entry_range_high is not None
        )

    def compute_risk_reward(self) -> Optional[float]:
        """
        Compute risk-reward ratio from entry, stop loss, and first take profit.
        Returns None if insufficient data.
        """
        entry = self.entry_price or (
            (self.entry_range_low + self.entry_range_high) / 2
            if self.entry_range_low and self.entry_range_high
            else None
        )
        if not entry or not self.stop_loss or not self.take_profit_1:
            return None

        risk = abs(entry - self.stop_loss)
        reward = abs(self.take_profit_1 - entry)

        if risk == 0:
            return None

        return round(reward / risk, 4)

    def compute_completeness_pct(self) -> int:
        """
        Score signal completeness as a percentage.
        Core fields are weighted more heavily.
        """
        fields = {
            "symbol": (self.symbol, 20),
            "direction": (self.direction != DirectionEnum.unknown, 20),
            "entry": (self.has_entry, 20),
            "stop_loss": (self.stop_loss is not None, 20),
            "take_profit": (self.has_take_profit, 10),
            "timeframe": (self.timeframe is not None, 5),
            "asset_type": (self.asset_type != AssetTypeEnum.unknown, 5),
        }
        total_weight = sum(w for _, (_, w) in fields.items())
        earned = sum(w for _, (present, w) in fields.items() if present)
        return round((earned / total_weight) * 100)
