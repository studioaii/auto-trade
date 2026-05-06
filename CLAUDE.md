# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**FastAPI-based automated intraday trading system** for NIFTY 50 and BANKNIFTY options, integrating with the Zerodha Kite Connect API. Supports paper trading (simulation) and live trading modes. Both instruments run simultaneously on a shared WebSocket.

## Running the Application

```bash
pip install -r requirements.txt
python main.py
```

Or directly with uvicorn:
```bash
uvicorn main:app --reload
```

Server: `http://127.0.0.1:8000`. The Vue.js dashboard is served from `frontend/dist/` (falls back gracefully if not built). API routes take priority over the SPA mount.

## Environment Configuration

Copy `.env.example` to `.env` and populate:
- `API_KEY` / `API_SECRET` — Zerodha Kite Connect credentials
- `REDIRECT_URL` — OAuth callback URL (default: `http://127.0.0.1:8000/callback`)
- `TRADING_MODE` — `PAPER` (simulated) or `LIVE` (real orders)

## Architecture

### Data Flow

```
Zerodha OAuth → kite_service.py (stores access_token)
    → daily_scheduler.py (auto-starts engines at 09:15, stops at 15:35)
    → strategy_engine.py (TradingEngine × 2: NIFTY + BANKNIFTY)
        → instruments.py (fetch option chain, ATM strike, futures token)
        → market_data.py (shared KiteTicker WebSocket → 5-min OHLC candles)
        → indicators.py (EMA-20, VWAP, RSI-14, efficiency ratio)
        → strategy.py (BUY_CE / BUY_PE / NO_SIGNAL)
        → risk_manager.py (SL/target/trailing gates)
        → order_service.py (LIVE) or paper_trade.py (PAPER)
        → candle_logger.py (CSV per instrument per day)
        → entry_logger.py (CSV of blocked entry attempts)
```

### Key Services (`services/`)

| File | Responsibility |
|---|---|
| `kite_service.py` | Zerodha API client singleton |
| `trading_state.py` | Thread-safe per-instrument state (`TradingState`, `Candle`, `PositionInfo` dataclasses; `InstrumentStateManager`) |
| `market_data.py` | Single shared KiteTicker WebSocket → 5-min candle aggregation; `ConnectionMonitor`; candle backfill |
| `indicators.py` | Pure functions: EMA-20, VWAP (intraday cumulative), RSI-14, efficiency ratio, volume surge, spike/body detection |
| `strategy.py` | Entry/exit signal logic (VWAP+EMA breakout); sideways detection; opposite-signal exit |
| `strategy_engine.py` | `TradingEngine` orchestration (one per instrument); ATM hysteresis; session candle preload |
| `risk_manager.py` | SL (–20%), trailing SL (activates at +15%, dynamic gap), force-exit at 15:20 |
| `order_service.py` | Live order placement and fill tracking |
| `paper_trade.py` | CSV simulation logging with P&L stats |
| `instruments.py` | Option chain fetch, ATM strike rounding, futures lookup; daily-cached NFO instrument list |
| `daily_scheduler.py` | Background daemon: auto-start 09:15, auto-stop 15:35 |
| `candle_logger.py` | Async queue-based CSV writer — one file per instrument per day |
| `entry_logger.py` | CSV log of every signal that fired but was blocked by an entry gate |

### Routers (`routers/`)

- `auth.py` — `GET /login`, `GET /callback`, `GET /logout`
- `trading.py` — `GET /profile`, `/holdings`, `/positions`, `/orders`
- `auto_trading.py` — prefix `/auto-trading` — start/stop/status per instrument, paper-log, candle-log download (19 endpoints)

### State Management

Each instrument has its own `InstrumentStateManager` (owns a `TradingState` + `threading.Lock`). Never bypass the lock when reading or writing state. Use `get_raw_state()` only while holding the lock; use `get_state()` for snapshot reads.

### Infrastructure

- **Logging:** `trading.log` — rotating (10 MB × 5 files) + stdout
- **CSRF protection:** POST start/stop endpoints validated against allowed origins
- **Status cache:** 1-second TTL on status endpoints to avoid Kite rate-limit hits
- **DailyScheduler** starts automatically on FastAPI lifespan startup

---

## Instrument Configuration (`config.py`)

| Key | NIFTY | BANKNIFTY |
|---|---|---|
| `index_token` | 256265 | 260105 |
| `strike_interval` | 50 | 100 |
| `lot_size` | 65 | 30 |
| `ltp_symbol` | `NSE:NIFTY 50` | `NSE:NIFTY BANK` |
| `rsi_min_ce / max_ce` | 50 / 100 | 50 / 100 |
| `rsi_min_pe / max_pe` | 0 / 50 | 0 / 50 |
| `vwap_dist_min_pct` | 0.15 | 0.15 |
| `efficiency_min_ce/pe` | 0.45 | 0.45 |

---

## Trading Strategy — VWAP+EMA Breakout v2

### Time Windows

| Event | Time (IST) |
|---|---|
| Engine auto-start | 09:15 (DailyScheduler) |
| Earliest entry | 09:50 |
| Latest entry | 14:00 |
| Force exit | 15:20 |
| Engine auto-stop | 15:35 (DailyScheduler) |

### Entry Conditions (ALL must be true)

**CE (Call) entry:**
1. `close > VWAP` with distance ≥ 0.15%
2. EMA-20 trending up (last two values rising)
3. EMA-20 strong up slope: rose ≥ 8.0 pts over last 5 candles
4. Strong bullish candle: `close > open` AND body ≥ 55% of range
5. Breakout: `high > previous high`
6. Multi-candle: 2 of last 3 candles bullish (`close > open`)
7. RSI-14 > 50
8. Efficiency ratio ≥ 0.45 (over last 10 candles)
9. Volume surge ≥ 1.2× average of prior 10 candles
10. NOT a spike candle (range ≤ 1% of close)
11. Market state is TRENDING (not SIDEWAYS)

**PE (Put) entry:** mirror of CE with all conditions inverted.

### SIDEWAYS Detection (blocks all entries)

- Efficiency ratio < 0.40 over last 10 candles
- OR ≥ 2 VWAP crossings in last 5 candles

### Exit Conditions

| Trigger | Level |
|---|---|
| Hard stop-loss | –20% from entry |
| Trailing SL activates | profit ≥ +15% |
| Trail gap at activation | 6% below peak |
| Trail gap tightening | –1% per additional +10% gain |
| Trail gap floor | 3% |
| Opposite signal exit | reverse breakout on open position |
| Time exit | 15:20 IST force-close |

**Second-trade block:** if the first trade of the day hit the hard SL, the second entry is blocked for the rest of the session (`first_trade_was_sl` flag).

### ATM Strike Management

- Dynamic ATM reselection on each candle close (while no position open)
- Hysteresis: only swap when spot moves ≥ 40% of one strike interval past current ATM
  - NIFTY: ≥ 20 pts, BANKNIFTY: ≥ 40 pts
- After swap: old option tokens unsubscribed, new tokens subscribed; `ce_ltp` / `pe_ltp` reset to 0 until first tick arrives

---

## Indicators (`indicators.py`)

| Indicator | Details |
|---|---|
| **EMA-20** | `k = 2/(20+1)`; seeded with SMA of first 20 values; first 19 entries `None` |
| **RSI-14** | Wilder's method; first 14 entries `None` |
| **VWAP** | Intraday cumulative: `Σ(typical_price × vol) / Σ(vol)`; resets each session; previous-day seed candles excluded |
| **Efficiency ratio** | `|net_close_move| / (max_high − min_low)` over last 10 candles |
| **Volume surge** | current vol ≥ 1.2× avg of prior 10 |
| **Candle body** | threshold: 55% of range |
| **Spike candle** | range > 1% of close |
| **MIN_CANDLES** | 22 (20 for EMA + 2 for slope detection) |

---

## WebSocket & Market Data (`market_data.py`)

- **Single `KiteTicker`** shared across NIFTY and BANKNIFTY engines
- Index/Futures tokens: `MODE_FULL`; Option tokens: `MODE_LTP`
- **ConnectionMonitor** (persistent daemon thread):
  - Check interval: 15 s
  - Grace period before forced reconnect: 45 s
  - Tick stall threshold (market hours only): 60 s
- **KiteTicker settings:** `reconnect_max_tries=300`, `reconnect_max_delay=30 s`, `connect_timeout=30 s`
- **Candle backfill on reconnect:** fetches missed candles via Zerodha REST historical API
- **Volume:** cumulative daily volume converted to per-tick delta; first tick after start sets baseline (contributes 0)

---

## Output Files

| File | Contents |
|---|---|
| `candle_logs/nifty_candles_YYYY-MM-DD.csv` | Per-candle OHLCV + all indicators + signal + position snapshot (36 columns) |
| `candle_logs/banknifty_candles_YYYY-MM-DD.csv` | Same schema for BANKNIFTY |
| `entry_attempts_nifty.csv` | Every signal that fired but was blocked (12 columns: signal, spot, ATM, LTP, VWAP dist, RSI, body, market state, skip reason, computed SL %) |
| `entry_attempts_banknifty.csv` | Same for BANKNIFTY |
| `paper_trades_nifty.csv` | Completed paper trades with full indicator snapshot at entry (26 columns) |
| `paper_trades_banknifty.csv` | Same for BANKNIFTY |
| `trading.log` | Rotating application log (10 MB × 5 files) |

---

## Important Notes

- **No automated tests** in this codebase — verify changes by reading logic carefully.
- **Frontend:** Vue.js SPA built separately into `frontend/dist/`; not embedded in `main.py`.
- **Authentication state** is stored in-memory (lost on server restart — re-login required).
- **Single-user application** — no multi-user auth layer.
- State shared between threads lives in `InstrumentStateManager` objects; always use the lock.
- Do not add a `_check_websocket` watchdog back to `DailyScheduler` — `ConnectionMonitor` in `market_data.py` is the sole reconnection driver.
