# Strategy Version Reference
## NIFTY_INTRADAY_VWAP_EMA_BREAKOUT

---

## v2 — Baseline (current as of 2026-04-20)

### Philosophy
Simple, permissive filters. Trust the core VWAP+EMA+RSI+volume combination without additional quality gates. Fewer false negatives at the cost of some low-quality entries.

### Entry Rules (ALL must be true)

**CE (Call):**
- `close > VWAP` by ≥ 0.15%
- EMA20 trending up + strong slope (≥8 pts over last 5 candles)
- Strong bullish candle: body ≥ 55%, close > open
- Breakout: `high > prev_high`
- 2 of last 3 candles are bullish (multi-candle confirmation)
- RSI > 50 (no upper cap)
- Efficiency ratio ≥ 0.45
- Volume surge ≥ 1.2× 10-candle average
- Not a spike candle (range ≤ 1%)
- Market state: TRENDING (not SIDEWAYS)

**PE (Put):**
- `close < VWAP` by ≥ 0.15%
- EMA20 trending down + strong slope (≥8 pts drop over last 5 candles)
- Strong bearish candle: body ≥ 55%, close < open
- Breakout: `low < prev_low`
- 2 of last 3 candles are bearish (multi-candle confirmation)
- RSI < 50 (no lower floor)
- Efficiency ratio ≥ 0.45
- Volume surge ≥ 1.2× 10-candle average
- Not a spike candle (range ≤ 1%)
- Market state: TRENDING (not SIDEWAYS)

### Config (both NIFTY and BANKNIFTY identical)

| Parameter | Value |
|---|---|
| `vwap_dist_min_pct` | 0.15% |
| `rsi_min_ce` | 50 |
| `rsi_max_ce` | 100 (no cap) |
| `rsi_min_pe` | 0 (no floor) |
| `rsi_max_pe` | 50 |
| `price_ema_gap_min_ce` | 0.0% (disabled) |
| `price_ema_gap_max_ce` | 999% (disabled) |
| `price_ema_gap_min_pe` | 0.0% (disabled) |
| `price_ema_gap_max_pe` | 999% (disabled) |
| `efficiency_min_ce` | 0.45 |
| `efficiency_min_pe` | 0.45 |
| `opening_rsi_overbought` | 999 (disabled) |
| `opening_rsi_oversold` | 0 (disabled) |
| `vwap_dist_max_pe_pct` | 999% (disabled) |

### Risk Parameters
- Hard SL: 20% below entry
- Trailing SL: activates at +15% gain, starts 6% below peak, tightens 1% per additional 10% gain, floor at 3%
- Max trades/day: 2
- Entry window: 09:50 AM – 2:00 PM IST
- Force exit: 3:20 PM IST

### Known Weaknesses
- RSI with no upper cap allows entries at overbought levels (RSI 80+) where reversals are likely
- 0.15% VWAP distance is a thin buffer — prone to reversal whipsaws near VWAP
- efficiency_min_pe = 0.45 is too loose for PE trades; allowed entries in choppy downswings
- No protection against extreme opening RSI days (gap-up/gap-down opens)
- Volume surge filter fails in sustained high-volume distribution phases (entire period is elevated, no candle "surges" 20% above baseline)

---

## v3 — Quality Filters (active Apr-13 to Apr-20, 2026)

### Philosophy
Added per-instrument quality gates tuned from live trading data (Apr-13/15/16/17). More restrictive to reduce low-quality entries, at the cost of missing some valid trades in sustained trends.

### Changes from v2

#### NIFTY-specific

| Parameter | v2 | v3 | Reason |
|---|---|---|---|
| `vwap_dist_min_pct` | 0.15% | **0.20%** | Reduce VWAP reversal whipsaws |
| `rsi_max_ce` | 100 | **72** | Block overbought CE entries |
| `rsi_min_pe` | 0 | **28** | Block oversold PE entries (likely to bounce) |
| `rsi_max_pe` | 50 | **50** | Unchanged |
| `price_ema_gap_min_ce` | 0.0% | **0.05%** | Price must be meaningfully above EMA (not just touching) |
| `price_ema_gap_max_ce` | 999% | **0.35%** | Price too far above EMA = overextended, skip |
| `price_ema_gap_min_pe` | 0.0% | **0.10%** | Price must be meaningfully below EMA |
| `efficiency_min_ce` | 0.45 | **0.45** | Unchanged |
| `efficiency_min_pe` | 0.45 | **0.60** | PE trades need stronger trend confirmation |

#### BANKNIFTY-specific

| Parameter | v2 | v3 | Reason |
|---|---|---|---|
| `vwap_dist_min_pct` | 0.15% | **0.15%** | Kept at 0.15% — Apr-17 win had only 0.15% |
| `rsi_max_ce` | 100 | **70** | BankNifty reverses faster at overbought |
| `rsi_min_pe` | 0 | **30** | BankNifty bounces fast from oversold |
| `rsi_max_pe` | 50 | **48** | Slight tightening |
| `price_ema_gap_min_ce` | 0.0% | **0.05%** | Same as NIFTY |
| `price_ema_gap_max_ce` | 999% | **0.40%** | BankNifty allows slightly wider gap than NIFTY |
| `price_ema_gap_min_pe` | 0.0% | **0.10%** | Same as NIFTY |
| `price_ema_gap_max_pe` | 999% | **0.40%** | Apr-16 had 0.54% and was a bad entry |
| `efficiency_min_ce` | 0.45 | **0.55** | Higher bar for BN CE |
| `efficiency_min_pe` | 0.45 | **0.55** | Higher bar for BN PE |
| `opening_rsi_overbought` | disabled | **80** | Opening RSI > 80 → block entire day |
| `opening_rsi_oversold` | disabled | **25** | Opening RSI < 25 → block entire day |
| `vwap_dist_max_pe_pct` | disabled | **0.50%** | PE: don't enter if already >0.50% below VWAP (overextended) |

### v3 Performance Issues Found (Apr-20 analysis)

1. **Volume surge paradox**: In sustained high-volume selloffs (e.g., Apr-20 afternoon), ALL candles have elevated volume (~900M–1.1B). No single candle can surge 20% above the already-elevated 10-candle baseline → `volume_surge=False` blocks every valid PE entry.

2. **NIFTY 0.20% VWAP filter too tight**: The Apr-20 selloff started near VWAP and spent most of the afternoon within 0.20% of it. Every PE candidate was blocked before it could develop enough distance.

3. **efficiency_min_pe = 0.60 too strict**: The 13:25 candle (efficiency=0.53) and 13:45 (0.5974) were both near-misses. The 0.60 bar filtered out valid PE setups in a genuine downtrend.

4. **10:40 CE miss**: Only blocker was `multi_candle_bullish=False`. A perfect candle (body 88%, VWAP dist 0.30%, RSI 67, efficiency 0.83) was killed by the multi-candle filter because the prior 2 candles had weak bodies (28%, 44%).

### Hypothetical missed P&L on Apr-20 (v3)
- **CE opportunity at 10:40**: CE LTP ~125 → peak ~140 at 11:25 = +12% (missed)
- **PE opportunity at 13:35**: PE LTP ~158 → trailing stop exit ~179 = +14% (missed)
- On 65 lots PE: ₹158 × 65 × 14% ≈ **₹91,000 notional gain missed**

---

## Comparison Summary

| Dimension | v2 | v3 |
|---|---|---|
| VWAP min distance | 0.15% (both) | 0.20% NIFTY, 0.15% BN |
| RSI bands | CE >50, PE <50 (unbounded) | Capped: CE ≤72/70, PE ≥28/30 |
| EMA gap filter | Off | On (0.05–0.35% CE, ≥0.10% PE) |
| Efficiency PE | 0.45 (both) | 0.60 NIFTY, 0.55 BN |
| Opening RSI day-block | Off | Off (NIFTY), On ≥80/≤25 (BN) |
| VWAP max PE distance | Off | Off (NIFTY), 0.50% (BN) |
| False negative rate | Lower | Higher (missed Apr-20 trades) |
| False positive rate | Higher | Lower |
| Best suited for | Trending days, clear breakouts | Volatile/whipsaw days, gap opens |

---

## Potential v4 Ideas (not implemented)

1. **Volume surge against longer baseline**: Use 20–30 candle avg instead of 10. Prevents volume-surge failure in sustained institutional selling/buying sessions.

2. **Body threshold for PE**: Lower from 55% → 50%. Bearish candles naturally have smaller bodies; the 13:20 candle (49.5%) and 13:40 (54.5%) would have triggered.

3. **Multi-candle weighting by body strength**: Instead of raw green/red count, weight by body%. A single 88%-body candle should outweigh two 20%-body candles.

4. **VWAP distance adaptive**: Use ATR-based VWAP distance threshold rather than fixed %. On low-volatility days 0.15% is tight; on high-volatility days 0.20% is fine.

5. **Volume surge per session phase**: Separate avg baselines for morning (09:15–11:00) and afternoon (11:00–14:00). Afternoon volumes are structurally higher.
