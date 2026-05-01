# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **FastAPI-based automated intraday trading system** for Nifty 50 options, integrating with the Zerodha Kite Connect API. It supports paper trading (simulation) and live trading modes.

## Running the Application

```bash
pip install -r requirements.txt
python main.py
```

Or directly with uvicorn:
```bash
uvicorn main:app --reload
```

The server runs on `http://127.0.0.1:8000`. The embedded dashboard is served at `GET /`.

## Environment Configuration

Copy `.env.example` to `.env` and populate:
- `API_KEY` / `API_SECRET` — Zerodha Kite Connect credentials
- `REDIRECT_URL` — OAuth callback URL (default: `http://127.0.0.1:8000/callback`)
- `TRADING_MODE` — `PAPER` (simulated) or `LIVE` (real orders)

## Architecture

### Data Flow

```
Zerodha OAuth → kite_service.py (stores access_token)
    → market_data.py (WebSocket ticks → 5-min OHLC candles)
    → indicators.py (EMA-20, VWAP, RSI-14)
    → strategy.py (BUY_CE / BUY_PE / NO_SIGNAL)
    → risk_manager.py (SL/target gates)
    → order_service.py (LIVE) or paper_trade.py (PAPER, logs to paper_trades.csv)
```

### Key Services (`services/`)

| File | Responsibility |
|---|---|
| `kite_service.py` | Zerodha API client singleton |
| `trading_state.py` | Thread-safe global state (`TradingState`, `Candle`, `PositionInfo` dataclasses) |
| `market_data.py` | KiteTicker WebSocket → 5-min candle aggregation |
| `indicators.py` | Pure functions: EMA, VWAP, RSI (match TradingView) |
| `strategy.py` | Entry/exit signal logic (VWAP+EMA breakout) |
| `strategy_engine.py` | Main trading loop orchestration |
| `risk_manager.py` | SL (20%), target (35%), trailing SL (activates at +20%), breakeven at +15% |
| `order_service.py` | Order placement and fill tracking |
| `paper_trade.py` | CSV simulation logging with P&L stats |
| `instruments.py` | Option chain lookup, ATM strike selection |

### Routers (`routers/`)

- `auth.py` — Zerodha OAuth login/callback/logout
- `trading.py` — Portfolio endpoints (profile, holdings, positions, orders)
- `auto_trading.py` — Engine start/stop/status, paper log access

### State Management

`trading_state.py` is a thread-safe singleton protected by `threading.Lock`. All modules access shared state through `get_state()`. Do not bypass the lock when reading/writing state.

## Trading Strategy Details

- **Strategy:** NIFTY_INTRADAY_VWAP_EMA_BREAKOUT v2 (optimised)
- **Active hours:** 9:50 AM – 2:00 PM IST (entries); force-exit at 3:20 PM
- **Max trades/day:** 2
- **Entry:** All of — price above/below VWAP (≥0.15% distance), EMA-20 directional confirmation, strong candle body, breakout to new high/low, 2/3 candle directional confirmation, RSI filter (CE: >50, PE: <50), volume surge ≥1.2× avg, not a spike candle, trend efficiency ≥45%
- **Exit:** Target +35%, hard SL –20%, trailing SL at +20% (trails 10% below peak), breakeven at +15%

## Important Notes

- The embedded HTML/CSS/JS dashboard lives entirely in `main.py` — no separate frontend build step.
- `paper_trades.csv` is written to the project root during paper trading sessions.
- There are no automated tests in this codebase.
- This is a single-user application; authentication state is stored in-memory (lost on restart).
