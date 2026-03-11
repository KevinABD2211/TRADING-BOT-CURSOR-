# Trading Assistant

Discord-based trading signal ingestion and parsing: raw messages → signal detection → regex/LLM parsing → PostgreSQL. **Full platform:** FastAPI backend + Next.js dashboard (Vercel-ready). **Alternative to Discord:** use **Market (Live)** — Binance 24h top movers, no API key or DB required.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy files\.env.example .env
# Edit .env: DB_*, DISCORD_*, SECRET_KEY, optional LLM keys
```

## Run Discord bot

```bash
python -m app.services.discord_ingestor.bot
```

Requires: `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID`, `DISCORD_CHANNEL_ID`, Message Content Intent enabled in Discord Developer Portal.

## Run API (backend)

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Endpoints: `GET /health`, `GET /api/stats`, `GET /api/signals` (from DB), **`GET /api/signals/market`** (live Binance 24h movers, no key). Needs `.env` with at least `SECRET_KEY` and DB vars for DB routes; market route works without DB.

## Run frontend (dashboard)

```bash
cd frontend
copy .env.example .env.local   # Windows
# Or: cp .env.example .env.local
# Set NEXT_PUBLIC_API_URL=http://localhost:8000 (or your API URL)
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). **Signals** page: use **Unified (DB + Market)** to always see data; click **Load demo signals** to add sample stock/crypto signals. **Trades (Alpaca)** page: use **Research** to get a confidence level from trusted sources (Finnhub + optional LLM).

## Research & confidence

Set `RESEARCH_FINNHUB_API_KEY` (free at [finnhub.io](https://finnhub.io)) so the bot can pull company news and assign a **confidence level** to trades. On the Trades page, click **Research** next to a suggestion to run research and see confidence (0–100) and rationale.

## Push to GitHub

After making changes, push to keep the repo up to date:

```powershell
.\scripts\push.ps1 "Your commit message"
```

Or: `git add -A` → `git commit -m "..."` → `git push origin main`. See `.cursor/rules/push-to-github.mdc`.

## Deploy frontend to Vercel

1. Import the repo in Vercel; set **Root Directory** to `frontend`.
2. Add env var: `NEXT_PUBLIC_API_URL` = your backend API URL (e.g. where the FastAPI app is hosted).
3. Deploy. The dashboard will call your API from the browser.

## Run tests

```bash
pytest
```

## Project layout

- `app/` — main package: config, database, models, services (signal_detector, signal_parser, discord_ingestor), utils
- `app/services/market_signal_service.py` — Binance 24h ticker → live market signals (no API key)
- `app/services/research_service.py` — Finnhub news + optional LLM → research confidence for trades
- `app/services/alpaca_service.py` — Alpaca account, quotes, order execution
- `frontend/` — Next.js app (App Router): dashboard, signals list, **Market (Live)** page, API client
- `main.py` — FastAPI app (health, stats, signals)
- `files/` — original source files (reference)
- `tests/unit/` — pytest unit tests
