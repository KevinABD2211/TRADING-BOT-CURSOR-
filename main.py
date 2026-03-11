"""
Trading Assistant — FastAPI backend for the web dashboard.

Run: uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, init_db, dispose_db
from app.models import (
    AssetTypeEnum,
    BrokerEnum,
    DirectionEnum,
    Execution,
    ExecutionStatusEnum,
    ParsedSignal,
    ParseMethodEnum,
    RawDiscordMessage,
    SignalSourceEnum,
)
from app.services.market_signal_service import get_market_signals
from app.services.alpaca_service import (
    get_account as alpaca_get_account,
    get_latest_price as alpaca_get_price,
    place_order as alpaca_place_order,
    is_alpaca_configured,
)
from app.services.research_service import get_research_confidence
from app.services.advice_service import get_advice_for_symbol, get_all_advice

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_db()
    except Exception as e:
        logger.warning("Database init skipped or failed: %s", e)
    yield
    await dispose_db()


app = FastAPI(
    title="Trading Assistant API",
    description="Backend for the trading signal dashboard",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    api: str = "ok"


class StatsResponse(BaseModel):
    raw_messages_total: int
    signals_total: int
    signals_actionable: int
    executions_total: int = 0
    execution_mode: str
    environment: str


class SignalItem(BaseModel):
    id: str
    symbol: str
    asset_type: str
    direction: str
    entry_price: Optional[float]
    stop_loss: Optional[float]
    take_profit_1: Optional[float]
    take_profit_2: Optional[float]
    take_profit_3: Optional[float]
    leverage: Optional[int]
    timeframe: Optional[str]
    signal_timestamp: Optional[datetime]
    parsed_at: Optional[datetime]
    parse_method: str
    source: str = "discord"
    signal_completeness_pct: Optional[int]
    llm_confidence: Optional[float]
    confidence_wording: Optional[str]
    risk_reward_ratio: Optional[float]
    raw_text_preview: str
    discord_author_name: Optional[str]
    discord_message_link: Optional[str]

    class Config:
        from_attributes = True


class SignalsListResponse(BaseModel):
    signals: list[SignalItem]
    total: int


class MarketSignalItem(BaseModel):
    """One signal from live market API (e.g. Binance 24h movers)."""
    id: str
    symbol: str
    direction: str
    entry_price: Optional[float]
    price_change_pct: Optional[float]
    confidence_pct: Optional[int] = None  # 0-100 derived from |price_change_pct|
    raw_text_preview: str
    signal_timestamp: Optional[str]
    parse_method: str
    source: str


class MarketSignalsResponse(BaseModel):
    signals: list[MarketSignalItem]
    total: int
    source: str = "binance"


class SourceCount(BaseModel):
    source: str
    count: int


class SourcesResponse(BaseModel):
    sources: list[SourceCount]


class ExecutionItem(BaseModel):
    id: str
    symbol: str
    direction: str
    side: str
    quantity: Optional[float]
    price: Optional[float]
    notional_usd: Optional[float]
    status: str
    broker: str
    executed_at: Optional[datetime]
    created_at: Optional[datetime]
    notes: Optional[str]

    class Config:
        from_attributes = True


class ExecutionsListResponse(BaseModel):
    executions: list[ExecutionItem]
    total: int


class CreateExecutionRequest(BaseModel):
    symbol: str
    direction: str  # long | short
    quantity: Optional[float] = None
    notional_usd: Optional[float] = None
    price: Optional[float] = None
    notes: Optional[str] = None


class AlpacaAccountResponse(BaseModel):
    configured: bool
    environment: Optional[str] = None
    buying_power: Optional[str] = None
    cash: Optional[str] = None
    portfolio_value: Optional[str] = None


class AlpacaSuggestionItem(BaseModel):
    signal_id: str
    symbol: str
    direction: str
    entry_price: Optional[float]
    current_price: Optional[float]
    suggested_notional_usd: float
    suggested_qty: Optional[float]
    source: str
    confidence_pct: Optional[int]
    stop_loss: Optional[float]
    take_profit_1: Optional[float]


class AlpacaSuggestionsResponse(BaseModel):
    suggestions: list[AlpacaSuggestionItem]
    total: int
    alpaca_configured: bool


class AlpacaExecuteRequest(BaseModel):
    symbol: str
    direction: str  # long | short
    quantity: Optional[float] = None
    notional_usd: Optional[float] = None
    signal_id: Optional[str] = None


class ResearchConfidenceResponse(BaseModel):
    confidence_pct: int
    rationale: str
    sources: list[str]


class AdviceItemResponse(BaseModel):
    """One recommendation per commodity: LONG or SHORT with target and stop loss always set."""
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    target_price: float
    confidence_pct: int
    rationale: str
    sources_used: list[str]


class AdviceListResponse(BaseModel):
    advice: list[AdviceItemResponse]
    total: int


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    """Root route so opening the API URL in a browser doesn't 404."""
    return {"message": "Trading Assistant API", "docs": "/docs", "health": "/health", "api_prefix": "/api"}


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok")


@app.get("/api/stats", response_model=StatsResponse)
async def stats(db: AsyncSession = Depends(get_db)):
    try:
        try:
            settings = get_settings()
            execution_mode = settings.execution_mode
            environment = settings.environment
        except Exception:
            execution_mode = "paper"
            environment = "development"

        raw_count = await db.execute(select(func.count()).select_from(RawDiscordMessage))
        signals_count = await db.execute(select(func.count()).select_from(ParsedSignal))
        actionable_count = await db.execute(
            select(func.count()).select_from(ParsedSignal).where(ParsedSignal.is_actionable == True)
        )
        exec_count = await db.execute(select(func.count()).select_from(Execution))

        return StatsResponse(
            raw_messages_total=raw_count.scalar() or 0,
            signals_total=signals_count.scalar() or 0,
            signals_actionable=actionable_count.scalar() or 0,
            executions_total=exec_count.scalar() or 0,
            execution_mode=execution_mode,
            environment=environment,
        )
    except Exception as e:
        logger.warning("Stats failed (database may be unavailable): %s", e)
        return StatsResponse(
            raw_messages_total=0,
            signals_total=0,
            signals_actionable=0,
            executions_total=0,
            execution_mode="paper",
            environment="development",
        )


@app.get("/api/signals", response_model=SignalsListResponse)
async def list_signals(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    symbol: Optional[str] = Query(None),
    direction: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    min_confidence: Optional[int] = Query(None, ge=0, le=100),
):
    try:
        q = select(ParsedSignal).order_by(desc(ParsedSignal.parsed_at))
        count_q = select(func.count()).select_from(ParsedSignal)

        if symbol:
            q = q.where(ParsedSignal.symbol == symbol.upper())
            count_q = count_q.where(ParsedSignal.symbol == symbol.upper())
        if direction and direction.lower() in ("long", "short"):
            q = q.where(ParsedSignal.direction == direction.lower())
            count_q = count_q.where(ParsedSignal.direction == direction.lower())
        if source:
            try:
                src_enum = SignalSourceEnum(source.lower())
                q = q.where(ParsedSignal.source == src_enum)
                count_q = count_q.where(ParsedSignal.source == src_enum)
            except ValueError:
                pass
        if min_confidence is not None:
            q = q.where(ParsedSignal.signal_completeness_pct >= min_confidence)
            count_q = count_q.where(ParsedSignal.signal_completeness_pct >= min_confidence)

        total = (await db.execute(count_q)).scalar() or 0
        q = q.offset(offset).limit(limit)
        result = await db.execute(q)
        rows = result.scalars().all()

        def to_item(s: ParsedSignal) -> SignalItem:
            return SignalItem(
                id=str(s.id),
                symbol=s.symbol,
                asset_type=s.asset_type.value if hasattr(s.asset_type, "value") else str(s.asset_type),
                direction=s.direction.value if hasattr(s.direction, "value") else str(s.direction),
                entry_price=float(s.entry_price) if s.entry_price is not None else None,
                stop_loss=float(s.stop_loss) if s.stop_loss is not None else None,
                take_profit_1=float(s.take_profit_1) if s.take_profit_1 is not None else None,
                take_profit_2=float(s.take_profit_2) if s.take_profit_2 is not None else None,
                take_profit_3=float(s.take_profit_3) if s.take_profit_3 is not None else None,
                leverage=s.leverage,
                timeframe=s.timeframe,
                signal_timestamp=s.signal_timestamp,
                parsed_at=s.parsed_at,
                parse_method=s.parse_method.value if hasattr(s.parse_method, "value") else str(s.parse_method),
                source=s.source.value if hasattr(s.source, "value") else str(s.source),
                signal_completeness_pct=s.signal_completeness_pct,
                llm_confidence=float(s.llm_confidence) if s.llm_confidence is not None else None,
                confidence_wording=s.confidence_wording,
                risk_reward_ratio=float(s.risk_reward_ratio) if s.risk_reward_ratio is not None else None,
                raw_text_preview=(s.raw_text or "")[:200],
                discord_author_name=s.discord_author_name,
                discord_message_link=s.discord_message_link,
            )

        return SignalsListResponse(signals=[to_item(s) for s in rows], total=total)
    except Exception as e:
        logger.warning("List signals failed (database may be unavailable): %s", e)
        return SignalsListResponse(signals=[], total=0)


@app.post("/api/seed-demo")
async def seed_demo_signals(db: AsyncSession = Depends(get_db)):
    """Insert demo signals so you can see data on the Signals page without Discord."""
    now = datetime.now(timezone.utc)
    demos = [
        ("AAPL", AssetTypeEnum.stock, DirectionEnum.long, 178.50, 175.0, 185.0, "AAPL long from support. SL 175, TP 185."),
        ("MSFT", AssetTypeEnum.stock, DirectionEnum.long, 415.20, 408.0, 425.0, "MSFT breakout. Entry 415, SL 408, TP 425."),
        ("TSLA", AssetTypeEnum.stock, DirectionEnum.short, 242.0, 248.0, 230.0, "TSLA short at resistance 242. SL 248, TP 230."),
        ("GOOGL", AssetTypeEnum.stock, DirectionEnum.long, 172.0, 168.0, 180.0, "GOOGL long. Entry 172, SL 168, TP 180."),
        ("NVDA", AssetTypeEnum.stock, DirectionEnum.long, 138.0, 132.0, 148.0, "NVDA long from key level. SL 132, TP 148."),
        ("BTC", AssetTypeEnum.crypto, DirectionEnum.long, 97200.0, 95000.0, 100000.0, "BTC long. Entry 97200, SL 95k, TP 100k."),
        ("ETH", AssetTypeEnum.crypto, DirectionEnum.long, 3480.0, 3380.0, 3650.0, "ETH long. Entry 3480, SL 3380, TP 3650."),
        ("META", AssetTypeEnum.stock, DirectionEnum.long, 585.0, 575.0, 605.0, "META long. Entry 585, SL 575, TP 605."),
    ]
    count = 0
    for symbol, asset_type, direction, entry, sl, tp, raw_text in demos:
        sig = ParsedSignal(
            source=SignalSourceEnum.manual,
            parse_method=ParseMethodEnum.manual,
            symbol=symbol,
            asset_type=asset_type,
            direction=direction,
            entry_price=entry,
            stop_loss=sl,
            take_profit_1=tp,
            signal_timestamp=now,
            raw_text=raw_text,
            is_actionable=True,
            signal_completeness_pct=75,
        )
        db.add(sig)
        count += 1
    await db.flush()
    return {"seeded": count, "message": "Demo signals added. Refresh the Signals page."}


@app.get("/api/signals/unified", response_model=SignalsListResponse)
async def list_signals_unified(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
):
    """Combined view: DB signals first, then market (Binance) signals so you always see something."""
    from app.services.market_signal_service import get_market_signals
    try:
        q = select(ParsedSignal).order_by(desc(ParsedSignal.parsed_at)).limit(limit)
        result = await db.execute(q)
        rows = result.scalars().all()
        def to_item(s: ParsedSignal) -> SignalItem:
            return SignalItem(
                id=str(s.id),
                symbol=s.symbol,
                asset_type=s.asset_type.value if hasattr(s.asset_type, "value") else str(s.asset_type),
                direction=s.direction.value if hasattr(s.direction, "value") else str(s.direction),
                entry_price=float(s.entry_price) if s.entry_price is not None else None,
                stop_loss=float(s.stop_loss) if s.stop_loss is not None else None,
                take_profit_1=float(s.take_profit_1) if s.take_profit_1 is not None else None,
                take_profit_2=float(s.take_profit_2) if s.take_profit_2 is not None else None,
                take_profit_3=float(s.take_profit_3) if s.take_profit_3 is not None else None,
                leverage=s.leverage,
                timeframe=s.timeframe,
                signal_timestamp=s.signal_timestamp,
                parsed_at=s.parsed_at,
                parse_method=s.parse_method.value if hasattr(s.parse_method, "value") else str(s.parse_method),
                source=s.source.value if hasattr(s.source, "value") else str(s.source),
                signal_completeness_pct=s.signal_completeness_pct,
                llm_confidence=float(s.llm_confidence) if s.llm_confidence is not None else None,
                confidence_wording=s.confidence_wording,
                risk_reward_ratio=float(s.risk_reward_ratio) if s.risk_reward_ratio is not None else None,
                raw_text_preview=(s.raw_text or "")[:200],
                discord_author_name=s.discord_author_name,
                discord_message_link=s.discord_message_link,
            )
        signals = [to_item(s) for s in rows]
        total_db = len(signals)
    except Exception:
        signals = []
        total_db = 0
    # Append market signals if we have room; always set default TP/SL so every row has target and stop loss
    market = await get_market_signals(limit=max(0, limit - total_db))
    try:
        sl_pct = get_settings().advice.default_sl_pct / 100.0
        tp_pct = get_settings().advice.default_tp_pct / 100.0
    except Exception:
        sl_pct, tp_pct = 0.02, 0.04
    for r in market:
        entry = r.get("entry_price")
        direction = (r.get("direction") or "long").lower()
        if entry is not None and entry > 0:
            if direction == "long":
                sl, tp = entry * (1 - sl_pct), entry * (1 + tp_pct)
            else:
                sl, tp = entry * (1 + sl_pct), entry * (1 - tp_pct)
        else:
            sl, tp = None, None
        signals.append(
            SignalItem(
                id=f"market-{r['symbol']}",
                symbol=r["symbol"],
                asset_type="crypto",
                direction=r["direction"],
                entry_price=entry,
                stop_loss=sl,
                take_profit_1=tp,
                take_profit_2=None,
                take_profit_3=None,
                leverage=None,
                timeframe=None,
                signal_timestamp=datetime.fromisoformat(r["signal_timestamp"].replace("Z", "+00:00")) if r.get("signal_timestamp") else None,
                parsed_at=None,
                parse_method=r.get("parse_method", "market_api"),
                source=r.get("source", "binance"),
                signal_completeness_pct=r.get("confidence_pct"),
                llm_confidence=None,
                confidence_wording=None,
                risk_reward_ratio=None,
                raw_text_preview=r.get("raw_text_preview", ""),
                discord_author_name=None,
                discord_message_link=None,
            )
        )
    return SignalsListResponse(signals=signals, total=len(signals))


@app.get("/api/signals/market", response_model=MarketSignalsResponse)
@app.get("/api/market-signals", response_model=MarketSignalsResponse)
async def list_market_signals(
    limit: int = Query(50, ge=1, le=200),
):
    """Live signals from Binance 24h ticker (no API key). Use this when Discord/DB are not set up."""
    rows = await get_market_signals(limit=limit)
    signals = [
        MarketSignalItem(
            id=f"market-{r['symbol']}",
            symbol=r["symbol"],
            direction=r["direction"],
            entry_price=r.get("entry_price"),
            price_change_pct=r.get("price_change_pct"),
            confidence_pct=r.get("confidence_pct"),
            raw_text_preview=r.get("raw_text_preview", ""),
            signal_timestamp=r.get("signal_timestamp"),
            parse_method=r.get("parse_method", "market_api"),
            source=r.get("source", "binance"),
        )
        for r in rows
    ]
    return MarketSignalsResponse(signals=signals, total=len(signals), source="binance")


@app.get("/api/sources", response_model=SourcesResponse)
async def list_sources(db: AsyncSession = Depends(get_db)):
    """Signal counts by source (discord, tradingview, etc.). Market (binance) is live-only."""
    try:
        sub = select(ParsedSignal.source, func.count().label("count")).group_by(ParsedSignal.source)
        result = await db.execute(sub)
        rows = result.all()
        sources = [
            SourceCount(source=r.source.value if hasattr(r.source, "value") else str(r.source), count=r.count)
            for r in rows
        ]
        return SourcesResponse(sources=sources)
    except Exception as e:
        logger.warning("List sources failed: %s", e)
        return SourcesResponse(sources=[])


def _exec_to_item(e: Execution) -> ExecutionItem:
    return ExecutionItem(
        id=str(e.id),
        symbol=e.symbol,
        direction=e.direction.value if hasattr(e.direction, "value") else str(e.direction),
        side=e.side,
        quantity=float(e.quantity) if e.quantity is not None else None,
        price=float(e.price) if e.price is not None else None,
        notional_usd=float(e.notional_usd) if e.notional_usd is not None else None,
        status=e.status.value if hasattr(e.status, "value") else str(e.status),
        broker=e.broker.value if hasattr(e.broker, "value") else str(e.broker),
        executed_at=e.executed_at,
        created_at=e.created_at,
        notes=e.notes,
    )


@app.get("/api/executions", response_model=ExecutionsListResponse)
async def list_executions(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    symbol: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """List trade executions (paper or live)."""
    try:
        q = select(Execution).order_by(desc(Execution.created_at))
        count_q = select(func.count()).select_from(Execution)
        if symbol:
            q = q.where(Execution.symbol == symbol.upper())
            count_q = count_q.where(Execution.symbol == symbol.upper())
        if status and status.lower() in ("pending", "filled", "cancelled", "failed"):
            st = ExecutionStatusEnum(status.lower())
            q = q.where(Execution.status == st)
            count_q = count_q.where(Execution.status == st)

        total = (await db.execute(count_q)).scalar() or 0
        q = q.offset(offset).limit(limit)
        result = await db.execute(q)
        rows = result.scalars().all()
        return ExecutionsListResponse(executions=[_exec_to_item(e) for e in rows], total=total)
    except Exception as e:
        logger.warning("List executions failed: %s", e)
        return ExecutionsListResponse(executions=[], total=0)


@app.post("/api/executions", response_model=ExecutionItem)
async def create_execution(
    body: CreateExecutionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Record a paper execution (no real order). For live, broker integration would be required."""
    try:
        direction = (
            DirectionEnum.long
            if (body.direction or "").lower() == "long"
            else DirectionEnum.short
        )
        side = "buy" if direction == DirectionEnum.long else "sell"
        price = body.price
        quantity = body.quantity
        notional_usd = body.notional_usd
        if quantity is None and notional_usd is not None and price is not None and price > 0:
            quantity = notional_usd / price
        elif quantity is None:
            quantity = 0.0
        if notional_usd is None and price is not None and quantity is not None:
            notional_usd = float(price * quantity)
        now = datetime.now(timezone.utc)
        exec_row = Execution(
            symbol=body.symbol.upper(),
            direction=direction,
            side=side,
            quantity=quantity,
            price=price,
            notional_usd=notional_usd,
            status=ExecutionStatusEnum.filled,
            broker=BrokerEnum.paper,
            executed_at=now,
            notes=body.notes or "Paper execution from dashboard",
        )
        db.add(exec_row)
        await db.flush()
        await db.refresh(exec_row)
        return _exec_to_item(exec_row)
    except Exception as e:
        logger.exception("Create execution failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


# ---------------------------------------------------------------------------
# Alpaca: connect & execute
# ---------------------------------------------------------------------------

@app.get("/api/alpaca/account", response_model=AlpacaAccountResponse)
async def alpaca_account():
    """Alpaca account info if configured."""
    configured = await is_alpaca_configured()
    if not configured:
        return AlpacaAccountResponse(configured=False)
    acc = await alpaca_get_account()
    if not acc:
        return AlpacaAccountResponse(configured=True)
    try:
        env = get_settings().alpaca.environment
    except Exception:
        env = "paper"
    return AlpacaAccountResponse(
        configured=True,
        environment=env,
        buying_power=acc.get("buying_power"),
        cash=acc.get("cash"),
        portfolio_value=acc.get("portfolio_value"),
    )


@app.get("/api/alpaca/suggestions", response_model=AlpacaSuggestionsResponse)
async def alpaca_suggestions(
    db: AsyncSession = Depends(get_db),
    limit: int = Query(30, ge=1, le=100),
):
    """Scan stock signals and suggest trades for Alpaca. Fetches current price when Alpaca is configured."""
    configured = await is_alpaca_configured()
    suggestions: list[AlpacaSuggestionItem] = []
    try:
        q = (
            select(ParsedSignal)
            .where(ParsedSignal.asset_type == AssetTypeEnum.stock)
            .where(ParsedSignal.is_actionable == True)
            .order_by(desc(ParsedSignal.parsed_at))
            .limit(limit)
        )
        result = await db.execute(q)
        signals = result.scalars().all()
    except Exception as e:
        logger.warning("Alpaca suggestions query failed: %s", e)
        return AlpacaSuggestionsResponse(suggestions=[], total=0, alpaca_configured=configured)

    try:
        max_notional = get_settings().alpaca.max_notional_per_trade_usd
    except Exception:
        max_notional = 500.0

    for s in signals:
        symbol = (s.symbol or "").upper()
        if not symbol:
            continue
        entry = float(s.entry_price) if s.entry_price is not None else None
        current: Optional[float] = None
        if configured:
            current = await alpaca_get_price(symbol)
        price = current if current is not None else entry
        if price is None or price <= 0:
            suggested_notional = max_notional
            suggested_qty = None
        else:
            suggested_notional = min(max_notional, max(10.0, price * 10))
            suggested_qty = suggested_notional / price
        confidence = s.signal_completeness_pct
        if confidence is None and s.llm_confidence is not None:
            confidence = int(round(float(s.llm_confidence) * 100))
        suggestions.append(
            AlpacaSuggestionItem(
                signal_id=str(s.id),
                symbol=symbol,
                direction=s.direction.value if hasattr(s.direction, "value") else str(s.direction),
                entry_price=entry,
                current_price=current,
                suggested_notional_usd=round(suggested_notional, 2),
                suggested_qty=round(suggested_qty, 6) if suggested_qty is not None else None,
                source=s.source.value if hasattr(s.source, "value") else str(s.source),
                confidence_pct=confidence,
                stop_loss=float(s.stop_loss) if s.stop_loss is not None else None,
                take_profit_1=float(s.take_profit_1) if s.take_profit_1 is not None else None,
            )
        )
    return AlpacaSuggestionsResponse(
        suggestions=suggestions,
        total=len(suggestions),
        alpaca_configured=configured,
    )


@app.post("/api/alpaca/execute", response_model=ExecutionItem)
async def alpaca_execute(
    body: AlpacaExecuteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Execute a trade on Alpaca and record it as an execution."""
    if not await is_alpaca_configured():
        raise HTTPException(status_code=503, detail="Alpaca not configured. Set ALPACA_API_KEY and ALPACA_API_SECRET.")
    symbol = body.symbol.upper()
    side = "buy" if (body.direction or "").lower() == "long" else "sell"
    qty = body.quantity
    notional = body.notional_usd
    if (qty is None or qty <= 0) and (notional is None or notional <= 0):
        raise HTTPException(status_code=400, detail="Provide quantity or notional_usd.")
    order = await alpaca_place_order(symbol=symbol, side=side, qty=qty, notional=notional)
    if not order:
        raise HTTPException(status_code=502, detail="Alpaca order failed. Check symbol and size.")
    order_id = order.get("id")
    order_status = order.get("status") or "new"
    filled_qty = order.get("filled_qty")
    filled_avg_price = order.get("filled_avg_price")
    # Prefer filled values; fallback to request
    try:
        qty_num = float(filled_qty) if filled_qty is not None else (float(body.quantity) if body.quantity else 0)
    except (TypeError, ValueError):
        qty_num = 0
    if qty_num <= 0 and body.notional_usd and filled_avg_price:
        qty_num = float(body.notional_usd) / float(filled_avg_price)
    if qty_num <= 0 and body.quantity:
        qty_num = float(body.quantity)
    if qty_num <= 0 and body.notional_usd:
        try:
            p = float(filled_avg_price) if filled_avg_price else None
            if p and p > 0:
                qty_num = float(body.notional_usd) / p
        except (TypeError, ValueError):
            pass
    price_num = float(filled_avg_price) if filled_avg_price is not None else None
    notional_num = (float(qty_num * price_num) if price_num else None) or (float(body.notional_usd) if body.notional_usd else None)
    status_enum = ExecutionStatusEnum.filled if order_status == "filled" else (ExecutionStatusEnum.cancelled if order_status in ("canceled", "cancelled") else ExecutionStatusEnum.pending)
    now = datetime.now(timezone.utc)
    exec_row = Execution(
        symbol=symbol,
        direction=DirectionEnum.long if side == "buy" else DirectionEnum.short,
        side=side,
        quantity=qty_num,
        price=price_num,
        notional_usd=notional_num,
        status=status_enum,
        broker=BrokerEnum.alpaca,
        external_order_id=str(order_id) if order_id else None,
        executed_at=now,
        notes=body.signal_id and f"Signal {body.signal_id}" or "Alpaca execute",
    )
    db.add(exec_row)
    await db.flush()
    await db.refresh(exec_row)
    return _exec_to_item(exec_row)


# ---------------------------------------------------------------------------
# Research: trusted sources + confidence
# ---------------------------------------------------------------------------

@app.get("/api/research/confidence", response_model=ResearchConfidenceResponse)
async def research_confidence(
    symbol: str = Query(..., min_length=1),
    direction: str = Query("long"),
    signal_summary: Optional[str] = Query(None),
):
    """Research a symbol from trusted finance sources (e.g. Finnhub) and return a confidence level (0-100) for the trade."""
    out = await get_research_confidence(symbol.strip().upper(), direction.strip(), signal_summary)
    return ResearchConfidenceResponse(
        confidence_pct=out["confidence_pct"],
        rationale=out["rationale"],
        sources=out["sources"],
    )


# ---------------------------------------------------------------------------
# Advice: one recommendation per commodity (LONG/SHORT + target + stop loss)
# ---------------------------------------------------------------------------

@app.get("/api/advice", response_model=AdviceListResponse)
async def list_advice(db: AsyncSession = Depends(get_db)):
    """One advice per tracked commodity. Aggregates DB, Binance, CoinGecko, Finnhub; always returns target_price and stop_loss."""
    try:
        items = await get_all_advice(db)
        return AdviceListResponse(
            advice=[
                AdviceItemResponse(
                    symbol=a.symbol,
                    direction=a.direction,
                    entry_price=a.entry_price,
                    stop_loss=a.stop_loss,
                    target_price=a.target_price,
                    confidence_pct=a.confidence_pct,
                    rationale=a.rationale,
                    sources_used=a.sources_used,
                )
                for a in items
            ],
            total=len(items),
        )
    except Exception as e:
        logger.warning("List advice failed: %s", e)
        return AdviceListResponse(advice=[], total=0)


@app.get("/api/advice/{symbol}", response_model=AdviceItemResponse)
async def get_advice(symbol: str, db: AsyncSession = Depends(get_db)):
    """Single advice for one symbol: LONG or SHORT with target_price and stop_loss always set."""
    try:
        a = await get_advice_for_symbol(symbol.strip().upper(), db)
        return AdviceItemResponse(
            symbol=a.symbol,
            direction=a.direction,
            entry_price=a.entry_price,
            stop_loss=a.stop_loss,
            target_price=a.target_price,
            confidence_pct=a.confidence_pct,
            rationale=a.rationale,
            sources_used=a.sources_used,
        )
    except Exception as e:
        logger.warning("Advice for %s failed: %s", symbol, e)
        raise HTTPException(status_code=404, detail=f"Advice for symbol {symbol} failed.")
