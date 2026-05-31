# NIFTY Futures Strategy Backtest

Self-contained harness to design & compare intraday NIFTY-futures strategies
(trades the future directly: long/short, SL/target in index points).

## Data (`data/`, fetched via `fetch_data.py` from Zerodha)
- `nifty_fut_5min.csv` — **front-month FUT 5-min, 2026-04-01 → 2026-05-29** (~40 sessions). PRIMARY backtest instrument.
- `nifty_spot_5min.csv` — index spot 5-min, 2026-03-02 → 2026-05-29 (~60 sessions). Extra robustness check (≈ futures minus basis).
- `nifty_fut_daily.csv` — continuous stitched FUT daily, full 3 months (context only).

> Intraday 5-min futures only goes back to Apr 1 because monthly contracts expire and Zerodha's stitched "continuous" feed is daily-only.

## Run
```bash
source venv/bin/activate
python backtest/run.py <strategy_module> --data fut --split [--trades]
```
Prints a human summary + a final `JSON {...}` line (machine-readable).

## Cost model (engine.py, conservative)
- 1 lot = 65 qty; 1 index point = ₹65/lot
- Round-trip cost ₹450/lot (brokerage+STT+exch+GST+stamp)
- Slippage 1.0 pt/side; entries fill at NEXT bar open; SL checked before target intrabar (conservative)

## Files
- `indicators.py` — vectorized EMA/RSI/VWAP/ATR/Supertrend (per-session, no leakage)
- `engine.py` — market mechanics + engine-owned exit/risk model + cost accounting; `BarContext` strategy interface (see header)
- `metrics.py` — win%, PF, max DD, Sharpe, expectancy
- `run.py` — runner with OOS train/test split
- `strategies/` — one module per strategy, each exposes `STRATEGY`
