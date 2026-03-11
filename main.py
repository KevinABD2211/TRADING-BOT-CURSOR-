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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

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
