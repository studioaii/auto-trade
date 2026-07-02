export const meta = {
  name: 'nifty-v2-improve',
  description: 'Deep analysis of NIFTY v1 vs v2 paper-trading data to find evidence-based improvements to v2 (no code changes)',
  phases: [
    { title: 'Analyze', detail: '5 parallel deep-dives: v1 profile, v1↔v2 reconcile, v2 shadow-log gates, SL/exit, code grounding' },
    { title: 'Verify', detail: 'adversarial noise + mechanism check on each recommendation' },
    { title: 'Synthesize', detail: 'confidence-ranked improved-v2 plan' },
  ],
}

const COMMON = `CONTEXT — NIFTY options paper-trading system. Working dir: /home/kumarasamyppm321/auto-trade
Two NIFTY strategies share the SAME VWAP+EMA breakout signal logic:
- NIFTY v1 ("1.0"): the proven config. Hard SL was -20%, recently changed to -18%. Trades file: paper_trades_nifty.csv (34 completed trades, 2026-05-12 to 2026-06-25). Entry attempts: entry_attempts_nifty.csv. Candle logs: candle_logs/nifty_candles_YYYY-MM-DD.csv (May 4 to Jun 26; read its header first to learn columns).
- NIFTY v2 ("2.0"): the SAME v1 breakout SIGNAL (model logged as V1_BREAKOUT) PLUS extra gates; paper-only; only ~6 trading days old. Trades file: paper_trades_nifty_2.csv (6 trades, Jun 18-25; has extra cols mfe_pct, mae_pct, sl_pct, exit_layer, hard_sl_premium, trail_active). Shadow/entry-attempt log: entry_attempts_nifty_2.csv (331 rows = one per evaluated 5-min candle, Jun 18-26; cols: date,time,model,signal,outcome,spot,atm_strike,option_ltp,vwap,vwap_dist_pct,rsi14,body_pct,or_high,or_low,skip_reasons,reason). v2 candle logs: candle_logs/nifty2_candles_YYYY-MM-DD.csv for Jun 18,19,23,24,25,26 (cols include open,high,low,close,volume,vwap,ema20,rsi14,vwap_distance_pct,body_pct,or_high,or_low,signal_v2,skip_reason,spot,atm_strike,ce_ltp,pe_ltp,in_position,position_type,position_entry_price,position_current_price,trail_active).
v2 differs from v1 ONLY by these (from config.py NIFTY_2): morning guard entry_window_start=(11,0) [v1=09:50]; entry_window_end=(14,0); require_close_breakout=True + breakout_lookback=3 + breakout_margin_pct=0.02 (close must clear swing by 0.02% of price); regime chop gate regime_vwap_lookback=5 + regime_max_vwap_crossings=2 (>=2 VWAP crossings in 5 candles = block); softened opposite-signal exit opposite_exit_confirm_closes=2 (needs 2 consecutive wrong-side-VWAP closes vs v1's 1); sl_pct=18.0; trail_trigger_pct=15.0, trail_gap_base_pct=6.0, trail_gap_step_pct=1.0, trail_gap_min_pct=3.0; max_trades_per_day=2; skip_second_after_hard_sl=True; force_exit_hhmm=(15,20). NO fixed target, NO breakeven, NO time-stop.
GOAL: find evidence-based ways to improve v2. Do NOT propose changes to v1. Ignore BANKNIFTY and NIFTY_FUT entirely — do not read or mention them.
STATISTICAL DISCIPLINE (critical): samples are tiny (v1 n=34, v2 n=6, shadow blocked-signals ~dozen). A prior analysis found the v1 edge NOT statistically significant (t approx 0.22, tail-driven). Quantify EVERY claim with its n; explicitly flag anything driven by 1-2 trades; never present an overfit parameter as fact. pandas is available, scipy is NOT (compute t = mean / (std/sqrt(n)) by hand if needed).
TOOLS: you have Bash (python3 + pandas) and Read. COMPUTE from the CSVs with real code — never eyeball or guess numbers. Return your analysis via the StructuredOutput tool. In recommendations, target REAL config keys / files. Be precise and quantitative.`

const FINDINGS = {
  type:'object', additionalProperties:false,
  properties:{
    summary:{type:'string', description:'2-4 sentence executive summary of this analysis'},
    keyStats:{type:'array', items:{type:'object', additionalProperties:false,
      properties:{metric:{type:'string'},value:{type:'string'},note:{type:'string'}}, required:['metric','value']}},
    findings:{type:'array', items:{type:'object', additionalProperties:false,
      properties:{title:{type:'string'},evidence:{type:'string'},impact:{type:'string'},
        confidence:{type:'string'},sampleSize:{type:'number'}}, required:['title','evidence','confidence']}},
    recommendations:{type:'array', items:{type:'object', additionalProperties:false,
      properties:{change:{type:'string', description:'concrete change to v2, naming the config key/param'},
        rationale:{type:'string'},expectedEffect:{type:'string'},
        confidence:{type:'string', description:'high/medium/low'},risksNoise:{type:'string', description:'how likely this is just noise/overfit'}},
      required:['change','rationale','confidence']}}
  },
  required:['summary','findings','recommendations']
}

const VERDICT = {
  type:'object', additionalProperties:false,
  properties:{
    verdict:{type:'string', enum:['confirmed','weakened','refuted']},
    reasoning:{type:'string'},
    statisticalConcern:{type:'string'},
    revisedConfidence:{type:'string', description:'high/medium/low after scrutiny'}
  },
  required:['verdict','reasoning']
}

phase('Analyze')

const P1 = await parallel([
  () => agent(`${COMMON}

TASK — PROFILE NIFTY v1 (the baseline). Load paper_trades_nifty.csv (34 trades). Compute and report:
1. Overall: win rate, count W/L, total pnl_pct sum, mean pnl_pct, expectancy, profit factor (gross win / gross loss), payoff ratio (avg win / avg loss), and a hand t-stat on mean pnl_pct.
2. By direction: CE vs PE — win rate, mean pnl_pct, count.
3. By entry time-of-day bucket: 09:50-11:00, 11:00-12:00, 12:00-13:00, 13:00-14:00 — win rate, mean pnl_pct, sum pnl_pct, count for each.
4. By exit reason (reason_for_exit / column): TRAILING_STOP, STOPLOSS_HIT, OPPOSITE_SIGNAL, TIME_EXIT — count, win rate, mean pnl_pct.
5. Winners-vs-losers separation: compare mean entry rsi14, efficiency, vwap distance (derive from reason_for_entry or spot vs vwap), entry hour — does anything actually separate them? Report the deltas and whether they're meaningful given n.
6. THE MORNING WINDOW (this is what v2's 11:00 guard blocks): list every v1 trade entered 09:50-11:00 with date, dir, strike, entry_time, pnl_pct, exit reason. Sum their pnl_pct and rupees (lot 65, pnl_points*65). State clearly: did the 09:50-11:00 window NET make or lose money in v1, and is the result tail-driven (name the 1-2 trades dominating it)?
Flag every tail-driven conclusion.`, {label:'v1-profile', phase:'Analyze', schema: FINDINGS}),

  () => agent(`${COMMON}

TASK — RECONCILE v1 vs v2 ON OVERLAPPING DATES and attribute each difference to a v2 filter. v2 = v1 signal + filters, so on the same days the differences are caused by v2's gates/exits.
Load paper_trades_nifty.csv filtered to 2026-06-18..2026-06-25, and paper_trades_nifty_2.csv (all 6). For EACH overlapping date, list side by side: v1 trade(s) [entry_time, dir, strike, pnl_pct, exit reason] vs v2 trade(s) [same]. Explain each divergence.
Then quantify:
A) Trades v1 TOOK that v2 did NOT (blocked) — list them, sum their pnl_pct and rupees. Attribute each to the specific v2 filter that blocked it (morning guard 11:00 / close-breakout / regime chop / opposite-exit / max-trades). Cross-check against entry_attempts_nifty_2.csv skip_reasons at that timestamp.
B) Trades v2 took that differ from v1 (different strike/entry/exit on same day) — explain why.
C) Net P&L: total pnl_pct and rupees for v1-over-overlap vs v2-over-overlap. Which did better and by how much?
D) Per-filter verdict: for the MORNING GUARD specifically, list every v1 trade entered before 11:00 in the overlap window, its pnl_pct, and whether blocking it helped or hurt. PAY SPECIAL ATTENTION to 2026-06-25: confirm whether v1's 10:50 CE trade (+33.87%) was blocked by v2's morning guard while v2 took only the 12:00 trade (-18%). Quantify the cost of the morning guard on the overlap.
Be explicit that the overlap is only ~6 days — flag low confidence and tail-dependence.`, {label:'v1-v2-reconcile', phase:'Analyze', schema: FINDINGS}),

  () => agent(`${COMMON}

TASK — v2 SHADOW-LOG / GATE PRECISION. Analyze entry_attempts_nifty_2.csv (331 rows; 6 FIRED, 325 SKIPPED). Skip-reason counts already known: 'market is sideways' 188, 'low volume — no participation' 96, 'no breakout' 17, 'too close to VWAP' 13, plus a few second-entry/max-trades/before-window/regime blocks.
1. Confirm the skip-reason distribution and break it down by date and time-of-day.
2. FOCUS on blocked DIRECTIONAL signals: rows where signal is BUY_CE or BUY_PE but outcome=SKIPPED. List each (date, time, signal, spot, atm_strike, skip_reason).
3. For each blocked directional signal, RECONSTRUCT the would-be trade outcome: open candle_logs/nifty2_candles_<date>.csv, locate the candle at that time, take entry option price (ce_ltp if BUY_CE, pe_ltp if BUY_PE), then walk that option's LTP forward candle-by-candle applying v2 exit rules (-18% hard SL = entry*0.82; trailing activates at +15%, gap starts 6% below peak tightening 1% per extra +10% gain, floor 3%; force-exit at 15:20). Report would-be pnl_pct for each and whether WIN/LOSS.
4. VERDICT per gate: do 'market is sideways' and 'low volume' gates block net-WINNING or net-LOSING would-be trades? Tally would-be W/L and summed pnl_pct for the signals each gate blocked. Is any gate clearly over-blocking good entries, or correctly filtering bad ones?
5. Also check: how many distinct candles even produced a directional signal vs NO_SIGNAL — is v2 starving for trades (too few signals) or drowning in blocked-but-bad signals?
Tiny sample — flag confidence honestly. 5-min granularity for the forward walk is acceptable; note it.`, {label:'shadow-gates', phase:'Analyze', schema: FINDINGS}),

  () => agent(`${COMMON}

TASK — STOP-LOSS and EXIT/TRAILING analysis (the user explicitly asked about 20% vs 18% SL).
DATA: paper_trades_nifty_2.csv has mfe_pct (max favorable excursion) and mae_pct (max adverse excursion) per trade — use them directly for v2. paper_trades_nifty.csv (v1, 34) lacks MFE/MAE — RECONSTRUCT per trade from candle_logs/nifty_candles_<date>.csv: read the header to find the in-position option price column (e.g. position_current_price or ce_ltp/pe_ltp), then over the candles between entry_time and exit_time compute max favorable % = (max_price-entry)/entry*100 and max adverse % = (min_price-entry)/entry*100. If the option price isn't logged per candle in v1 logs, say so and fall back to what's available.
Q1 — STOP LOSS (18% vs 20% vs alternatives):
 - For WINNERS (both v1 and v2), how deep did MAE go before the trade recovered? List EVERY winner whose MAE breached -15%, -18%, and -20%. A tighter SL would have converted these winners into losers — quantify how many winners each SL level (15/18/20) would have killed and the pnl_pct given up.
 - For trades that hit the hard SL, note that capital is lost regardless of 18 vs 20 (tighter just loses slightly less per trade). Compute the per-trade rupee difference between an 18% and 20% stop on the SL-hit trades.
 - RECOMMEND an SL level/band with the trade-off explicit (tighter SL saves on losers but kills marginal winners). Is 18% better or worse than 20% on this data? Quantify net.
Q2 — TRAILING-STOP GIVE-BACK:
 - For winners, compute MFE vs realized exit pnl_pct. Average give-back = mean(mfe_pct - pnl_pct) over winners. List the worst give-backs (big MFE, small realized).
 - Assess whether a tighter trail, a looser trail, a fixed partial-book, or a fixed target would have improved total expectancy on this data. Quantify a couple of concrete alternatives (e.g., book 50% at +20%; or trail gap floor 5% vs 3%).
Flag tail-driven results and tiny-n caveats throughout.`, {label:'sl-exit', phase:'Analyze', schema: FINDINGS}),

  () => agent(`${COMMON}

TASK — CODE GROUNDING + BUG/INCONSISTENCY HUNT (so later recommendations target real params, and so we catch implementation issues that masquerade as strategy problems). Read these files fully:
 v1: services/strategy.py, services/risk_manager.py
 v2: services/nifty_strategy_v2.py, services/nifty_risk_manager_v2.py, services/nifty_engine_v2.py, services/nifty_paper_trade_v2.py
Document precisely:
1. v1 entry conditions (all gates) and exit logic.
2. v2 entry conditions and exit logic.
3. The EXACT v2-minus-v1 differences as IMPLEMENTED (confirm or correct the config-level summary in the context: morning guard, close-confirmed breakout + margin, regime chop gate, softened opposite-exit with 2-close confirm, sl_pct=18, trailing schedule). Note any place where the code does NOT match the config intent.
4. BUGS / inconsistencies / dead logic in v2 specifically: e.g. a gate that can never pass or always passes; off-by-one in the 11:00 / 14:00 / 15:20 time windows; the shadow log not actually recording would-be outcomes; mae/mfe computed wrong; trailing/SL math errors; ATM/LTP carry-forward issues affecting entries. For each, give file + function + line and severity.
5. For anything you'd recommend changing, name the exact config key (in config.py NIFTY_2) or code location.
Do NOT edit any file.`, {label:'code-grounding', phase:'Analyze', schema: FINDINGS}),
])

const labels = ['v1-profile','v1-v2-reconcile','shadow-gates','sl-exit','code-grounding']
const recs = []
P1.forEach((r,i)=>{ if(r && Array.isArray(r.recommendations)) r.recommendations.forEach(rec=> recs.push({...rec, source: labels[i]})) })
log(`Phase 1 done: ${P1.filter(Boolean).length}/5 analyses returned, ${recs.length} candidate recommendations to verify`)

phase('Verify')
const MAX_VERIFY = 20
const toVerify = recs.slice(0, MAX_VERIFY)
if (recs.length > MAX_VERIFY) log(`Verifying top ${MAX_VERIFY} of ${recs.length} recommendations (rest folded into synthesis)`)

const verified = await parallel(toVerify.map(rec => () =>
  parallel([
    () => agent(`${COMMON}

You are a SKEPTICAL STATISTICIAN. A prior agent recommended this change to NIFTY v2:
${JSON.stringify(rec, null, 2)}

Independently RE-CHECK from the raw CSVs whether this is a real edge or just noise/overfit on a tiny sample. Recompute the relevant numbers yourself. Ask: is it driven by 1-2 tail trades? Is n large enough to distinguish from zero? Would it plausibly survive out-of-sample? Default to 'weakened' or 'refuted' if the evidence is thin. Only 'confirmed' if the data genuinely supports it AND it isn't fragile to removing the single best/worst trade. Report a revised confidence.`,
      {label:`noise:${rec.change.slice(0,28)}`, phase:'Verify', schema: VERDICT}),
    () => agent(`${COMMON}

You are a SKEPTICAL SYSTEMS ENGINEER. A prior agent recommended this change to NIFTY v2:
${JSON.stringify(rec, null, 2)}

Check MECHANISM/IMPLEMENTABILITY, not statistics. Read the relevant code (services/nifty_strategy_v2.py, services/nifty_risk_manager_v2.py, services/nifty_engine_v2.py, config.py NIFTY_2) and confirm: does the named param/key actually exist and do what the recommendation assumes? Would the change have unintended interactions with other gates/exits? Is it internally consistent (e.g., loosening the morning guard but keeping a gate that blocks mornings anyway)? Could it reintroduce a known leak? Verdict confirmed/weakened/refuted with reasoning.`,
      {label:`mech:${rec.change.slice(0,28)}`, phase:'Verify', schema: VERDICT}),
  ]).then(vs => ({ rec, verdicts: vs.filter(Boolean) }))
))

phase('Synthesize')
const payload = {
  analyses: P1.map((r,i)=>({ agent: labels[i], result: r })),
  verifiedRecommendations: verified.filter(Boolean),
}
const report = await agent(`${COMMON}

TASK — SYNTHESIZE THE FINAL "IMPROVE NIFTY v2" REPORT for the system owner. You are given (as JSON) all five phase-1 analyses and the adversarial verdicts (statistical + mechanism) on each candidate recommendation.

DATA:
${JSON.stringify(payload, null, 2)}

Write a thorough, decision-ready markdown report with these sections:
1. **Bottom line** — 3-5 sentences: is v2 actually better than v1 so far, and what are the 2-3 highest-value, defensible improvements?
2. **v1 vs v2 scorecard** — the head-to-head numbers (win rate, expectancy, net P&L on the overlap), with the explicit caveat that v2 is only ~6 days old.
3. **What's working in v2 / what's hurting** — per filter (morning guard, close-breakout, regime gate, softened opposite-exit, 18% SL). For the morning guard, state clearly whether the Jun-25 +33.9% blocked winner means the guard is net-negative on current data, or whether that's a single-trade artifact.
4. **The SL question (18% vs 20%)** — direct answer with the winner-kill / loser-save trade-off quantified.
5. **Exit quality** — trailing give-back finding and whether a partial-book/target is worth testing.
6. **Gate precision** — are the sideways/low-volume gates blocking good trades?
7. **Ranked recommendations** — a table: change | confidence (HIGH/MED/LOW) after adversarial review | expected effect | the noise/mechanism caveat. ONLY mark HIGH where BOTH verdicts confirmed and n supports it. Anything fragile to one trade = LOW and framed as "instrument & wait for more data", not "change now".
8. **What NOT to do** — overfit traps to avoid given n.
9. **Data-collection plan** — since n is the real bottleneck, what to log / how many more trades before any change is trustworthy.
Be honest and quantitative. Do not invent numbers — use only what the analyses provide. Prefer "insufficient evidence" over false precision. Return the markdown report as your final message.`,
  {label:'synthesis', phase:'Synthesize'})

return { report, analyses: payload.analyses, verified: verified.filter(Boolean) }
