"""
config.py
---------
Centralised application configuration loaded from environment variables.
Uses Pydantic v2 BaseSettings for validation and type safety.

All secrets are sourced from environment variables — never hardcoded.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field, PostgresDsn, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class DiscordSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DISCORD_", extra="ignore")

    bot_token: str = Field(..., description="Discord bot token")
    guild_id: int = Field(..., description="Target Discord server (guild) ID")
    channel_id: int = Field(..., description="Target Discord channel ID to monitor")
    # If True, messages from other bots will also be processed
    include_bot_messages: bool = Field(default=False)
    # How many historical messages to backfill on startup (0 = disabled)
    historical_backfill_limit: int = Field(default=500)
    # Minimum delay between historical fetch batches (seconds)
    historical_fetch_delay_seconds: float = Field(default=1.0)


class BinanceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BINANCE_", extra="ignore")

    api_key: str = Field(..., description="Binance API key")
    api_secret: str = Field(..., description="Binance API secret")
    # 'live' uses fapi.binance.com; 'testnet' uses testnet.binancefuture.com
    environment: Literal["live", "testnet"] = Field(default="testnet")
    max_notional_per_trade_usd: float = Field(default=100.0)
    default_leverage: int = Field(default=1, ge=1, le=125)
    # If True, leverage stated in signal may be applied (subject to risk rules)
    allow_signal_leverage: bool = Field(default=False)
    request_timeout_seconds: int = Field(default=10)


class AlpacaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALPACA_", extra="ignore")

    api_key: str = Field(..., description="Alpaca API key")
    api_secret: str = Field(..., description="Alpaca API secret")
    environment: Literal["paper", "live"] = Field(default="paper")
    paper_base_url: str = Field(default="https://paper-api.alpaca.markets")
    live_base_url: str = Field(default="https://api.alpaca.markets")
    max_notional_per_trade_usd: float = Field(default=500.0)
    fractional_shares_enabled: bool = Field(default=False)

    @property
    def base_url(self) -> str:
        return self.paper_base_url if self.environment == "paper" else self.live_base_url


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_", extra="ignore")

    host: str = Field(default="localhost")
    port: int = Field(default=5432)
    name: str = Field(default="trading_assistant")
    user: str = Field(default="postgres")
    password: str = Field(...)
    pool_size: int = Field(default=10)
    max_overflow: int = Field(default=20)
    pool_timeout: int = Field(default=30)
    echo_sql: bool = Field(default=False)

    @property
    def async_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )

    @property
    def sync_url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.name}"
        )


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_", extra="ignore")

    host: str = Field(default="localhost")
    port: int = Field(default=6379)
    password: Optional[str] = Field(default=None)
    db: int = Field(default=0)
    # Celery broker DB index (separate from default)
    celery_db: int = Field(default=1)

    @property
    def url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"

    @property
    def celery_broker_url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.celery_db}"


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", extra="ignore")

    provider: Literal["openai", "anthropic", "disabled"] = Field(default="openai")
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    anthropic_api_key: Optional[str] = Field(default=None, alias="ANTHROPIC_API_KEY")
    model_name: str = Field(default="gpt-4o-mini")
    temperature: float = Field(default=0.1, ge=0.0, le=1.0)
    max_tokens: int = Field(default=1024)
    request_timeout_seconds: int = Field(default=30)
    # Minimum regex confidence below which LLM fallback is triggered (0.0–1.0)
    fallback_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        populate_by_name=True,
        extra="ignore",
    )


class ScoringSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCORING_", extra="ignore")

    auto_execute_threshold: int = Field(default=80, ge=0, le=100)
    manual_review_threshold: int = Field(default=60, ge=0, le=100)

    # Weights (must sum to ~1.0 before penalty)
    weight_signal_completeness: float = Field(default=0.15)
    weight_risk_reward: float = Field(default=0.15)
    weight_entry_attractiveness: float = Field(default=0.10)
    weight_trend_alignment: float = Field(default=0.10)
    weight_news_sentiment: float = Field(default=0.15)
    weight_analyst_alignment: float = Field(default=0.10)
    weight_source_credibility: float = Field(default=0.10)
    weight_liquidity_volatility: float = Field(default=0.10)
    # Penalty component — negative, max deduction
    max_negative_evidence_penalty: float = Field(default=0.15)


class RiskSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RISK_", extra="ignore")

    daily_loss_limit_usd: float = Field(default=200.0)
    max_open_positions: int = Field(default=5)
    max_crypto_exposure_usd: float = Field(default=300.0)
    max_stock_exposure_usd: float = Field(default=1500.0)
    consecutive_loss_cooldown_minutes: int = Field(default=60)
    consecutive_losses_before_cooldown: int = Field(default=3)
    kill_switch_active: bool = Field(default=False)
    manual_pause_active: bool = Field(default=False)
    require_stop_loss: bool = Field(default=True)
    max_spread_pct: float = Field(default=0.5)  # 0.5%


class NotificationSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTIFY_", extra="ignore")

    telegram_enabled: bool = Field(default=False)
    telegram_bot_token: Optional[str] = Field(default=None)
    telegram_chat_id: Optional[str] = Field(default=None)

    email_enabled: bool = Field(default=False)
    email_sender: Optional[str] = Field(default=None)
    email_recipient: Optional[str] = Field(default=None)
    smtp_host: Optional[str] = Field(default=None)
    smtp_port: int = Field(default=587)
    smtp_username: Optional[str] = Field(default=None)
    smtp_password: Optional[str] = Field(default=None)

    slack_enabled: bool = Field(default=False)
    slack_bot_token: Optional[str] = Field(default=None)
    slack_channel_id: Optional[str] = Field(default=None)


class AppSettings(BaseSettings):
    """
    Root application settings.
    Aggregates all sub-settings and provides app-level configuration.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # General
    app_name: str = Field(default="TradingAssistant")
    environment: Literal["development", "staging", "production"] = Field(
        default="development"
    )
    debug: bool = Field(default=False)
    secret_key: str = Field(
        ..., description="Random secret key for JWT / session signing"
    )
    api_version: str = Field(default="v1")
    log_level: str = Field(default="INFO")

    # Execution mode — system-wide override
    execution_mode: Literal["paper", "live"] = Field(
        default="paper",
        description="paper = no real orders; live = real execution",
    )

    # Sub-settings (populated by nested env var parsing)
    discord: DiscordSettings = Field(default_factory=DiscordSettings)
    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    alpaca: AlpacaSettings = Field(default_factory=AlpacaSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    risk: RiskSettings = Field(default_factory=RiskSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)

    @model_validator(mode="after")
    def validate_live_mode_requirements(self) -> "AppSettings":
        """
        Enforce safety checks when switching to live trading.
        Live mode must be explicitly enabled with awareness of consequences.
        """
        if self.execution_mode == "live":
            if self.environment != "production":
                raise ValueError(
                    "execution_mode='live' is only permitted when environment='production'. "
                    "Set ENVIRONMENT=production in your .env file to proceed."
                )
            logger.warning(
                "⚠️  LIVE TRADING MODE ACTIVE — Real money will be committed to orders."
            )
        return self

    @property
    def is_paper_mode(self) -> bool:
        return self.execution_mode == "paper"

    @property
    def is_live_mode(self) -> bool:
        return self.execution_mode == "live"


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """
    Returns a cached singleton of AppSettings.
    Call get_settings() anywhere in the app to get the current config.

    To reload settings in tests, call get_settings.cache_clear() first.
    """
    settings = AppSettings()
    logger.info(
        "Configuration loaded | env=%s | mode=%s | log_level=%s",
        settings.environment,
        settings.execution_mode,
        settings.log_level,
    )
    return settings
