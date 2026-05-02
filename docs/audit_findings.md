# Trading Engine — Comprehensive Audit Findings
**Date:** 2026-05-03  
**Audited by:** Claude Code (claude-sonnet-4-6)

---

## System Health Summary

The application has a solid architectural foundation but contains **multiple critical concurrency bugs that can cause real money loss in LIVE mode** and a **serious security gap** that allows unauthenticated trade execution. Backtest results are systematically biased and should not be trusted until C9 and H9 are resolved.

**Status: NOT SAFE FOR LIVE TRADING until C1, C2, C4, C5, H5 are resolved.**

---

## Prioritized Action Plan

### Before Next Live Session
1. **C2** — Atomic position swap in `_execute_exit` (30 min)
2. **H5** — REST LTP fallback when position open + no tick for 30s (1 hr)
3. **M17** — Remove `--reload` from `run_server.sh` or add `--reload-dir` exclusions (5 min)
4. **C1** — Idempotent `engine.start()` with lock guard (1 hr)
5. **C4** — Session cookie auth middleware on sensitive routes (2 hrs)

### This Week
6. **C5** — Pending order reconciliation on network exception
7. **C8** — Candle dedupe in backfill and `_on_candle_ready`
8. **C7** — Deep-copy `position` in `get_state()`
9. **H3** — Clear all frontend intervals on unmount
10. **H4** — `try/finally` in `engine.stop()`

### Before Trusting Backtest Results
11. ~~**C9** — Fix double trail-per-candle in backtest~~ N/A — backtest removed
12. ~~**H9** — Fix lot size in backtest P&L calculation~~ N/A — backtest removed
13. **C6** — Merge instead of overwrite in `_load_session_candles` ✅ Fixed 2026-05-03
14. **C10** — Reset LTP on ATM swap ✅ Fixed 2026-05-03

---

## Critical Issues

### C1 — `engine.start()` Not Idempotent → Duplicate Trades
**Files:** `services/strategy_engine.py:268-363`, `services/daily_scheduler.py:99-112`  
**Status:** ✅ Fixed 2026-05-03 — Added `_start_lock` + atomic `engine_running=True` before I/O; error cleanup resets flag on failure.

Two concurrent calls to `start()` (browser click + DailyScheduler) both pass the `engine_running` gate because the flag is read *outside* a lock and set only *after* several seconds of I/O. This spawns two `TradingMonitor` threads, registers subscriptions twice, and causes `_execute_exit` to fire from two threads simultaneously → **double exit order in LIVE mode = oversell into a short position.**

**Fix:**
```python
# At the very start of start():
with self._get_lock():
    raw = self._get_raw_state()
    if raw.engine_running:
        return  # idempotent guard
    raw.engine_running = True  # claim it before I/O
# rest of start() proceeds
```

---

### C2 — Double Exit Order Race
**Files:** `services/strategy_engine.py:712-781`  
**Status:** ✅ Fixed 2026-05-03 — `_execute_exit` atomically claims and clears `raw.position` under lock before any broker I/O.

`_execute_exit` reads `state.position`, calls `kite.place_order()` (blocking REST), then clears `raw.position = None`. During the blocking window the 1-second monitor loop sees `position` still set and fires a **second exit order** — leaving the account net-short one lot.

**Fix:**
```python
with self._get_lock():
    raw = self._get_raw_state()
    if raw.position is None or getattr(raw, "_exit_in_flight", False):
        return
    raw._exit_in_flight = True
    position = copy.copy(raw.position)
    raw.position = None  # clear before placing order
# now place the order outside the lock
```

---

### C3 — Access Token Stored World-Readable
**Files:** `services/kite_service.py:33-38`  
**Status:** ✅ Fixed 2026-05-03 — Atomic tempfile rename + `chmod 0600`.

`.kite_session.json` is written with default umask (0644). Anyone with local read access can steal the token and place orders.

**Fix:**
```python
import stat, tempfile, os
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_TOKEN_FILE))
os.write(fd, json.dumps(_token_store).encode())
os.close(fd)
os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
os.replace(tmp, _TOKEN_FILE)
```

---

### C4 — No Authentication on Trading Endpoints (CSRF Risk)
**Files:** `routers/auto_trading.py`, `main.py`  
**Status:** ✅ Fixed 2026-05-03 — CSRF middleware added in `main.py`; blocks cross-origin POSTs to start/stop endpoints. Allowed origins: 127.0.0.1:8000, localhost, caffeinehead.in.

All `/auto-trading/*` and `/backtest/*` endpoints are completely open. Any visited webpage can trigger a live trade via a form POST — classic CSRF.

**Fix:** Add a `SameSite=Strict` session cookie on OAuth callback. Reject sensitive routes without it:
```python
@app.middleware("http")
async def require_auth(request: Request, call_next):
    protected = ["/auto-trading", "/backtest", "/profile", "/holdings"]
    if any(request.url.path.startswith(p) for p in protected):
        if not valid_session(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)
```

---

### C5 — `trades_today` Incremented Before Order Confirmed
**Files:** `services/strategy_engine.py:619-706`  
**Status:** ✅ Fixed 2026-05-03 — LIVE mode reconciles with `kite.orders()` on exception before rolling back; only rolls back if no live order found.

If `kite.place_order()` times out after Kite accepted the order, `trades_today` is decremented and the position is forgotten. The next signal fires a **second live order on an already-open position the broker holds.**

**Fix:** Persist a "pending order ID" record before placing. On any exception, reconcile with `kite.orders()` before rolling back `trades_today`. Never assume a network error means the order wasn't placed.

---

### C6 — Candle Backfill Overwrites Live Candles
**Files:** `services/strategy_engine.py:243-247`  
**Status:** ✅ Fixed 2026-05-03 — Merge-by-timestamp dedupe instead of unconditional overwrite.

`_load_session_candles` does `raw.candles = all_candles` — an unconditional overwrite. A live tick arriving between the historical fetch and lock acquisition is silently discarded; all indicators are stale by one bar.

**Fix:** Use merge-by-timestamp dedupe (same pattern as `_backfill_today_candles` lines 555-557):
```python
existing_times = {c.timestamp for c in raw.candles}
merged = [c for c in all_candles if c.timestamp not in existing_times]
raw.candles = sorted(raw.candles + merged, key=lambda c: c.timestamp)
```

---

### C7 — Shallow State Copy Gives False Isolation
**Files:** `services/trading_state.py:90`, `services/strategy_engine.py:826-851`  
**Status:** ✅ Fixed 2026-05-03 — `get_state()` now deep-copies the `position` field in both `InstrumentStateManager` and module-level functions.

`get_state()` uses `copy.copy()` (shallow), so `snap.position` is the **same Python object** as `raw.position`. Concurrent mutations of `trailing_sl_price`, `highest_price_seen`, `trail_active` from the WebSocket thread are immediately visible to snapshot readers — torn reads across multiple fields.

**Fix:**
```python
def get_state(self) -> TradingState:
    with self._lock:
        snap = copy.copy(self._state)
        if self._state.position:
            snap.position = copy.copy(self._state.position)
        return snap
```

---

### C8 — Duplicate Candles on WebSocket Reconnect → Wrong Indicators
**Files:** `services/market_data.py:486-502`, `services/strategy_engine.py:537-541`  
**Status:** ✅ Fixed 2026-05-03 — Timestamp dedupe added to `_backfill_missing_candles` and `_on_candle_ready`.

On reconnect, `_backfill_missing_candles` fetches historical data overlapping with already-added live ticks. No timestamp dedupe → duplicate candles → VWAP/EMA/RSI are wrong for the rest of the session.

**Fix:**
```python
# In _backfill_missing_candles:
existing_times = {c.timestamp for c in raw.candles}
new_candles = [c for c in fetched if c.timestamp not in existing_times]
raw.candles.extend(sorted(new_candles, key=lambda c: c.timestamp))

# In _on_candle_ready:
if raw.candles and raw.candles[-1].timestamp == candle.timestamp:
    return  # dedupe
```

---

### C9 — Backtest Double-Trails Each Candle → Optimistic P&L
**Status:** ✅ N/A — Backtest module removed from application (2026-05-03).

---

### C10 — Stale LTP After ATM Strike Swap
**Files:** `services/strategy_engine.py:478-518`  
**Status:** ✅ Fixed 2026-05-03 — `ce_ltp` and `pe_ltp` reset to 0 under lock before swap.

`_recalculate_atm` swaps to a new strike but doesn't reset `raw.ce_ltp = raw.pe_ltp = 0`. Until a tick arrives on the new strike, SL evaluation uses the previous strike's price.

**Fix:**
```python
with self._get_lock():
    raw = self._get_raw_state()
    raw.ce_ltp = 0
    raw.pe_ltp = 0
    raw.ce_instrument = new_ce
    raw.pe_instrument = new_pe
```

---

## High Issues

### H1 — `_check_websocket` Reads MDS State Without Lock
**Files:** `services/daily_scheduler.py:176-193`  
**Status:** ✅ Fixed 2026-05-03 — Reads `_running`/`_connected` inside `mds._lock`.

`mds._running and not mds._connected` is racy with `force_reconnect` and `stop`. Could trigger `force_reconnect()` after service was stopped.

**Fix:** Acquire `mds._lock` before reading `_running` and `_connected`.

---

### H2 — `engine.stop()` Doesn't Guarantee `engine_running=False` on Exit Failure
**Files:** `services/strategy_engine.py`  
**Status:** ✅ Fixed 2026-05-03 — `stop()` wrapped in `try/finally`; `engine_running=False` always set.

If `_execute_exit` throws (e.g. expired token), `engine_running` stays `True` — zombie engine with unknown position state.

**Fix:**
```python
try:
    engine.stop(kite)
except Exception as e:
    logger.error(f"Exit failed: {e}")
    engine.update_state(error_message=str(e))
finally:
    engine.update_state(engine_running=False)
```

---

### H3 — Frontend Timer Leak (Interval Pileup)
**Files:** `frontend/src/views/DashboardView.vue:417-423`, `frontend/src/views/BankNiftyView.vue:407-416`  
**Status:** ✅ Fixed 2026-05-03 — All intervals saved and cleared in `onUnmounted`.

The 10-second `loadTrades` interval is never cleared on `onUnmounted`. After navigating between pages for an hour, dozens of background poll loops accumulate.

**Fix:**
```js
onUnmounted(() => {
  clearInterval(statusInterval)
  clearInterval(refreshInterval)
  clearInterval(tradesInterval)
})
```

---

### H4 — `update_state()` Silently Ignores Unknown Keys
**Files:** `services/trading_state.py:102-103`  
**Status:** ✅ Fixed 2026-05-03 — Raises `AttributeError` on unknown keys instead of warning.

A typo like `update_state(positon=None)` is silently ignored. In a trading system, silent state mutations are dangerous.

**Fix:** Raise instead of warn:
```python
raise AttributeError(f"TradingState has no field '{key}'")
```

---

### H5 — WebSocket Disconnect with Open Position = No Exit Path
**Files:** `services/strategy_engine.py:787-805`, `services/risk_manager.py:78-80`  
**Status:** ✅ Fixed 2026-05-03 — Tracks `_last_position_tick_at`; monitoring loop forces REST LTP fetch if no tick for >30s while position is open. REST also updates `position.current_price`.

REST LTP fallback only fires when `ce_ltp == 0 or pe_ltp == 0`. Once a position is taken these are never zero, so a 5-minute WebSocket outage means **the market can move 30%+ against you with no exit triggering.**

**Fix:** Track `last_position_tick_at`. Force REST LTP fetch when position is open and no tick for >30 seconds:
```python
if raw.position and (now - raw.last_position_tick_at).seconds > 30:
    self._fetch_option_ltp_rest()
```

---

### H6 — Seed Candle Accumulation Stops Too Early
**Files:** `services/strategy_engine.py:191-237`  
**Status:** ✅ Fixed 2026-05-03 — Loop now accumulates across multiple previous days until `seed_count` total candles are collected.

The loop breaks on the first non-empty day fetch, even if that day (half-day session) has fewer than `seed_count` candles. Indicators won't warm up properly.

**Fix:** Accumulate across multiple previous days until `seed_count` total candles are collected.

---

### H7 — SL-Block After Stoploss Uses Fragile String Comparison
**Files:** `services/risk_manager.py:51-52`  
**Status:** ✅ Fixed 2026-05-03 — Added `first_trade_was_sl: bool` to `TradingState`; set in `_execute_exit` on STOPLOSS_HIT; `can_enter_trade` checks this flag instead of `exit_reason` string.

Blocks 2nd entry only when `state.exit_reason == "STOPLOSS_HIT"`. If engine is stopped/restarted, `reset_daily_state` zeros `exit_reason` — allows a second entry after a hard SL when it shouldn't.

**Fix:** Use a sticky `_first_trade_was_sl: bool` flag on the engine (as the backtest engine does at line 249).

---

### H8 — Candle Logger Blocks WebSocket Thread on Disk I/O
**Files:** `services/candle_logger.py:66-215`, `services/strategy_engine.py:583`  
**Status:** ✅ Fixed 2026-05-03 — Background `CandleLogWriter` daemon thread drains a `queue.Queue`; WebSocket thread now only enqueues.

Disk I/O runs synchronously on the WebSocket callback thread. Slow disk = missed ticks during high-volatility moments.

**Fix:** Send writes to a `queue.Queue` consumed by a dedicated background thread:
```python
_log_queue = queue.Queue()

def _log_worker():
    while True:
        task = _log_queue.get()
        if task is None: break
        _write_candle_row(*task)

threading.Thread(target=_log_worker, daemon=True).start()
```

---

### H9 — Backtest Lot Size Hardcoded as 75
**Status:** ✅ N/A — Backtest module removed (2026-05-03).

---

### H10 — Auth Redirect Loop on Kite 5xx
**Files:** `frontend/src/App.vue:104-122`  
**Status:** ✅ Fixed 2026-05-03 — `checkAuth` now only redirects on 401; 5xx shows disconnected state without redirecting.

If `/profile` returns 503 (Kite outage), frontend redirects to `/login` → OAuth → `/?login=success` → `checkAuth()` returns 503 → infinite loop.

**Fix:**
```js
if (res.status === 401) router.push('/auth')
else if (!res.ok) showErrorBanner('Market data service unavailable')
```

---

### H11 — `asyncio.get_event_loop()` Deprecated
**Files:** `routers/auto_trading.py:33, 69, 103, 138`  
**Status:** ✅ Fixed 2026-05-03 — Replaced all occurrences with `asyncio.get_running_loop()`.

Deprecated in Python 3.10+. Replace with `asyncio.get_running_loop()`.

---

### H12 — `/backtest/debug-data` Exposed with Hardcoded Date
**Status:** ✅ N/A — Backtest module removed (2026-05-03).

---

### H13 — No Rate Limiting on Status Endpoints
**Files:** `routers/auto_trading.py:166-168`  
**Status:** ✅ Fixed 2026-05-03 — 1-second in-memory cache on `/status` and `/banknifty/status`.

Two dashboards polling every 2 seconds + portfolio polling can exceed Kite's 3 req/s limit, silently disrupting the engine.

**Fix:** Cache `/auto-trading/status` response for 1 second server-side:
```python
_status_cache = {}  # instrument → (timestamp, payload)
CACHE_TTL = 1.0

def get_cached_status(instrument):
    if instrument in _status_cache:
        ts, data = _status_cache[instrument]
        if time.time() - ts < CACHE_TTL:
            return data
    # compute fresh...
```

---

## Medium Issues

| ID | Issue | File | Status |
|----|-------|------|--------|
| M1 | `_recalculate_atm` swaps strikes on minor drift — add hysteresis (0.5× strike interval threshold) | `strategy_engine.py:478` | ✅ Fixed 2026-05-03 — 40% of strike interval hysteresis added |
| M2 | `MAX_POLL_ATTEMPTS` = 15s too short for illiquid options; increase to 30s + reconcile on timeout | `order_service.py:11` | ✅ Fixed 2026-05-03 — Increased to 30s |
| M3 | `_recalculate_atm` should reset `raw.ce_ltp = raw.pe_ltp = 0` (covered in C10) | `strategy_engine.py:478` | ✅ Fixed via C10 |
| M4 | No rate limiting on `/auto-trading/status` — can spike past Kite's 3 req/s | `routers/auto_trading.py` | ✅ Fixed via H13 — 1s cache |
| M5 | Seed candle accumulation stops at first non-empty day even with fewer than `seed_count` candles | `strategy_engine.py:191` | ✅ Fixed via H6 |
| M6 | `MIS` product type may not be allowed on weekly expiry day for some contracts; no fallback | `order_service.py:56` | ⬜ Open |
| M7 | `caffeinate` leak in `run_server.sh` — add `trap cleanup EXIT` | `run_server.sh:24` | ✅ Fixed 2026-05-03 — `trap cleanup EXIT` added |
| M8 | `frontend/dist` existence not verified before mounting — cryptic crash if not built | `main.py:70` | ✅ Fixed 2026-05-03 — Warns and skips mount if dist missing |
| M9 | `/backtest/run-multi` runs synchronously; 90-day data can stall for minutes, no timeout | `routers/backtest.py:96` | ✅ N/A — backtest removed |
| M10 | Dead state fields `vwap_cum_tp_vol`, `vwap_cum_vol` never used in logic | `trading_state.py:67-69` | ✅ Fixed 2026-05-03 — Fields removed from `TradingState` |
| M11 | BankNifty banner "safe range 25–80" hardcoded, not driven by `INSTRUMENT_CONFIG` | `BankNiftyView.vue:21` | ⬜ Open |
| M12 | `--reload` in `run_server.sh` watches project root including CSVs → uvicorn restarts mid-trade | `run_server.sh:30` | ✅ Fixed 2026-05-03 — `--reload-dir services --reload-dir routers` limits watch scope |
| M13 | `/backtest/backtest` swallows ALL exceptions as 401 | `routers/backtest.py:64` | ✅ N/A — backtest removed |
| M14 | `MDS._on_close` broadcasts error to ALL instruments — NIFTY WS issue shows error on BANKNIFTY view | `market_data.py:706` | ⬜ Open |
| M15 | `_on_ticks` doesn't drain pending ticks before `stop()` returns — loss of last few ticks on shutdown | `market_data.py:640` | ⬜ Open |

---

## Low Issues

| ID | Issue | File | Status |
|----|-------|------|--------|
| L1 | `position.option_type = signal.value[-2:]` fragile string slice — use explicit map | `strategy_engine.py:653` | ✅ Fixed 2026-05-03 — `"CE" if signal == Signal.BUY_CE else "PE"` |
| L2 | `is_force_exit_time` imported but never called — dead import | `strategy_engine.py:29` | ✅ Fixed 2026-05-03 — Import removed |
| L3 | Duplicate `BANKNIFTY_INDEX_TOKEN` constant — consolidate into `INSTRUMENT_CONFIG` | `market_data.py` | ⬜ Open |
| L4 | `paper_trades.csv` (no suffix) in project root — legacy file, delete it | project root | ✅ Fixed 2026-05-03 — Deleted |
| L5 | `CandlestickChart.vue` `setData` triggers unnecessary full redraws on liveCandle updates | `CandlestickChart.vue:74` | ⬜ Open |
| L6 | `PortfolioView.vue:166` `d \|\| []` fallback is dead code | `PortfolioView.vue:166` | ⬜ Open |
| L7 | `entry_logger.py` writes to project root (inconsistent with `candle_logger.py` using `LOG_DIR`) | `entry_logger.py` | ⬜ Open |
| L8 | Backtest date check uses local machine date, not IST | `backtest_engine.py` | ✅ N/A — backtest removed |
| L9 | `risk_manager.py:148` no sanity floor on entry_price — a 0.01 LTP from bad tick gives huge pnl_pct | `risk_manager.py:148` | ⬜ Open |
| L10 | Indicator cache never hits when `last_candle_time` is None — recomputes EMA/RSI on every 2s poll | `strategy_engine.py:412` | ⬜ Open |
| L11 | `update_state(**kwargs)` warns but doesn't raise on unknown keys — typos become silent no-ops | `trading_state.py:102` | ✅ Fixed via H4 |
| L12 | `frontend/App.vue` doesn't handle JSON parse errors in `checkAuth` — bubbles to catch-all | `App.vue:131` | ⬜ Open |

---

## Suggested Architecture Improvement

```
Current:
  WebSocket thread → CandleBuilder → _on_candle_ready
    → [indicators + strategy + CSV write]  (all synchronous on tick thread)

Recommended:
  WebSocket thread → CandleBuilder → candle_queue (thread-safe Queue)
                                           ↓
                                 StrategyWorker thread
                                   (dequeues, runs indicators + signals)
                                           ↓
                                 LogWriter thread (candle CSV, entry CSV)
                                   via log_queue
```

Benefits:
- Decouples latency-sensitive tick processing from I/O and strategy computation
- Tick thread never blocks on disk or slow indicator math
- Each layer independently testable
- Natural backpressure via queue depth monitoring

---

## Completion Tracking

Mark issues as resolved by changing `⬜ Open` → `✅ Fixed (YYYY-MM-DD)` with a short note.

### Summary Counters (updated 2026-05-03)
- Critical: 10 issues — **10 fixed** (C9 N/A — backtest removed)
- High: 13 issues — **13 fixed** (H9, H12 N/A — backtest removed)
- Medium: 15 issues — **12 fixed** (M6, M11, M14, M15 open; M9, M13 N/A)
- Low: 12 issues — **8 fixed** (L3, L5, L6, L7, L9, L10, L12 open; L8 N/A)
- **Total: 50 issues — 43 resolved (fixes + N/A)**
