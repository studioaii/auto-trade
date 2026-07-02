# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**FastAPI-based automated intraday trading system** for NIFTY 50 options, integrating with the Zerodha Kite Connect API. Two engines run simultaneously on a shared WebSocket:

- **NIFTY v1** (`strategy_engine.py`) — the baseline VWAP+EMA breakout engine. Supports `PAPER` and `LIVE` modes via `TRADING_MODE`.
- **NIFTY v2** (`nifty_engine_v2.py`) — v1's strategy plus analysis-driven improvement gates and full instrumentation. **Hard-locked to paper mode** (`force_paper_mode`); it exists to forward-test changes against the untouched v1 baseline.

BANKNIFTY (v1 + v2) and NIFTY-futures engines were removed on 2026-07-02 in preparation for live trading (code recoverable from git history; their data CSVs live in `archive/removed_banknifty_fut_2026-07-02/`).

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
    → daily_scheduler.py (auto-starts both engines at 09:15, stops at 15:35)
    → strategy_engine.py (NIFTY v1) + nifty_engine_v2.py (NIFTY v2)
        → instruments.py (fetch option chain, ATM strike, futures token)
        → market_data.py (shared KiteTicker WebSocket → 5-min OHLC candles)
        → indicators.py (EMA-20, VWAP, RSI-14, efficiency ratio)
        → strategy.py / nifty_strategy_v2.py (BUY_CE / BUY_PE / NO_SIGNAL)
        → risk_manager.py / nifty_risk_manager_v2.py (SL/trailing gates)
        → order_service.py (LIVE, v1 only) or paper_trade.py / nifty_paper_trade_v2.py
        → candle_logger.py / nifty_candle_logger_v2.py (CSV per engine per day)
        → entry_logger.py / nifty_entry_logger_v2.py (blocked entry attempts)
        → nifty_instrumentation_v2.py (v2 only: shadow signals + post-exit paths)
```

### Key Services (`services/`)

| File | Responsibility |
|---|---|
| `kite_service.py` | Zerodha API client singleton |
| `trading_state.py` | Thread-safe per-instrument state (`TradingState`, `Candle`, `PositionInfo` dataclasses; `InstrumentStateManager`) |
| `market_data.py` | Single shared KiteTicker WebSocket → 5-min candle aggregation; `ConnectionMonitor`; candle backfill |
| `indicators.py` | Pure functions: EMA-20, VWAP (intraday cumulative), RSI-14, efficiency ratio, volume surge, spike/body detection |
| `strategy.py` | v1 entry/exit signal logic (VWAP+EMA breakout); sideways detection; opposite-signal exit |
| `strategy_engine.py` | v1 `TradingEngine` orchestration; ATM hysteresis; session candle preload |
| `risk_manager.py` | v1 SL (–18%), trailing SL (activates at +15%, dynamic gap), force-exit at 15:20 |
| `order_service.py` | Live order placement and fill tracking (v1 LIVE mode only) |
| `paper_trade.py` | v1 CSV simulation logging with P&L stats |
| `instruments.py` | Option chain fetch, ATM strike rounding, futures lookup; daily-cached NFO instrument list |
| `daily_scheduler.py` | Background daemon: auto-start 09:15, auto-stop 15:35 (both engines) |
| `candle_logger.py` | Async queue-based CSV writer — one file per day |
| `entry_logger.py` | CSV log of every signal that fired but was blocked by an entry gate |
| `nifty_engine_v2.py` | v2 engine — v1 orchestration + shadow/post-exit path trackers |
| `nifty_strategy_v2.py` | v2 strategy — v1 breakout + regime gate (close-confirmed break, recent-window chop, session-cumulative chop gate) |
| `nifty_risk_manager_v2.py` | v2 risk — v1 profile (–18% SL, +15% trail) + tick MAE/MFE tracking; entry window 09:50–14:00 |
| `nifty_paper_trade_v2.py` / `nifty_candle_logger_v2.py` / `nifty_entry_logger_v2.py` | v2 CSV writers |
| `nifty_instrumentation_v2.py` | v2 shadow-signal (would-be P&L of gate-blocked entries) + post-exit option-path CSVs |

### Routers (`routers/`)

- `auth.py` — `GET /login`, `GET /callback`, `GET /logout`
- `trading.py` — `GET /profile`, `/holdings`, `/positions`, `/orders`
- `auto_trading.py` — prefix `/auto-trading` — v1 start/stop/status, paper-log, candle-log download, plus `/start-all` & `/stop-all` (v1)
- `auto_trading_nifty2.py` — prefix `/auto-trading/nifty2` — v2 start/stop/status, paper/candle logs, chart candles, instrumentation downloads

### State Management

Each instrument has its own `InstrumentStateManager` (owns a `TradingState` + `threading.Lock`). Never bypass the lock when reading or writing state. Use `get_raw_state()` only while holding the lock; use `get_state()` for snapshot reads.

### Infrastructure

- **Logging:** `trading.log` — rotating (10 MB × 5 files) + stdout
- **CSRF protection:** POST start/stop endpoints validated against allowed origins
- **Status cache:** 1-second TTL on status endpoints to avoid Kite rate-limit hits
- **DailyScheduler** starts automatically on FastAPI lifespan startup

---

## Instrument Configuration (`config.py`)

Two blocks in `INSTRUMENT_CONFIG`: `NIFTY` (v1) and `NIFTY_2` (v2). Shared identity/thresholds:

| Key | NIFTY / NIFTY_2 |
|---|---|
| `index_token` | 256265 |
| `strike_interval` | 50 |
| `lot_size` | 65 |
| `ltp_symbol` | `NSE:NIFTY 50` |
| `rsi_min_ce / max_ce` | 50 / 100 |
| `rsi_min_pe / max_pe` | 0 / 50 |
| `vwap_dist_min_pct` | 0.15 |
| `efficiency_min_ce/pe` | 0.45 |

`NIFTY_2` additionally carries `force_paper_mode: True`, the regime-gate keys (`require_close_breakout`, `breakout_margin_pct`, `regime_max_vwap_crossings`, `session_max_vwap_crossings` — pre-registered at 6, do not retune in-sample), the softened opposite-exit (`opposite_exit_confirm_closes: 2`), and instrumentation settings. The config comments document the evidence behind each value — read them before changing anything.

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
| Hard stop-loss | –18% from entry (tightened from –20% per June-2026 analysis) |
| Trailing SL activates | profit ≥ +15% |
| Trail gap at activation | 6% below peak |
| Trail gap tightening | –1% per additional +10% gain |
| Trail gap floor | 3% |
| Opposite signal exit | reverse breakout on open position |
| Time exit | 15:20 IST force-close |

**Second-trade block:** if the first trade of the day hit the hard SL, the second entry is blocked for the rest of the session (`first_trade_was_sl` flag).

### v2-only additions (NIFTY 2.0)

- **Regime gate** on top of the base signal: (1) close-confirmed breakout — the close must clear the prior 3-candle swing by ≥0.02%; (2) recent-window chop guard — ≥2 VWAP crossings in last 5 candles blocks; (3) **session chop gate** — once today's closes have flipped sides of VWAP ≥6 times, all further entries are blocked for the day.
- **Softened opposite-signal exit** — requires 2 consecutive candle closes on the wrong side of VWAP (v1 exits on a single reverse breakout).
- **Instrumentation** — tick-resolution MAE/MFE per trade, post-exit option path (8 candles), and would-be P&L for every gate-blocked signal (`shadow_signals_nifty_2.csv`). Judge gate changes with this data, not in-sample backtests.

### ATM Strike Management

- Dynamic ATM reselection on each candle close (while no position open)
- Hysteresis: only swap when spot moves ≥ 40% of one strike interval past current ATM (NIFTY: ≥ 20 pts)
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

- **Single `KiteTicker`** shared across the v1 and v2 engines
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
| `candle_logs/nifty_candles_YYYY-MM-DD.csv` | v1 per-candle OHLCV + all indicators + signal + position snapshot (36 columns) |
| `candle_logs/nifty2_candles_YYYY-MM-DD.csv` | Same for v2 (plus v2 signal/model/skip columns) |
| `entry_attempts_nifty.csv` | v1: every signal that fired but was blocked (signal, spot, ATM, LTP, VWAP dist, RSI, body, market state, skip reason) |
| `entry_attempts_nifty_2.csv` | v2: every evaluated candle with outcome + skip reasons |
| `paper_trades_nifty.csv` | v1 completed paper trades with indicator snapshot at entry (26 columns) |
| `paper_trades_nifty_2.csv` | v2 completed paper trades — includes tick `mfe_pct` / `mae_pct` |
| `shadow_signals_nifty_2.csv` | v2: would-be P&L path of every gate-blocked signal (how gates are judged) |
| `post_exit_paths_nifty_2.csv` | v2: option max/min for 8 candles after each exit (trailing-stop efficiency) |
| `trading.log` | Rotating application log (10 MB × 5 files) |

Archived data from removed engines (BANKNIFTY v1/v2, NIFTY futures) lives under `archive/`.

---

## Important Notes

- **No automated tests** in this codebase — verify changes by reading logic carefully.
- **Frontend:** Vue.js SPA built separately into `frontend/dist/`; not embedded in `main.py`.
- **Authentication state** is stored in-memory (lost on server restart — re-login required).
- **Single-user application** — no multi-user auth layer.
- State shared between threads lives in `InstrumentStateManager` objects; always use the lock.
- Do not add a `_check_websocket` watchdog back to `DailyScheduler` — `ConnectionMonitor` in `market_data.py` is the sole reconnection driver.
