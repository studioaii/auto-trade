# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**FastAPI-based automated intraday options-trading system** for NIFTY 50 and BANKNIFTY, integrating with the Zerodha Kite Connect API. Supports paper trading (simulation) and live trading. Both instruments run simultaneously on a shared WebSocket.

**v3 design:** per-instrument strategy variants — NIFTY runs *trend-pullback* continuation, BANKNIFTY runs *mean-reversion fade-failed-spike*. Both share a v3 risk envelope (day-bias gate, spot-based SL, time stop, partial booking, India VIX gate, event-day calendar, 1 trade/day cap, 14:30 force-exit, 1-strike-ITM strikes). The legacy `vwap_ema_breakout` v2 strategy is retained behind a config flag for rollback.

## Running the Application

```bash
pip install -r requirements.txt
python3 -m uvicorn main:app --host 127.0.0.1 --port 8000
# (the codebase has no `if __name__ == "__main__"` block, so prefer uvicorn)
```

Server: `http://127.0.0.1:8000`. Vue dashboard served from `frontend/dist/` (build via `cd frontend && npm install && npm run build`). API routes take priority over the SPA mount.

## Environment Configuration

Copy `.env.example` to `.env` and populate:
- `API_KEY` / `API_SECRET` — Zerodha Kite Connect credentials
- `REDIRECT_URL` — OAuth callback URL (default: `http://127.0.0.1:8000/callback`)
- `TRADING_MODE` — `PAPER` (simulated) or `LIVE` (real orders)

`events.json` (repo root, hand-maintained) contains event-day skip dates (RBI policy, Fed, big-bank earnings, election results, budget, manual blocks).

## Architecture

### Data Flow

```
Zerodha OAuth → kite_service.py (stores access_token; transient-503 retries)
    → daily_scheduler.py (auto-starts engines at 09:15, stops at 15:35)
    → strategy_engine.py (TradingEngine × 2: NIFTY + BANKNIFTY)
        ├ instruments.py (option chain, ATM rounding, ITM-offset selection, futures)
        ├ market_data.py (shared KiteTicker → 5-min OHLCV; VIX meta-token routing)
        ├ events.py / events.json (event-day skip)
        ├ vix_state.py (India VIX LTP singleton)
        ├ day_bias.py (09:50 IST classifier → state.day_bias)
        ├ indicators.py (EMA-20, EMA-9, VWAP, RSI-14, efficiency, wick%, volume surge)
        ├ strategy.py (dispatcher → trend_pullback | mean_reversion | legacy)
        ├ risk_manager.py (cfg-driven gates: spot SL, time stop, partial book, trail)
        ├ order_service.py (LIVE: full + partial exit orders)
        ├ paper_trade.py (PAPER: leg-aware CSV with auto-rotation)
        ├ candle_logger.py (43-col CSV per instrument per day)
        └ entry_logger.py (CSV of blocked entry attempts)
```

### Key Services (`services/`)

| File | Responsibility |
|---|---|
| `kite_service.py` | Zerodha API client + persisted token. `generate_session()` retries on transient 503/504/HTML errors (`TransientKiteError`). |
| `trading_state.py` | Thread-safe per-instrument state. `TradingState`, `Candle`, `PositionInfo` dataclasses; `InstrumentStateManager`. v3 fields below. |
| `market_data.py` | Single `KiteTicker` shared across instruments. `subscribe_meta_token(token, callback)` registers LTP-only tokens (e.g., India VIX 264969). `ConnectionMonitor` is the sole reconnection driver. |
| `indicators.py` | Pure: EMA(period), VWAP (intraday, date-filtered), RSI-14, efficiency ratio, volume surge, body/wick %, spike detection. |
| `day_bias.py` | `classify_day_bias(today_candles, prev_close, cfg)` → `"UP" / "DOWN" / "NEUTRAL" / "NO_TRADE"`. Runs once per day at 09:50. |
| `events.py` | `is_event_day(date, instrument) → (bool, type)` from `events.json`. Cached. Empty file = no events. |
| `vix_state.py` | Singleton VIX LTP store. `set_vix_ltp(value)` / `get_vix_ltp()`. |
| `strategy.py` | Dispatcher `generate_signal(state, indicators, cfg, opening_rsi)` routes by `cfg["entry_mode"]` → `_generate_signal_legacy` / `generate_trend_pullback_signal` / `generate_mean_reversion_signal`. `detect_opposite_signal` is the legacy reverse-breakout exit detector. |
| `strategy_engine.py` | `TradingEngine` orchestration (one per instrument). Day-bias call at 09:50. ATM hysteresis in ATM-space (offset-aware). Full + partial exit paths. `_execute_partial_exit` does NOT clear the position; the final exit aggregates legs into one `paper_trades_*.csv` row. |
| `risk_manager.py` | Cfg-driven. `can_enter_trade(state, cfg, instrument) → (allowed, reason)`. `check_exit_conditions(position, state, cfg) → (action, reason, qty)` with tri-state action `NONE / FULL_EXIT / PARTIAL_EXIT`. |
| `order_service.py` | `place_entry_order`, `place_exit_order` (full), `place_partial_exit_order(qty, reason)`. All MARKET orders, MIS product. |
| `paper_trade.py` | CSV simulation. v3 schema (43 cols) auto-rotates legacy 26-col files to `paper_trades_*.legacy.csv` on first write after upgrade. `log_trade(legs=[...], day_bias, vix_at_entry, entry_mode)` accepts optional leg list; legacy aggregate columns hold weighted averages. |
| `instruments.py` | `get_atm_strike` and `get_strike_with_offset(spot, interval, offset)` (offset 0=ATM, -1=ITM-CE/OTM-PE, +1=OTM-CE/ITM-PE). Daily-cached NFO instrument list. |
| `daily_scheduler.py` | Background daemon: auto-start 09:15, auto-stop 15:35. No WebSocket watchdog (that lives in `market_data.py`). |
| `candle_logger.py` | Async queue-based CSV writer. 43-col schema (v3 — see below). |
| `entry_logger.py` | Free-text `skip_reason` for blocked entries. New v3 reasons: `EVENT_DAY:<TYPE>`, `VIX_HIGH:<n>`, `DAY_BIAS_NO_TRADE`, `BIAS_MISMATCH`, `NO_PULLBACK`, `NO_SPIKE`, `MAX_TRADES_HIT`. |

### Routers (`routers/`)

- `auth.py` — `GET /login`, `GET /callback` (transient-503 → redirect back to `/login`), `GET /logout`
- `trading.py` — `GET /profile`, `/holdings`, `/positions`, `/orders`
- `auto_trading.py` — prefix `/auto-trading` — start/stop/status per instrument, paper-log download, candle-log download, and the multi-instrument `start-all`/`stop-all`

### State Management

Each instrument has its own `InstrumentStateManager` (owns `TradingState` + `threading.Lock`). Never bypass the lock when reading or writing state. Use `get_raw_state()` only while holding the lock; use `get_state()` for snapshot reads. `get_state()` deep-copies `position` and `partial_legs` to prevent torn reads.

### Infrastructure

- **Logging:** `trading.log` — rotating (10 MB × 5 files) + stdout
- **CSRF protection:** POST start/stop endpoints validated against allowed origins (`main.py:69`)
- **Status cache:** 1-second TTL on status endpoints to avoid Kite rate-limit hits
- **DailyScheduler** starts automatically on FastAPI lifespan startup
- **VIX subscription** registered at lifespan startup; activates on first WebSocket connect

---

## Per-Instrument Configuration (`config.py`)

Every threshold is config-driven via `INSTRUMENT_CONFIG[<INSTRUMENT>]`. Defaults below match the **production v3 settings**. Flip `entry_mode` to `vwap_ema_breakout` for legacy rollback.

| Key | NIFTY | BANKNIFTY | Notes |
|---|---|---|---|
| `index_token` | 256265 | 260105 | NSE index tokens |
| `strike_interval` | 50 | 100 | option strike spacing |
| `lot_size` | 65 | 30 | weekly lot size |
| `entry_mode` | `trend_pullback` | `mean_reversion` | dispatcher key |
| `strike_offset_ce` | -1 | -1 | 1-strike ITM call |
| `strike_offset_pe` | +1 | +1 | 1-strike ITM put |
| `max_trades_per_day` | 1 | 1 | hard cap |
| `force_exit_time` | 14:30 | 14:30 | down from legacy 15:20 |
| `sl_spot_pct` | 0.25 | 0.35 | spot-distance SL |
| `sl_premium_pct` | 20.0 | 20.0 | safety-net premium SL |
| `time_stop_min` | 15 | 10 | no-progress exit |
| `partial_book_enabled` | true | true | leg-aware exits |
| `partial_book_1_pct` / `_size` | 7% / 0.50 | 7% / 0.50 | first leg |
| `partial_book_2_pct` / `_size` | 14% / 0.30 | 14% / 0.30 | second leg |
| `trail_gap_pct` | 4% | 4% | trail on remaining 20% |
| `min_lots_for_partial_book` | 3 | 3 | full-exit at L3 if qty too small |
| `hold_ceiling_min` | 180 | 90 | BNF reverts faster |
| `vix_max` | 22.0 | 22.0 | India VIX gate |
| `bias_gap_pct_no_trade` | 0.80% | 1.00% | gap above this → NO_TRADE |
| `bias_opening_rsi_ob` / `_os` | 78 / 22 | 78 / 22 | opening-RSI extremes block day |
| `bias_opening_efficiency_min` | 0.35 | 0.35 | first 35-min must be directional |
| `bias_rsi_min_up` / `max_up` | 55 / 73 | 55 / 73 | UP-bias RSI band |
| `bias_rsi_min_down` / `max_down` | 27 / 45 | 27 / 45 | DOWN-bias RSI band |
| `gap_pct_min` | 0.10% | 0.10% | min gap to claim direction |

**NIFTY-only (trend_pullback):** `vwap_hold_min_candles=6`, `pullback_retrace_pct=0.20`, `pullback_rsi_low/high=45/55`, `pullback_vol_max_ratio=0.85`, `resume_rsi_min/max_ce=55/70`, `resume_rsi_min/max_pe=30/45`, `resume_vwap_dist_min/max=0.20/0.80`, `resume_body_pct=50`, `resume_vol_surge_ratio=1.5`, `ema_gap_min/max=0.05/0.40`, `ema_period_secondary=9`, `range_anomaly_mult=1.5`, `spike_threshold_pct=0.60`.

**BNF-only (mean_reversion):** `bnf_spike_window_candles=3`, `bnf_spike_pct=0.60`, `bnf_spike_vol_surge=2.5`, `bnf_fade_rsi_overbought/oversold=70/30`, `fade_wick_min_pct=40`, `fade_body_min_pct=40`, `failed_reversion_max=2`.

**Legacy keys (read by `vwap_ema_breakout` only):** `rsi_min_ce/max_ce`, `rsi_min_pe/max_pe`, `vwap_dist_min_pct`, `efficiency_min_ce/pe`, `opening_rsi_overbought/oversold`. These are intentionally distinct from `bias_*` keys to prevent collision.

---

## Trading Strategies

### Time Windows (shared)

| Event | Time (IST) |
|---|---|
| Engine auto-start | 09:15 (DailyScheduler) |
| Day-bias classified | 09:50 (`_on_candle_ready` at first ≥09:50 candle) |
| Earliest entry | 09:50 |
| Latest entry | 14:00 |
| Force exit | 14:30 (cfg-driven; legacy 15:20) |
| Engine auto-stop | 15:35 (DailyScheduler) |

### Day-bias classifier

At 09:50 IST, on each instrument's first candle ≥09:50, classify the day:

- `gap_pct = (today_open - prev_close) / prev_close × 100`
- `opening_rsi`: RSI-14 at 09:45 candle close (warmed up by previous-day seed candles)
- `vwap_slope = sign(VWAP[09:50] - VWAP[09:25])`
- `opening_efficiency`: efficiency ratio over 7 candles (09:15-09:50)

**`NO_TRADE`** if any: `|gap_pct| ≥ bias_gap_pct_no_trade` / `opening_rsi ≥ 78` or `≤ 22` / `opening_efficiency < 0.35` / VIX > `vix_max` / today on event calendar.

**`UP`** = `gap_pct ≥ +0.10%` AND `opening_rsi ∈ [55, 73]` AND `vwap_slope > 0` AND spot > VWAP.
**`DOWN`** = mirror.
Otherwise → **`NEUTRAL`**.

NIFTY trades only on `UP` (CE) or `DOWN` (PE). BNF trades on `UP` / `DOWN` / `NEUTRAL` but never on `NO_TRADE`.

`prev_close` is captured from the last seed candle loaded by `_load_session_candles` at engine start.

### NIFTY — trend-pullback continuation

**Premise:** never enter on the breakout itself; wait for a controlled pullback into VWAP / 9-EMA, then enter on the resume bar.

Pre-conditions (each closed 5-min candle 09:50-14:00):
1. Day-bias matches direction (UP→CE only, DOWN→PE only)
2. Spot held above (UP) / below (DOWN) VWAP for ≥6 consecutive 5-min candles
3. 9-EMA trending in direction over last 3 candles
4. Not event day, VIX ≤ 22, NO_TRADE bias not set
5. `trades_today == 0`
6. Current and previous candle not spike candles (`range ≤ 0.6% of close`)

Pullback structure (most recent 1-3 candles against bias, within last 6):
1. Length 1-3 candles
2. Doesn't break today's session low (UP) / high (DOWN)
3. Pullback bottom within 0.20% of VWAP or 9-EMA (whichever closer)
4. RSI-14 at pullback bottom in `[45, 55]`
5. Pullback-window avg volume ≤ 0.85 × prior 5 trend candles' avg ("low volume on the dip")

Resume-candle entry trigger:
1. Direction-matching candle (green for CE)
2. Closes above pullback high (CE) / below pullback low (PE)
3. Volume ≥ 1.5 × avg of prior 10
4. Body ≥ 50% of range
5. RSI-14 ∈ `[55, 70]` (CE) — **70 cap is the upgrade missing in v2**
6. VWAP-distance after trigger ∈ `[0.20%, 0.80%]`
7. Price–9-EMA gap ∈ `[0.05%, 0.40%]`

Hard exclusions: range > 1.5× avg of last 20, NIFTY/BNF bias disagree (macro indecision).

Strike: 1-strike ITM (CE = ATM-50, PE = ATM+50).

### BANKNIFTY — mean-reversion / fade-failed-spike

**Premise:** BNF spikes hard on single-stock flow, then snaps back. Buy the *opposite-direction* option after a spike fails.

Pre-conditions:
1. Bias not `NO_TRADE`; VIX ≤ 22; not event day
2. `trades_today == 0`
3. `failed_reversion_attempts_today < 2` (sticky counter)
4. Time window 09:50-14:00

Spike detection (over last 3 contiguous 5-min candles = 15-min window):

**Bullish spike** (eligible for CE-fade → buy PE):
1. `(window_close - VWAP) / VWAP × 100 ≥ 0.60%`
2. Window low > VWAP (entire window above VWAP)
3. Volume on most-recent candle ≥ 2.5 × avg of prior 10
4. RSI-14 at window end ≥ 70

**Bearish spike** (eligible for PE-fade → buy CE): mirror — distance below VWAP ≥ 0.60%, window high < VWAP, RSI ≤ 30.

Failure-of-spike trigger (next candle):
- For CE-fade: upper wick ≥ 40% of range, bearish close, body ≥ 40%, trigger high ≤ spike-window high, RSI rolls down
- For PE-fade: mirror

Entry direction: **CE-fade → buy PE** (1 strike ITM = ATM+100). **PE-fade → buy CE** (ATM-100).

Spot SL: makes new high above spike high (CE-fade) / new low below spike low (PE-fade). Hard cap: 0.35% spot move.

Hold ceiling: 90 min.

### Universal exit ladder (both strategies)

Evaluated each candle close + each option LTP tick, in priority order (`risk_manager.check_exit_conditions`):

| Layer | Trigger | Action |
|---|---|---|
| L0 | Wall-clock ≥ `force_exit_time` (14:30 IST) | Full exit `TIME_EXIT` |
| L1 | Wall-clock ≥ entry + `hold_ceiling_min` | Full exit `HOLD_CEILING` |
| L2 | After `time_stop_min` AND `pnl_pct ≤ 0` | Full exit `TIME_STOP` |
| L3 | Spot moved ≥ `sl_spot_pct` against entry (CE: spot ≤ entry_spot × 0.9975 etc.) | Full exit `SPOT_SL_HIT`; sets `first_trade_was_sl` |
| L4 | `pnl_pct ≥ 7%` AND not yet booked | Sell 50%, move SL to entry (`breakeven_set`) |
| L5 | `pnl_pct ≥ 14%` AND L4 already hit | Sell 30% (of original), activate trailing |
| L6 | After L5: trail 4% below `peak_price` on remaining 20% | `TRAILING_STOP` when hit |
| L7 | Premium ≤ entry × 0.80 (–20%) safety net | Full exit `STOPLOSS_HIT` |

Lot rounding: with single-lot positions (NIFTY=65, BNF=30), 50/30/20 splits are not integer-lot. `min_lots_for_partial_book` (default 3) gates partial booking — below threshold, exit fully at L4. `partial_book_enabled = false` disables L4-L6 entirely (legacy behaviour).

The `_execute_partial_exit` flow: set `partial_book_X_hit = True` BEFORE broker I/O (prevents re-fire), place order outside the lock, on success decrement `qty_remaining` and append a leg. On order failure, roll back the flag for retry on the next candle. The partial exit does NOT clear `raw.position` and does NOT log to `paper_trade.csv` — only the final exit logs, with all accumulated legs in one row.

**Second-trade block:** sticky `first_trade_was_sl` flag preserved (defence-in-depth even with `max_trades_per_day=1`).

### ATM Strike Management

- Dynamic ATM reselection on each candle close (while no position open)
- Hysteresis is computed in **ATM-space** (offset-independent): only swap when spot moves ≥ 40% of one strike interval past the current ATM
- On swap: CE strike = `new_atm + strike_offset_ce × interval`, PE = `new_atm + strike_offset_pe × interval`. Old option tokens unsubscribed; `ce_ltp` / `pe_ltp` reset to 0.

---

## Indicators (`indicators.py`)

| Indicator | Details |
|---|---|
| **EMA(period)** | `compute_ema(values, period)` — generic; called with `period=20` (legacy) or `period=9` (trend-pullback secondary) |
| **RSI-14** | Wilder's method; first 14 entries `None` |
| **VWAP** | Intraday cumulative; resets each session; previous-day seed candles excluded by date filter inside `get_latest_indicators` |
| **Efficiency ratio** | `|net_close_move| / (max_high − min_low)` over last 10 candles |
| **Volume surge** | `has_volume_surge(candles, ratio)` — current ≥ ratio × avg(last 10); returns `True` (no-block) when volumes are uniform/zero |
| **Candle body** | `candle_body_pct(c)` — body / range × 100 |
| **Wick** | `upper_wick_pct(c)` / `lower_wick_pct(c)` (v3 — used by BNF fade trigger) |
| **Spike candle** | `is_spike_candle(c, threshold=1.0)` — range > threshold% of close |
| **MIN_CANDLES** | 22 (20 for EMA + 2 for slope detection) |

---

## WebSocket & Market Data (`market_data.py`)

- **Single `KiteTicker`** shared across NIFTY, BANKNIFTY, and meta tokens
- Index/Futures tokens: `MODE_FULL`. Option tokens: `MODE_LTP`. Meta tokens (VIX): `MODE_LTP`.
- **India VIX** — token `264969`, registered via `subscribe_meta_token(264969, vix_state.set_vix_ltp)` at app lifespan startup
- **ConnectionMonitor** (persistent daemon): 15s poll, 45s grace before forced reconnect, 60s tick-stall threshold (market hours only)
- **Candle backfill on reconnect**: REST historical API; per-instrument
- **Volume:** cumulative daily volume converted to per-tick delta; first tick after start sets baseline (contributes 0)

---

## Output Files

| File | Contents |
|---|---|
| `candle_logs/<inst>_candles_YYYY-MM-DD.csv` | Per-candle OHLCV + indicators + signal + position snapshot. **43 columns** (v3 added: `day_bias`, `vix_ltp`, `position_peak_price`, `qty_remaining`, `partial_book_1_hit`, `partial_book_2_hit`, `active_trail_level`, `spot_sl_price`, `event_today`) |
| `entry_attempts_<inst>.csv` | Every signal that fired but was blocked (12 cols; `skip_reason` is free-text) |
| `paper_trades_<inst>.csv` | Completed paper trades. **43 cols** (v3 added: `leg_1/2/3_qty/exit_price/exit_time/reason`, `weighted_avg_exit_price`, `total_pnl_rupees`, `day_bias`, `vix_at_entry`, `entry_mode`). Legacy `qty`, `exit_price`, `pnl_rupees` hold weighted-aggregate values when legs are present so existing readers/summary stats keep working. On schema upgrade, old 26-col files are auto-rotated to `paper_trades_<inst>.legacy.csv`. |
| `trading.log` | Rotating application log (10 MB × 5 files) |
| `events.json` | Hand-maintained event-day skip calendar |
| `backtest/results/<run>_<inst>_<timestamp>_trades.csv` | Per-trade output of `python3 -m backtest.replay ...` |
| `backtest/results/<run>_<inst>_<timestamp>_summary.csv` | Per-day summary (trades, wins, losses, gross_pnl, signal_mismatch, opening_rsi) |

---

## Backtest Harness (`backtest/`)

Replays logged candle data through the production strategy + risk_manager logic. Reuses real `get_latest_indicators`, `generate_signal`, `classify_day_bias`, trail-stop logic — zero duplication.

```bash
# Replay legacy strategy on logged days
python3 -m backtest.replay --instrument NIFTY --start 2026-04-28 --end 2026-04-30 --check-fidelity

# Replay with cfg override (test variants without editing config.py)
python3 -m backtest.replay --instrument NIFTY --start 2026-04-28 --end 2026-04-30 \
    --config-override backtest/variants/nifty_trend_pullback.json
```

`--check-fidelity` compares the recomputed signal vs the logged `signal` column. Where seed data is fresh (previous day), match rate is 100%. The harness loads previous-day candle CSV (within 20-day lookback) as seed for indicator warmup; VWAP is auto-filtered to today's date. Exit timing differs from production because the harness only sees candle closes (not intra-candle ticks).

---

## Frontend (`frontend/`)

Vue 3 + vite. Build with `npm run build` → `frontend/dist/`. The dashboard auto-refreshes every 2s (status) / 10s (trades).

**Key components:**
- `views/DashboardView.vue` — NIFTY page
- `views/BankNiftyView.vue` — BANKNIFTY page
- `components/StrategyStatusBanner.vue` — v3 banner showing day_bias, VIX vs threshold, event_today, force-exit, failed-fade counter (BNF), and the current `block_reason` if entries are blocked
- `components/CandlestickChart.vue` — live 5-min chart with VWAP / EMA-20 / RSI overlays

**Position banner (when a trade is open)** shows: `qty_remaining/qty`, breakeven-locked badge, ✓/○ partial-book progress pills, peak-premium tracker, spot SL, and trailing SL.

**Trade table** — exit-reason cell shows a `2 legs` / `3 legs` badge with hover tooltip listing each leg.

The status endpoint payload (`get_status` in `strategy_engine.py`) includes a `block_reason` field — the engine calls `can_enter_trade()` and surfaces why the next entry would be blocked (e.g., `EVENT_DAY:RBI_POLICY`, `VIX_HIGH:23.5`, `DAY_BIAS_NO_TRADE`, `Past last entry time (14:00:00)`). The banner translates these to user-friendly sentences.

---

## Important Notes

- **No automated tests** — verify changes by reading logic carefully and replaying via `python3 -m backtest.replay`.
- **Frontend** rebuild required after Vue source changes (`cd frontend && npm run build`). Server doesn't auto-rebuild.
- **Authentication state** is persisted to `.kite_session.json` (0600 perms) so re-logins survive server restarts; cleared via `/logout` or by deleting the file.
- **OAuth resilience:** `kite_service.generate_session()` retries on transient 503/504/HTML errors (3 attempts, 0.4s/0.8s/1.6s backoff). `TransientKiteError` triggers a `/login?retry=zerodha_503` redirect from the callback.
- **Single-user application** — no multi-user auth layer.
- State shared between threads lives in `InstrumentStateManager` objects; **always use the lock**. `partial_legs` list is defensively copied in `get_state()`.
- Do not add a `_check_websocket` watchdog back to `DailyScheduler` — `ConnectionMonitor` in `market_data.py` is the sole reconnection driver.
- **To roll back v3 → legacy:** flip `entry_mode = "vwap_ema_breakout"` in `config.py` (and ideally also `partial_book_enabled = False`, `max_trades_per_day = 2`, `force_exit_time = "15:20"`, `vix_max = 999.0`, `sl_spot_pct = 999.0`, `time_stop_min = 999`, `strike_offset_ce/pe = 0`). No code changes needed.
- **Reference plan:** `~/.claude/plans/along-the-above-plan-validated-wozniak.md` documents the full v2 → v3 rationale and per-phase rollout.

---

## Pre-LIVE checklist

Before flipping `TRADING_MODE=LIVE`:

1. ≥5 paper-trading days observed
2. ≥1 partial-book-1 trigger observed across instruments (validates the leg-aware exit path)
3. Backtest harness re-run weekly against latest candle logs (regime-shift detection)
4. `events.json` updated with upcoming RBI / Fed / earnings dates
5. Manual review of last 10 paper trades — ensure exit reasons make sense, day_bias was correct
6. VIX ticks visible in `trading.log` (`Meta token 264969 subscribed (live)`)
7. ITM strikes available in option chain on a quiet day (no `ValueError` from `find_option_instrument`)
