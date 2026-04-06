import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from routers import auth, trading, auto_trading, backtest

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Kite Connect backend starting up")
    yield
    logger.info("Kite Connect backend shutting down")
    # Best-effort cleanup of trading engine on server shutdown
    from services.trading_state import get_state
    from services.strategy_engine import get_engine
    from services.kite_service import get_stored_token, require_authenticated_client
    if get_state().engine_running and get_stored_token():
        try:
            kite = require_authenticated_client()
            get_engine().stop(kite)
            logger.info("Trading engine stopped on shutdown")
        except Exception as e:
            logger.warning("Could not cleanly stop engine on shutdown: %s", e)


app = FastAPI(
    title="Kite Connect Backend",
    description="Minimal FastAPI integration with Zerodha Kite Connect API + Automated Nifty Options Trading",
    version="2.0.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(trading.router)
app.include_router(auto_trading.router)
app.include_router(backtest.router)


# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <title>Kite Connect — Trading Dashboard</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,sans-serif;background:#f0f2f5;color:#222;padding:24px 16px}
    h1{color:#0070f3;font-size:22px;margin-bottom:2px}
    .subtitle{color:#888;font-size:12px;margin-bottom:20px}

    .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
    .grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:16px}
    @media(max-width:600px){.grid2,.grid3{grid-template-columns:1fr}}

    .card{background:#fff;border-radius:12px;padding:18px 20px;box-shadow:0 1px 4px rgba(0,0,0,.08)}
    .card-title{font-size:13px;font-weight:700;color:#555;margin-bottom:14px;display:flex;align-items:center;gap:8px}

    /* stat tiles */
    .stat{background:#fff;border-radius:10px;padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.07)}
    .stat .lbl{font-size:11px;color:#999;text-transform:uppercase;letter-spacing:.4px}
    .stat .val{font-size:20px;font-weight:800;margin-top:4px;color:#111}
    .val.g{color:#16a34a}.val.r{color:#dc2626}.val.b{color:#0070f3}

    /* engine */
    .engine-card{border:2px solid #dbeafe}
    .status-row{display:flex;align-items:center;gap:10px;background:#f0f6ff;border-radius:8px;padding:9px 13px;font-size:13px;margin-bottom:14px;flex-wrap:wrap;gap:6px}
    .dot{width:9px;height:9px;border-radius:50%;background:#ccc;flex-shrink:0}
    .dot.on{background:#22c55e;box-shadow:0 0 5px #22c55e}.dot.off{background:#ef4444}
    .sep{color:#ccc}

    .btn{display:inline-flex;align-items:center;gap:5px;padding:9px 18px;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:opacity .15s,transform .1s}
    .btn:hover{opacity:.87}.btn:active{transform:scale(.97)}
    .btn:disabled{opacity:.38;cursor:not-allowed;transform:none}
    .g-btn{background:#22c55e;color:#fff}.r-btn{background:#ef4444;color:#fff}
    .b-btn{background:#0070f3;color:#fff}.s-btn{background:#f1f5f9;color:#444;border:1px solid #ddd}
    .btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}

    #msg{margin-top:10px;padding:9px 13px;border-radius:8px;font-size:12px;display:none}
    #msg.ok{background:#dcfce7;color:#166534;display:block}
    #msg.err{background:#fee2e2;color:#991b1b;display:block}

    .badge{font-size:10px;font-weight:700;padding:2px 7px;border-radius:8px}
    .bp{background:#fef9c3;color:#854d0e}.bl{background:#fee2e2;color:#991b1b}

    .live-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}
    @media(max-width:500px){.live-grid{grid-template-columns:1fr 1fr}}
    .li{background:#f8fafc;border-radius:8px;padding:10px 12px}
    .li .lbl{font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:.4px}
    .li .val{font-size:15px;font-weight:700;color:#111;margin-top:3px}

    /* open position banner */
    #pos-banner{display:none;margin-top:12px;padding:12px 16px;border-radius:10px;background:linear-gradient(135deg,#eff6ff,#e0f2fe);border:1px solid #bfdbfe}
    .pos-row{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}
    .pos-sym{font-size:16px;font-weight:800;color:#1d4ed8}
    .pos-meta{font-size:12px;color:#64748b}
    .pos-pnl{font-size:18px;font-weight:800}
    .pos-sl{font-size:11px;color:#888;margin-top:4px}

    /* paper trade table */
    .tbl-wrap{overflow-x:auto;margin-top:12px}
    table{width:100%;border-collapse:collapse;font-size:12px}
    th{background:#f8fafc;padding:8px 10px;text-align:left;font-size:11px;color:#888;text-transform:uppercase;letter-spacing:.4px;border-bottom:2px solid #e2e8f0;white-space:nowrap}
    td{padding:9px 10px;border-bottom:1px solid #f1f5f9;vertical-align:middle;white-space:nowrap}
    tr:hover td{background:#fafbff}
    .pill{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700}
    .pill-ce{background:#e0f2fe;color:#0369a1}
    .pill-pe{background:#fce7f3;color:#9d174d}
    .pill-win{background:#dcfce7;color:#15803d}
    .pill-loss{background:#fee2e2;color:#b91c1c}
    .pill-exit{background:#f1f5f9;color:#475569;font-size:10px}
    .empty-state{text-align:center;padding:32px;color:#aaa;font-size:13px}

    /* summary stats row */
    .sum-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}
    @media(max-width:500px){.sum-grid{grid-template-columns:1fr 1fr}}

    /* links */
    a{color:#0070f3;text-decoration:none;font-size:13px}
    a:hover{text-decoration:underline}
    .lrow{display:flex;align-items:center;gap:8px;margin:6px 0}
    .mt{font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;font-family:monospace}
    .mget{background:#dbeafe;color:#1e40af}.mpost{background:#dcfce7;color:#166534}

    .strategy-note{font-size:11px;color:#aaa;margin-top:12px;line-height:1.7}

    /* multi-bt */
    .mbt-sum-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:10px}
    @media(max-width:700px){.mbt-sum-grid{grid-template-columns:repeat(3,1fr)}}
    tr.mbt-row{cursor:pointer;transition:background .12s}
    tr.mbt-row:hover td{background:#eff6ff}
    tr.mbt-row.active td{background:#dbeafe;font-weight:600}
    .cum-bar{display:inline-block;height:12px;border-radius:3px;min-width:2px;vertical-align:middle}
  </style>
</head>
<body>
<h1>Kite Connect Dashboard</h1>
<p class="subtitle">NIFTY_INTRADAY_VWAP_EMA_BREAKOUT &nbsp;·&nbsp; Paper Trading Mode</p>

<!-- ── ENGINE CARD ─────────────────────────────────────────────── -->
<div class="card engine-card" style="margin-bottom:16px">
  <div class="card-title">
    ⚡ Auto Trading Engine
    <span id="mode-badge" class="badge bp">PAPER</span>
  </div>

  <div class="status-row">
    <div class="dot off" id="engine-dot"></div>
    <span id="engine-status-text">Engine stopped</span>
    <span class="sep">|</span>
    <span id="trades-count">Trades: 0 / 2</span>
    <span class="sep">|</span>
    <span id="candle-info">Candles: 0 / 22</span>
    <span class="sep">|</span>
    <span id="market-state-top">Market: —</span>
  </div>

  <div class="btn-row">
    <button class="btn g-btn" id="btn-start" onclick="startEngine()">▶ Start Engine</button>
    <button class="btn r-btn" id="btn-stop"  onclick="stopEngine()" disabled>■ Stop Engine</button>
    <button class="btn b-btn" onclick="refreshStatus()">↻ Refresh</button>
  </div>

  <div id="msg"></div>

  <!-- live stats grid -->
  <div class="live-grid" id="live-grid" style="display:none">
    <div class="li"><div class="lbl">Nifty Spot (Index)</div><div class="val" id="nifty-spot">—</div></div>
    <div class="li"><div class="lbl">Nifty Futures</div><div class="val" id="nifty-futures">—</div></div>
    <div class="li"><div class="lbl">ATM CE LTP</div><div class="val" id="ce-ltp">—</div><div class="lbl" id="ce-sym" style="margin-top:3px;color:#0369a1;font-size:10px;font-weight:700">—</div></div>
    <div class="li"><div class="lbl">ATM PE LTP</div><div class="val" id="pe-ltp">—</div><div class="lbl" id="pe-sym" style="margin-top:3px;color:#9d174d;font-size:10px;font-weight:700">—</div></div>
    <div class="li"><div class="lbl">Last Signal</div><div class="val" id="last-signal">—</div></div>
    <div class="li"><div class="lbl">Last Candle</div><div class="val" id="last-candle">—</div></div>
    <div class="li"><div class="lbl">Exit Reason</div><div class="val" id="exit-reason">—</div></div>
  </div>

  <!-- open position banner -->
  <div id="pos-banner">
    <div class="pos-row">
      <div>
        <div class="pos-sym" id="pos-sym">—</div>
        <div class="pos-meta" id="pos-meta">—</div>
        <div class="pos-sl" id="pos-sl">—</div>
      </div>
      <div style="text-align:right">
        <div class="pos-pnl" id="pos-pnl">—</div>
        <div class="pos-meta" id="pos-entry">—</div>
      </div>
    </div>
  </div>

  <div class="strategy-note">
    VWAP + EMA(20) · 5-min candles · Dynamic SL (12–22%) · Trail from +20% (8%→20% gap) · No hard profit target · RSI + Volume filters · Max 2 trades/day · 9:50–14:00 entries · Force exit 3:20 PM
  </div>
</div>

<!-- ── PAPER TRADE LOG ─────────────────────────────────────────── -->
<div class="card" style="margin-bottom:16px">
  <div class="card-title" style="justify-content:space-between">
    <span>📋 Paper Trade Log</span>
    <div style="display:flex;gap:8px;align-items:center">
      <button class="btn s-btn" style="padding:5px 12px;font-size:12px" onclick="loadTrades()">↻ Refresh</button>
      <a href="/auto-trading/paper-log/download" style="font-size:12px">⬇ CSV</a>
    </div>
  </div>

  <!-- summary stats -->
  <div class="sum-grid" id="sum-grid" style="display:none">
    <div class="stat"><div class="lbl">Total Trades</div><div class="val b" id="s-total">0</div></div>
    <div class="stat"><div class="lbl">Win Rate</div><div class="val g" id="s-wr">—</div></div>
    <div class="stat"><div class="lbl">Total P&amp;L</div><div class="val" id="s-pnl">—</div></div>
    <div class="stat"><div class="lbl">Avg Win / Loss</div><div class="val" id="s-avg">—</div></div>
  </div>

  <div class="tbl-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th><th>Date</th><th>Type</th><th>Symbol</th>
          <th>Entry Time</th><th>Entry ₹</th>
          <th>Exit Time</th><th>Exit ₹</th>
          <th>P&amp;L ₹</th><th>P&amp;L %</th>
          <th>Exit Reason</th><th>Entry Reason</th>
        </tr>
      </thead>
      <tbody id="trade-tbody">
        <tr><td colspan="12" class="empty-state">No paper trades yet. Start the engine to begin.</td></tr>
      </tbody>
    </table>
  </div>
</div>

<!-- ── BACKTEST ────────────────────────────────────────────────── -->
<div class="card" style="margin-bottom:16px" id="backtest-card">
  <div class="card-title">🔁 Strategy Backtest</div>

  <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:12px">
    <div>
      <label style="font-size:11px;color:#999;display:block;margin-bottom:3px">Select Date</label>
      <input type="date" id="bt-date" style="padding:7px 10px;border:1px solid #ddd;border-radius:7px;font-size:13px;color:#222"/>
    </div>
    <div style="margin-top:18px">
      <button class="btn b-btn" id="bt-btn" onclick="runBacktest()">▶ Run Backtest</button>
    </div>
  </div>
  <p style="font-size:11px;color:#aaa;margin-bottom:10px">
    Replays the VWAP+EMA strategy on historical Nifty 5-min data.
    P&amp;L shown in Nifty index points — estimated options ₹ ≈ points × 0.5 × 75 lot.
  </p>

  <div id="bt-msg" style="display:none;padding:9px 13px;border-radius:8px;font-size:12px;margin-bottom:10px"></div>

  <!-- Summary -->
  <div id="bt-summary" style="display:none;margin-bottom:14px">
    <div class="sum-grid" style="grid-template-columns:repeat(4,1fr)">
      <div class="stat"><div class="lbl">Trades</div><div class="val b" id="bt-s-total">0</div></div>
      <div class="stat"><div class="lbl">Win Rate</div><div class="val g" id="bt-s-wr">—</div></div>
      <div class="stat"><div class="lbl">Total Points</div><div class="val" id="bt-s-pts">—</div></div>
      <div class="stat"><div class="lbl">Avg Win / Loss pts</div><div class="val" id="bt-s-avg">—</div></div>
    </div>
  </div>

  <!-- Trade results -->
  <div id="bt-trades" style="display:none;margin-bottom:14px">
    <div style="font-size:12px;font-weight:700;color:#555;margin-bottom:8px">Trades</div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th><th>Signal</th>
            <th>Entry</th><th>Entry Nifty</th>
            <th>Exit</th><th>Exit Nifty</th>
            <th>Nifty Pts</th><th>Est ₹ P&amp;L</th><th>Exit Reason</th><th>Result</th>
          </tr>
        </thead>
        <tbody id="bt-trade-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- Candle timeline -->
  <div id="bt-candles" style="display:none">
    <div style="font-size:12px;font-weight:700;color:#555;margin-bottom:8px">
      5-Min Candle Timeline
      <span style="font-size:10px;font-weight:400;color:#aaa;margin-left:6px">highlighted rows = active trade</span>
    </div>
    <div class="tbl-wrap" style="max-height:380px;overflow-y:auto">
      <table>
        <thead>
          <tr>
            <th>Time</th><th>Open</th><th>High</th><th>Low</th><th>Close</th>
            <th>VWAP</th><th>EMA20</th><th>Market</th><th>Signal</th><th>Note</th>
          </tr>
        </thead>
        <tbody id="bt-candle-tbody"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- ── MULTI-DAY BACKTEST ──────────────────────────────────────── -->
<div class="card" style="margin-bottom:16px" id="multi-bt-card">
  <div class="card-title">📊 Multi-Day Backtest</div>

  <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px">
    <div>
      <label style="font-size:11px;color:#999;display:block;margin-bottom:3px">From Date</label>
      <input type="date" id="mbt-from" style="padding:7px 10px;border:1px solid #ddd;border-radius:7px;font-size:13px;color:#222"/>
    </div>
    <div>
      <label style="font-size:11px;color:#999;display:block;margin-bottom:3px">To Date</label>
      <input type="date" id="mbt-to" style="padding:7px 10px;border:1px solid #ddd;border-radius:7px;font-size:13px;color:#222"/>
    </div>
    <div>
      <button class="btn b-btn" id="mbt-btn" onclick="runMultiBacktest()">▶ Run Multi-Day</button>
    </div>
    <div style="display:flex;gap:4px;flex-wrap:wrap">
      <button class="btn s-btn" style="padding:5px 10px;font-size:11px" onclick="setMultiRange(5)">5D</button>
      <button class="btn s-btn" style="padding:5px 10px;font-size:11px" onclick="setMultiRange(10)">10D</button>
      <button class="btn s-btn" style="padding:5px 10px;font-size:11px" onclick="setMultiRange(30)">30D</button>
      <button class="btn s-btn" style="padding:5px 10px;font-size:11px" onclick="setMultiRange(60)">60D</button>
    </div>
  </div>
  <p style="font-size:11px;color:#aaa;margin-bottom:10px">
    Runs strategy on each trading day in the range. Click any date row to see full trade details + candle timeline.
    Max 90 days.
  </p>

  <div id="mbt-msg" style="display:none;padding:9px 13px;border-radius:8px;font-size:12px;margin-bottom:10px"></div>
  <div id="mbt-progress" style="display:none;margin-bottom:10px">
    <div style="background:#e2e8f0;border-radius:6px;height:6px;overflow:hidden">
      <div id="mbt-progress-bar" style="width:0%;height:100%;background:#0070f3;border-radius:6px;transition:width .3s"></div>
    </div>
    <div style="font-size:11px;color:#888;margin-top:4px" id="mbt-progress-text"></div>
  </div>

  <!-- Aggregate Summary -->
  <div id="mbt-summary" style="display:none;margin-bottom:16px">
    <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:10px" class="mbt-sum-grid">
      <div class="stat"><div class="lbl">Trading Days</div><div class="val b" id="mbt-days">0</div></div>
      <div class="stat"><div class="lbl">Total Trades</div><div class="val b" id="mbt-total">0</div></div>
      <div class="stat"><div class="lbl">Win Rate</div><div class="val g" id="mbt-wr">—</div></div>
      <div class="stat"><div class="lbl">Total P&amp;L</div><div class="val" id="mbt-pnl">—</div></div>
      <div class="stat"><div class="lbl">Max Drawdown</div><div class="val r" id="mbt-dd">—</div></div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">
      <div class="stat"><div class="lbl">Avg Win Pts</div><div class="val g" id="mbt-avgw">—</div></div>
      <div class="stat"><div class="lbl">Avg Loss Pts</div><div class="val r" id="mbt-avgl">—</div></div>
      <div class="stat"><div class="lbl">Best Day</div><div class="val g" id="mbt-best">—</div></div>
      <div class="stat"><div class="lbl">Worst Day</div><div class="val r" id="mbt-worst">—</div></div>
    </div>
  </div>

  <!-- Day-by-day table -->
  <div id="mbt-daily" style="display:none;margin-bottom:14px">
    <div style="font-size:12px;font-weight:700;color:#555;margin-bottom:8px">
      Day-by-Day Results
      <span style="font-size:10px;font-weight:400;color:#aaa;margin-left:6px">click a row to see full details</span>
    </div>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th>Date</th><th>Day</th><th>Trades</th><th>Wins</th><th>Losses</th>
            <th>Win Rate</th><th>Nifty Pts</th><th>Est ₹ P&amp;L</th><th>Cum. Pts</th>
          </tr>
        </thead>
        <tbody id="mbt-daily-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- Drill-down detail panel (shown when clicking a date) -->
  <div id="mbt-detail" style="display:none;border:2px solid #dbeafe;border-radius:10px;padding:16px;background:#fafbff">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div style="font-size:14px;font-weight:800;color:#1d4ed8" id="mbt-detail-title">—</div>
      <button class="btn s-btn" style="padding:4px 12px;font-size:11px" onclick="closeMultiDetail()">✕ Close</button>
    </div>

    <!-- Detail summary -->
    <div class="sum-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:14px">
      <div class="stat"><div class="lbl">Trades</div><div class="val b" id="mbt-d-total">0</div></div>
      <div class="stat"><div class="lbl">Win Rate</div><div class="val g" id="mbt-d-wr">—</div></div>
      <div class="stat"><div class="lbl">Total Points</div><div class="val" id="mbt-d-pts">—</div></div>
      <div class="stat"><div class="lbl">Est ₹</div><div class="val" id="mbt-d-rs">—</div></div>
    </div>

    <!-- Detail trades -->
    <div id="mbt-d-trades" style="margin-bottom:14px">
      <div style="font-size:12px;font-weight:700;color:#555;margin-bottom:6px">Trades</div>
      <div class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th><th>Signal</th>
              <th>Entry</th><th>Entry Nifty</th><th>Entry Reason</th>
              <th>Exit</th><th>Exit Nifty</th>
              <th>Nifty Pts</th><th>Est ₹</th><th>Exit Reason</th><th>Result</th>
            </tr>
          </thead>
          <tbody id="mbt-d-trade-tbody"></tbody>
        </table>
      </div>
    </div>

    <!-- Detail candle timeline -->
    <div id="mbt-d-candles">
      <div style="font-size:12px;font-weight:700;color:#555;margin-bottom:6px">
        5-Min Candle Timeline
        <span style="font-size:10px;font-weight:400;color:#aaa;margin-left:6px">highlighted = in trade</span>
      </div>
      <div class="tbl-wrap" style="max-height:380px;overflow-y:auto">
        <table>
          <thead>
            <tr>
              <th>Time</th><th>Open</th><th>High</th><th>Low</th><th>Close</th>
              <th>VWAP</th><th>EMA20</th><th>Market</th><th>Signal</th><th>Note</th>
            </tr>
          </thead>
          <tbody id="mbt-d-candle-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>

<!-- ── BOTTOM ROW ──────────────────────────────────────────────── -->
<div class="grid2">
  <div class="card">
    <div class="card-title">📊 Portfolio</div>
    <div class="lrow"><span class="mt mget">GET</span><a href="/profile">Profile</a></div>
    <div class="lrow"><span class="mt mget">GET</span><a href="/holdings">Holdings</a></div>
    <div class="lrow"><span class="mt mget">GET</span><a href="/positions">Positions</a></div>
    <div class="lrow"><span class="mt mget">GET</span><a href="/orders">Orders</a></div>
  </div>
  <div class="card">
    <div class="card-title">🔐 Auth &amp; Docs</div>
    <div class="lrow"><span class="mt mget">GET</span><a href="/login">Login with Zerodha</a></div>
    <div class="lrow"><span class="mt mget">GET</span><a href="/logout">Logout</a></div>
    <div class="lrow"><span class="mt mget">GET</span><a href="/docs">Swagger UI</a></div>
    <div class="lrow"><span class="mt mget">GET</span><a href="/redoc">ReDoc</a></div>
  </div>
</div>

<script>
let autoRefresh = null;

/* ── ENGINE ──────────────────────────────────────────────────── */
async function startEngine() {
  setMsg('Starting engine...','');
  setBtns(true);
  try {
    const r = await fetch('/auto-trading/start',{method:'POST'});
    const d = await r.json();
    if(!r.ok){setMsg(d.detail||'Error','err');setBtns(false);return;}
    setMsg('Engine started ('+d.mode+'). CE: '+d.instruments.ce+' | PE: '+d.instruments.pe,'ok');
    document.getElementById('btn-start').disabled=true;
    document.getElementById('btn-stop').disabled=false;
    startAutoRefresh();
  } catch(e){setMsg('Network error: '+e,'err');setBtns(false);}
}

async function stopEngine() {
  setMsg('Stopping...','');
  try {
    const r = await fetch('/auto-trading/stop',{method:'POST'});
    const d = await r.json();
    if(!r.ok){setMsg(d.detail||'Error','err');return;}
    const p = d.final_pnl ? ' | PnL: ₹'+d.final_pnl.pnl_rupees : '';
    setMsg('Stopped. Trades: '+d.trades_today+p,'ok');
    document.getElementById('btn-start').disabled=false;
    document.getElementById('btn-stop').disabled=true;
    stopAutoRefresh();
  } catch(e){setMsg('Error: '+e,'err');}
  await refreshStatus();
  loadTrades();
}

async function refreshStatus() {
  try {
    const r = await fetch('/auto-trading/status');
    const d = await r.json();
    updateEngineUI(d);
  } catch(e){}
}

function updateEngineUI(d) {
  document.getElementById('engine-dot').className = 'dot '+(d.engine_running?'on':'off');
  document.getElementById('engine-status-text').textContent = d.engine_running?'Engine running ('+d.mode+')':'Engine stopped';
  document.getElementById('trades-count').textContent = 'Trades: '+d.trades_today+' / '+d.max_trades;
  document.getElementById('candle-info').textContent = 'Candles: '+d.candle_count+' / '+d.candles_needed;
  document.getElementById('market-state-top').textContent = 'Market: '+(d.market_state||'—');

  const badge = document.getElementById('mode-badge');
  badge.textContent = d.mode||'PAPER';
  badge.className = 'badge '+(d.mode==='LIVE'?'bl':'bp');

  document.getElementById('btn-start').disabled = d.engine_running;
  document.getElementById('btn-stop').disabled  = !d.engine_running;

  // Resume auto-refresh on page reload if engine is already running
  if(d.engine_running && !autoRefresh) startAutoRefresh();
  else if(!d.engine_running && autoRefresh) stopAutoRefresh();

  const liveGrid = document.getElementById('live-grid');
  if(d.engine_running || d.nifty_spot>0) {
    liveGrid.style.display='grid';
    document.getElementById('nifty-spot').textContent    = d.nifty_spot>0 ? d.nifty_spot.toFixed(1) : '—';
    document.getElementById('nifty-futures').textContent = d.nifty_futures_ltp>0 ? d.nifty_futures_ltp.toFixed(1) : '—';
    document.getElementById('ce-ltp').textContent      = d.ce_ltp>0 ? '₹'+d.ce_ltp.toFixed(2) : '—';
    document.getElementById('pe-ltp').textContent      = d.pe_ltp>0 ? '₹'+d.pe_ltp.toFixed(2) : '—';
    document.getElementById('last-signal').textContent = d.last_signal||'—';
    document.getElementById('last-candle').textContent = d.last_candle_time||'—';
    document.getElementById('exit-reason').textContent = d.exit_reason||'—';
    // Show ATM option symbols
    if(d.instruments) {
      document.getElementById('ce-sym').textContent = d.instruments.ce || '—';
      document.getElementById('pe-sym').textContent = d.instruments.pe || '—';
    }
  }

  // Open position banner
  const banner = document.getElementById('pos-banner');
  if(d.position) {
    banner.style.display='block';
    const p = d.position;
    const pnl = d.pnl;
    document.getElementById('pos-sym').textContent = p.symbol;
    document.getElementById('pos-meta').textContent = p.option_type+' · Strike '+p.strike+' · Expiry '+p.expiry+' · Qty '+p.qty;
    const slInfo = p.trail_active
      ? 'Trail SL: ₹'+p.trailing_sl+' (trailing active)'
      : 'SL: ₹'+p.trailing_sl;
    document.getElementById('pos-sl').textContent = slInfo;
    document.getElementById('pos-entry').textContent = 'Entry: ₹'+p.entry_price+' @ '+p.entry_time;
    if(pnl) {
      const sign = pnl.pnl_rupees>=0?'+':'';
      const pnlEl = document.getElementById('pos-pnl');
      pnlEl.textContent = sign+'₹'+pnl.pnl_rupees+' ('+sign+pnl.pnl_pct+'%)';
      pnlEl.style.color = pnl.pnl_rupees>=0?'#16a34a':'#dc2626';
    }
  } else {
    banner.style.display='none';
  }
}

/* ── PAPER TRADE LOG ────────────────────────────────────────── */
async function loadTrades() {
  try {
    const r = await fetch('/auto-trading/paper-log');
    const d = await r.json();
    renderSummary(d.summary);
    renderTrades(d.trades);
  } catch(e){}
}

function renderSummary(s) {
  if(!s || s.total_trades===0) {
    document.getElementById('sum-grid').style.display='none';
    return;
  }
  document.getElementById('sum-grid').style.display='grid';
  document.getElementById('s-total').textContent = s.total_trades;
  document.getElementById('s-wr').textContent = s.win_rate_pct+'%';
  const pnlEl = document.getElementById('s-pnl');
  const sign = s.total_pnl_rs>=0?'+':'';
  pnlEl.textContent = sign+'₹'+s.total_pnl_rs;
  pnlEl.className = 'val '+(s.total_pnl_rs>=0?'g':'r');
  document.getElementById('s-avg').textContent = '₹'+s.avg_win_rs+' / ₹'+s.avg_loss_rs;
}

function renderTrades(trades) {
  const tbody = document.getElementById('trade-tbody');
  if(!trades || trades.length===0) {
    tbody.innerHTML = '<tr><td colspan="12" class="empty-state">No paper trades yet. Start the engine to begin.</td></tr>';
    return;
  }
  // Show latest trades first
  const rows = [...trades].reverse().map((t,i) => {
    const pnl   = parseFloat(t.pnl_rupees||0);
    const pnlPct= parseFloat(t.pnl_pct||0);
    const sign  = pnl>=0?'+':'';
    const typeP = t.option_type==='CE'
      ? '<span class="pill pill-ce">CE</span>'
      : '<span class="pill pill-pe">PE</span>';
    const pnlP  = pnl>=0
      ? '<span class="pill pill-win">'+sign+'₹'+pnl+'</span>'
      : '<span class="pill pill-loss">₹'+pnl+'</span>';
    const pctP  = pnl>=0
      ? '<span style="color:#16a34a;font-weight:700">'+sign+pnlPct+'%</span>'
      : '<span style="color:#dc2626;font-weight:700">'+pnlPct+'%</span>';
    const exitP = '<span class="pill pill-exit">'+t.reason_for_exit+'</span>';
    const sym   = '<span style="font-weight:600;font-size:11px">'+t.option_symbol+'</span>';
    const entryReason = t.reason_for_entry ? '<span title="'+t.reason_for_entry+'" style="cursor:help;color:#888">ℹ hover</span>' : '—';
    return '<tr>'
      +'<td>'+t.trade_number+'</td>'
      +'<td>'+t.date+'</td>'
      +'<td>'+typeP+'</td>'
      +'<td>'+sym+'</td>'
      +'<td>'+t.entry_time+'</td>'
      +'<td>₹'+t.entry_price+'</td>'
      +'<td>'+t.exit_time+'</td>'
      +'<td>₹'+t.exit_price+'</td>'
      +'<td>'+pnlP+'</td>'
      +'<td>'+pctP+'</td>'
      +'<td>'+exitP+'</td>'
      +'<td>'+entryReason+'</td>'
      +'</tr>';
  }).join('');
  tbody.innerHTML = rows;
}

/* ── HELPERS ─────────────────────────────────────────────────── */
function setMsg(text,type) {
  const el=document.getElementById('msg');
  el.textContent=text;
  el.className=type==='err'?'err':(type==='ok'?'ok':'');
  el.style.display=text?'block':'none';
}
function setBtns(dis) {
  document.getElementById('btn-start').disabled=dis;
  document.getElementById('btn-stop').disabled=dis;
}
function startAutoRefresh() {
  if(autoRefresh) clearInterval(autoRefresh);
  autoRefresh = setInterval(()=>{refreshStatus();loadTrades();},5000);
}
function stopAutoRefresh() {
  if(autoRefresh){clearInterval(autoRefresh);autoRefresh=null;}
}

/* ── BACKTEST ────────────────────────────────────────────────── */
function localDateStr(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth()+1).padStart(2,'0');
  const d = String(date.getDate()).padStart(2,'0');
  return `${y}-${m}-${d}`;
}

(function initDatePicker(){
  const inp = document.getElementById('bt-date');
  const d = new Date(); d.setDate(d.getDate()-1);
  inp.value = localDateStr(d);
  inp.max   = localDateStr(d);
})();

async function runBacktest() {
  const dateVal = document.getElementById('bt-date').value;
  if(!dateVal){btMsg('Please select a date','err');return;}

  const btn = document.getElementById('bt-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Running…';
  btMsg('Fetching historical data and replaying strategy…','info');

  // Hide old results
  ['bt-summary','bt-trades','bt-candles'].forEach(id=>document.getElementById(id).style.display='none');

  try {
    const r = await fetch('/backtest/run',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({date:dateVal}),
    });
    const d = await r.json();
    if(!r.ok){btMsg(d.detail||'Error running backtest','err');return;}
    btMsg('','');
    renderBacktest(d);
  } catch(e){
    btMsg('Network error: '+e,'err');
  } finally {
    btn.disabled = false;
    btn.textContent = '▶ Run Backtest';
  }
}

function renderBacktest(d) {
  const s = d.summary;

  // Summary
  document.getElementById('bt-summary').style.display='block';
  document.getElementById('bt-s-total').textContent = s.total_trades;
  document.getElementById('bt-s-wr').textContent = s.win_rate_pct+'%';
  const ptsEl = document.getElementById('bt-s-pts');
  const estRs = s.total_est_rs || 0;
  ptsEl.textContent = (s.total_points>=0?'+':'')+s.total_points+' pts  ('+(estRs>=0?'+':'')+'₹'+estRs+')';
  ptsEl.className = 'val '+(s.total_points>=0?'g':'r');
  document.getElementById('bt-s-avg').textContent = '+'+s.avg_win_points+' / '+s.avg_loss_points;

  // Trade table
  if(d.trades && d.trades.length>0){
    document.getElementById('bt-trades').style.display='block';
    const rows = d.trades.map(t=>{
      const sign = t.points>=0?'+':'';
      const sigPill = t.signal==='BUY_CE'
        ?'<span class="pill pill-ce">CE</span>'
        :'<span class="pill pill-pe">PE</span>';
      const resPill = t.result==='WIN'
        ?'<span class="pill pill-win">WIN</span>'
        :'<span class="pill pill-loss">LOSS</span>';
      const ptColor = t.points>=0?'#16a34a':'#dc2626';
      return '<tr>'
        +'<td>'+t.num+'</td>'
        +'<td>'+sigPill+'</td>'
        +'<td>'+t.entry_time+'</td>'
        +'<td>'+t.entry_nifty+'</td>'
        +'<td>'+t.exit_time+'</td>'
        +'<td>'+t.exit_nifty+'</td>'
        +'<td style="font-weight:700;color:'+ptColor+'">'+sign+t.points+' pts</td>'
        +'<td style="font-weight:700;color:'+ptColor+'">'+(t.est_options_pnl>=0?'+':'')+t.est_options_pnl+'</td>'
        +'<td><span class="pill pill-exit">'+t.exit_reason+'</span></td>'
        +'<td>'+resPill+'</td>'
        +'</tr>';
    }).join('');
    document.getElementById('bt-trade-tbody').innerHTML = rows;
  }

  // Candle timeline
  if(d.candles && d.candles.length>0){
    document.getElementById('bt-candles').style.display='block';
    const rows = d.candles.map(c=>{
      const bg = c.in_trade ? 'background:#eff6ff' : '';
      const sigColor = c.signal==='BUY_CE'?'#0369a1':c.signal==='BUY_PE'?'#9d174d':'#aaa';
      const mktBadge = c.market_state==='TRENDING'
        ?'<span style="color:#16a34a;font-weight:700;font-size:10px">▲ TREND</span>'
        :c.market_state==='SIDEWAYS'
        ?'<span style="color:#f59e0b;font-weight:700;font-size:10px">↔ SIDE</span>'
        :'<span style="color:#aaa;font-size:10px">—</span>';
      const noteStyle = c.note.startsWith('ENTRY')
        ? 'color:#1d4ed8;font-weight:700'
        : c.note.startsWith('EXIT')
        ? 'color:#dc2626;font-weight:700'
        : 'color:#888';
      return '<tr style="'+bg+'">'
        +'<td style="font-weight:600">'+c.time+'</td>'
        +'<td>'+c.open+'</td>'
        +'<td>'+c.high+'</td>'
        +'<td>'+c.low+'</td>'
        +'<td style="font-weight:600">'+c.close+'</td>'
        +'<td>'+c.vwap+'</td>'
        +'<td>'+(c.ema20||'—')+'</td>'
        +'<td>'+mktBadge+'</td>'
        +'<td style="color:'+sigColor+';font-weight:700;font-size:11px">'+c.signal+'</td>'
        +'<td style="'+noteStyle+'">'+c.note+'</td>'
        +'</tr>';
    }).join('');
    document.getElementById('bt-candle-tbody').innerHTML = rows;
  }
}

function btMsg(text, type) {
  const el = document.getElementById('bt-msg');
  el.textContent = text;
  el.style.display = text ? 'block' : 'none';
  el.style.background = type==='err'?'#fee2e2':type==='info'?'#eff6ff':'#dcfce7';
  el.style.color = type==='err'?'#991b1b':type==='info'?'#1e40af':'#166534';
}

/* ── MULTI-DAY BACKTEST ─────────────────────────────────────── */
let multiData = null;   // store full response for drill-down

(function initMultiDatePickers(){
  const to = document.getElementById('mbt-to');
  const fr = document.getElementById('mbt-from');
  const d = new Date(); d.setDate(d.getDate()-1);
  to.value = localDateStr(d);
  to.max   = localDateStr(d);
  const f = new Date(); f.setDate(f.getDate()-11);
  fr.value = localDateStr(f);
  fr.max   = localDateStr(d);
})();

function setMultiRange(days){
  const to = document.getElementById('mbt-to');
  const fr = document.getElementById('mbt-from');
  const d = new Date(); d.setDate(d.getDate()-1);
  to.value = localDateStr(d);
  const f = new Date(); f.setDate(f.getDate()-1-days);
  fr.value = localDateStr(f);
}

async function runMultiBacktest(){
  const fromVal = document.getElementById('mbt-from').value;
  const toVal   = document.getElementById('mbt-to').value;
  if(!fromVal || !toVal){mbtMsg('Select both dates','err');return;}

  const btn = document.getElementById('mbt-btn');
  btn.disabled = true;
  btn.textContent = '⏳ Running…';
  mbtMsg('Fetching data & replaying strategy for each trading day…','info');
  document.getElementById('mbt-progress').style.display='block';
  document.getElementById('mbt-progress-bar').style.width='10%';
  document.getElementById('mbt-progress-text').textContent='Connecting to Kite API…';

  ['mbt-summary','mbt-daily','mbt-detail'].forEach(id=>document.getElementById(id).style.display='none');

  try{
    const r = await fetch('/backtest/run-multi',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({from_date:fromVal, to_date:toVal}),
    });
    const d = await r.json();
    if(!r.ok){mbtMsg(d.detail||'Error','err');return;}
    document.getElementById('mbt-progress-bar').style.width='100%';
    document.getElementById('mbt-progress-text').textContent='Done!';
    setTimeout(()=>{document.getElementById('mbt-progress').style.display='none';},800);
    mbtMsg('','');
    multiData = d;
    renderMultiBacktest(d);
  }catch(e){
    mbtMsg('Network error: '+e,'err');
  }finally{
    btn.disabled=false;
    btn.textContent='▶ Run Multi-Day';
  }
}

function renderMultiBacktest(d){
  const a = d.aggregate;

  // Summary
  document.getElementById('mbt-summary').style.display='block';
  document.getElementById('mbt-days').textContent = d.trading_days;
  document.getElementById('mbt-total').textContent = a.total_trades;
  document.getElementById('mbt-wr').textContent = a.win_rate_pct+'%';

  const pnlEl = document.getElementById('mbt-pnl');
  const sign = a.total_points>=0?'+':'';
  pnlEl.textContent = sign+a.total_points+' pts ('+(a.total_est_rs>=0?'+':'')+'₹'+a.total_est_rs+')';
  pnlEl.className = 'val '+(a.total_points>=0?'g':'r');

  document.getElementById('mbt-dd').textContent = '-'+a.max_drawdown_pts+' pts';
  document.getElementById('mbt-avgw').textContent = '+'+a.avg_win_points;
  document.getElementById('mbt-avgl').textContent = a.avg_loss_points;
  document.getElementById('mbt-best').textContent = a.best_day+' (+'+a.best_day_pts+')';
  document.getElementById('mbt-worst').textContent = a.worst_day+' ('+a.worst_day_pts+')';

  // Daily table
  if(d.daily && d.daily.length>0){
    document.getElementById('mbt-daily').style.display='block';
    const dow = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    let cumPts = 0;
    const maxAbsCum = Math.max(...d.daily.reduce((acc,day)=>{cumPts+=day.total_points;acc.push(Math.abs(cumPts));return acc;},[]),1);
    cumPts = 0;

    const rows = d.daily.map((day, idx)=>{
      cumPts += day.total_points;
      const dt = new Date(day.date+'T00:00:00');
      const dayName = dow[dt.getDay()];
      const ptSign = day.total_points>=0?'+':'';
      const ptColor = day.total_points>=0?'#16a34a':'#dc2626';
      const rsSign = day.total_est_rs>=0?'+':'';
      const rsColor = day.total_est_rs>=0?'#16a34a':'#dc2626';
      const cumSign = cumPts>=0?'+':'';
      const cumColor = cumPts>=0?'#16a34a':'#dc2626';
      const barW = Math.max(2, Math.abs(cumPts)/maxAbsCum*60);
      const barColor = cumPts>=0?'#22c55e':'#ef4444';
      const wrColor = day.win_rate_pct>=50?'#16a34a':day.win_rate_pct>0?'#f59e0b':'#dc2626';

      return '<tr class="mbt-row" data-idx="'+idx+'" onclick="showMultiDetail('+idx+')">'
        +'<td style="font-weight:600">'+day.date+'</td>'
        +'<td>'+dayName+'</td>'
        +'<td>'+day.total_trades+'</td>'
        +'<td style="color:#16a34a">'+day.wins+'</td>'
        +'<td style="color:#dc2626">'+day.losses+'</td>'
        +'<td style="color:'+wrColor+';font-weight:700">'+day.win_rate_pct+'%</td>'
        +'<td style="font-weight:700;color:'+ptColor+'">'+ptSign+day.total_points+'</td>'
        +'<td style="color:'+rsColor+'">'+rsSign+'₹'+day.total_est_rs+'</td>'
        +'<td><span style="color:'+cumColor+';font-weight:700;margin-right:6px">'+cumSign+cumPts.toFixed(1)+'</span>'
        +'<span class="cum-bar" style="width:'+barW+'px;background:'+barColor+'"></span></td>'
        +'</tr>';
    }).join('');
    document.getElementById('mbt-daily-tbody').innerHTML = rows;
  }
}

function showMultiDetail(idx){
  if(!multiData || !multiData.daily[idx]) return;
  const day = multiData.daily[idx];

  // Highlight active row
  document.querySelectorAll('tr.mbt-row').forEach(r=>r.classList.remove('active'));
  const activeRow = document.querySelector('tr.mbt-row[data-idx="'+idx+'"]');
  if(activeRow) activeRow.classList.add('active');

  document.getElementById('mbt-detail').style.display='block';
  document.getElementById('mbt-detail-title').textContent = '📅 '+day.date+' — Detailed View';

  // Summary
  document.getElementById('mbt-d-total').textContent = day.total_trades;
  document.getElementById('mbt-d-wr').textContent = day.win_rate_pct+'%';
  const ptsEl = document.getElementById('mbt-d-pts');
  ptsEl.textContent = (day.total_points>=0?'+':'')+day.total_points+' pts';
  ptsEl.className = 'val '+(day.total_points>=0?'g':'r');
  const rsEl = document.getElementById('mbt-d-rs');
  rsEl.textContent = (day.total_est_rs>=0?'+':'')+'₹'+day.total_est_rs;
  rsEl.className = 'val '+(day.total_est_rs>=0?'g':'r');

  // Trade table
  if(day.trades && day.trades.length>0){
    document.getElementById('mbt-d-trades').style.display='block';
    const rows = day.trades.map(t=>{
      const sign = t.points>=0?'+':'';
      const sigPill = t.signal==='BUY_CE'
        ?'<span class="pill pill-ce">CE</span>'
        :'<span class="pill pill-pe">PE</span>';
      const resPill = t.result==='WIN'
        ?'<span class="pill pill-win">WIN</span>'
        :'<span class="pill pill-loss">LOSS</span>';
      const ptColor = t.points>=0?'#16a34a':'#dc2626';
      const reason = t.entry_reason||'—';
      const shortReason = reason.length>40 ? '<span title="'+reason.replace(/"/g,'&quot;')+'" style="cursor:help">'+reason.slice(0,38)+'…</span>' : reason;
      return '<tr>'
        +'<td>'+t.num+'</td>'
        +'<td>'+sigPill+'</td>'
        +'<td>'+t.entry_time+'</td>'
        +'<td>'+t.entry_nifty+'</td>'
        +'<td style="font-size:11px;color:#666">'+shortReason+'</td>'
        +'<td>'+t.exit_time+'</td>'
        +'<td>'+t.exit_nifty+'</td>'
        +'<td style="font-weight:700;color:'+ptColor+'">'+sign+t.points+' pts</td>'
        +'<td style="font-weight:700;color:'+ptColor+'">'+(t.est_options_pnl>=0?'+':'')+t.est_options_pnl+'</td>'
        +'<td><span class="pill pill-exit">'+t.exit_reason+'</span></td>'
        +'<td>'+resPill+'</td>'
        +'</tr>';
    }).join('');
    document.getElementById('mbt-d-trade-tbody').innerHTML = rows;
  } else {
    document.getElementById('mbt-d-trades').style.display='block';
    document.getElementById('mbt-d-trade-tbody').innerHTML = '<tr><td colspan="11" class="empty-state">No trades on this day</td></tr>';
  }

  // Candle timeline
  if(day.candles && day.candles.length>0){
    document.getElementById('mbt-d-candles').style.display='block';
    const rows = day.candles.map(c=>{
      const bg = c.in_trade ? 'background:#eff6ff' : '';
      const sigColor = c.signal==='BUY_CE'?'#0369a1':c.signal==='BUY_PE'?'#9d174d':'#aaa';
      const mktBadge = c.market_state==='TRENDING'
        ?'<span style="color:#16a34a;font-weight:700;font-size:10px">▲ TREND</span>'
        :c.market_state==='SIDEWAYS'
        ?'<span style="color:#f59e0b;font-weight:700;font-size:10px">↔ SIDE</span>'
        :'<span style="color:#aaa;font-size:10px">—</span>';
      const noteStyle = c.note.startsWith('ENTRY')
        ? 'color:#1d4ed8;font-weight:700'
        : c.note.startsWith('EXIT')
        ? 'color:#dc2626;font-weight:700'
        : 'color:#888';
      return '<tr style="'+bg+'">'
        +'<td style="font-weight:600">'+c.time+'</td>'
        +'<td>'+c.open+'</td>'
        +'<td>'+c.high+'</td>'
        +'<td>'+c.low+'</td>'
        +'<td style="font-weight:600">'+c.close+'</td>'
        +'<td>'+c.vwap+'</td>'
        +'<td>'+(c.ema20||'—')+'</td>'
        +'<td>'+mktBadge+'</td>'
        +'<td style="color:'+sigColor+';font-weight:700;font-size:11px">'+c.signal+'</td>'
        +'<td style="'+noteStyle+'">'+c.note+'</td>'
        +'</tr>';
    }).join('');
    document.getElementById('mbt-d-candle-tbody').innerHTML = rows;
  }

  // Scroll to detail
  document.getElementById('mbt-detail').scrollIntoView({behavior:'smooth',block:'start'});
}

function closeMultiDetail(){
  document.getElementById('mbt-detail').style.display='none';
  document.querySelectorAll('tr.mbt-row').forEach(r=>r.classList.remove('active'));
}

function mbtMsg(text, type){
  const el = document.getElementById('mbt-msg');
  el.textContent = text;
  el.style.display = text ? 'block' : 'none';
  el.style.background = type==='err'?'#fee2e2':type==='info'?'#eff6ff':'#dcfce7';
  el.style.color = type==='err'?'#991b1b':type==='info'?'#1e40af':'#166534';
}

// Init
refreshStatus();
loadTrades();
</script>
</body>
</html>
"""
