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
    logger.info("Logesh Auto Trading Engine starting up")
    yield
    logger.info("Logesh Auto Trading Engine shutting down")
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
    title="Logesh Auto Trading Engine",
    description="Automated Nifty Options Trading — VWAP+EMA Breakout Strategy",
    version="3.0.0",
    lifespan=lifespan,
)

app.include_router(auth.router)
app.include_router(trading.router)
app.include_router(auto_trading.router)
app.include_router(backtest.router)


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root():
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>Logesh Auto Trading Engine</title>
  <style>
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:system-ui,-apple-system,sans-serif;background:#f0f2f5;color:#111;min-height:100vh}

    /* ── HEADER ── */
    header{background:#0f172a;color:#fff;padding:10px 20px;position:sticky;top:0;z-index:100;box-shadow:0 2px 8px rgba(0,0,0,.3)}
    .hdr{display:flex;align-items:center;justify-content:space-between;max-width:1400px;margin:0 auto;gap:12px;flex-wrap:wrap}
    .hdr h1{font-size:17px;font-weight:800;color:#38bdf8;letter-spacing:-.3px;white-space:nowrap}
    .hdr .sub{font-size:10px;color:#64748b;margin-top:1px}
    .tabs{display:flex;gap:4px}
    .tab{background:transparent;border:1px solid rgba(255,255,255,.15);color:#94a3b8;padding:6px 16px;border-radius:7px;font-size:13px;font-weight:600;cursor:pointer;transition:all .15s}
    .tab:hover{border-color:rgba(255,255,255,.4);color:#e2e8f0}
    .tab.active{background:#0070f3;border-color:#0070f3;color:#fff}

    /* ── LAYOUT ── */
    .page{max-width:1400px;margin:0 auto;padding:14px 16px}

    /* ── CARDS ── */
    .card{background:#fff;border-radius:12px;padding:16px 20px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:14px}
    .card-title{font-size:13px;font-weight:700;color:#555;margin-bottom:12px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}

    /* ── ENGINE CARD ── */
    .engine-card{border:2px solid #dbeafe}
    .status-row{display:flex;align-items:center;gap:8px;background:#f0f6ff;border-radius:8px;padding:8px 12px;font-size:12px;margin-bottom:12px;flex-wrap:wrap}
    .dot{width:9px;height:9px;border-radius:50%;background:#ccc;flex-shrink:0}
    .dot.on{background:#22c55e;box-shadow:0 0 6px #22c55e88}.dot.off{background:#ef4444}
    .sep{color:#ccc;font-size:11px}
    .btn{display:inline-flex;align-items:center;gap:5px;padding:8px 16px;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:opacity .15s,transform .1s}
    .btn:hover{opacity:.87}.btn:active{transform:scale(.97)}
    .btn:disabled{opacity:.38;cursor:not-allowed;transform:none}
    .g-btn{background:#22c55e;color:#fff}.r-btn{background:#ef4444;color:#fff}
    .b-btn{background:#0070f3;color:#fff}.s-btn{background:#f1f5f9;color:#444;border:1px solid #e2e8f0}
    .btn-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px}
    .badge{font-size:10px;font-weight:700;padding:2px 8px;border-radius:8px}
    .bp{background:#fef9c3;color:#854d0e}.bl{background:#fee2e2;color:#991b1b}
    #msg{margin-top:8px;padding:8px 12px;border-radius:8px;font-size:12px;display:none}
    #msg.ok{background:#dcfce7;color:#166534;display:block}
    #msg.err{background:#fee2e2;color:#991b1b;display:block}
    .strat-note{font-size:10px;color:#aaa;margin-top:10px;line-height:1.7}

    /* ── MARKET DATA STRIP ── */
    .mstrip{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}
    @media(max-width:700px){.mstrip{grid-template-columns:repeat(2,1fr)}}
    .mtile{background:#fff;border-radius:10px;padding:12px 14px;box-shadow:0 1px 3px rgba(0,0,0,.07)}
    .mtile .lbl{font-size:10px;color:#999;text-transform:uppercase;letter-spacing:.4px}
    .mtile .val{font-size:20px;font-weight:800;color:#111;margin-top:3px}
    .mtile .sub{font-size:10px;font-weight:700;margin-top:3px}
    .sub-ce{color:#0369a1}.sub-pe{color:#9d174d}

    /* ── INDICATORS STRIP ── */
    .istrip{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-bottom:14px}
    @media(max-width:900px){.istrip{grid-template-columns:repeat(3,1fr)}}
    @media(max-width:500px){.istrip{grid-template-columns:repeat(2,1fr)}}
    .itile{background:#fff;border-radius:8px;padding:10px 12px;box-shadow:0 1px 3px rgba(0,0,0,.07)}
    .itile .lbl{font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:.3px}
    .itile .val{font-size:14px;font-weight:700;color:#111;margin-top:3px}

    /* ── CHART ── */
    .chart-card{padding:14px 16px}
    .chart-legend{display:flex;gap:14px;align-items:center;margin-bottom:8px;font-size:11px;flex-wrap:wrap}
    .leg{display:flex;align-items:center;gap:5px;color:#555}
    .leg-line{width:18px;height:3px;border-radius:2px}
    #chart-container{display:block;width:100%;height:380px;cursor:crosshair}
    #rsi-container{display:block;width:100%;height:110px;margin-top:3px;cursor:crosshair}
    #chart-empty{text-align:center;padding:60px 20px;color:#aaa;font-size:13px;border:2px dashed #e2e8f0;border-radius:10px}

    /* ── POSITION BANNER ── */
    #pos-banner{display:none;margin-bottom:14px;padding:14px 18px;border-radius:12px;background:linear-gradient(135deg,#eff6ff,#e0f2fe);border:1px solid #bfdbfe}
    .pos-row{display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px}
    .pos-sym{font-size:17px;font-weight:800;color:#1d4ed8}
    .pos-meta{font-size:12px;color:#64748b;margin-top:2px}
    .pos-sl{font-size:11px;color:#888;margin-top:4px}
    .pos-pnl{font-size:20px;font-weight:800}
    .pos-entry{font-size:12px;color:#64748b;margin-top:3px}

    /* ── TRADE TABLE ── */
    .sum-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:12px}
    @media(max-width:500px){.sum-grid{grid-template-columns:1fr 1fr}}
    .stat{background:#f8fafc;border-radius:8px;padding:10px 12px}
    .stat .lbl{font-size:10px;color:#999;text-transform:uppercase;letter-spacing:.4px}
    .stat .val{font-size:18px;font-weight:800;margin-top:3px;color:#111}
    .val.g{color:#16a34a}.val.r{color:#dc2626}.val.b{color:#0070f3}
    .tbl-wrap{overflow-x:auto;margin-top:10px}
    table{width:100%;border-collapse:collapse;font-size:12px}
    th{background:#f8fafc;padding:8px 10px;text-align:left;font-size:10px;color:#888;text-transform:uppercase;letter-spacing:.4px;border-bottom:2px solid #e2e8f0;white-space:nowrap}
    td{padding:8px 10px;border-bottom:1px solid #f1f5f9;vertical-align:middle;white-space:nowrap}
    tr:hover td{background:#fafbff}
    .pill{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:700}
    .pill-ce{background:#e0f2fe;color:#0369a1}
    .pill-pe{background:#fce7f3;color:#9d174d}
    .pill-win{background:#dcfce7;color:#15803d}
    .pill-loss{background:#fee2e2;color:#b91c1c}
    .pill-exit{background:#f1f5f9;color:#475569;font-size:10px}
    .empty-state{text-align:center;padding:32px;color:#aaa;font-size:13px}

    /* ── BACKTEST ── */
    .mbt-sum-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px;margin-bottom:10px}
    @media(max-width:700px){.mbt-sum-grid{grid-template-columns:repeat(3,1fr)}}
    tr.mbt-row{cursor:pointer;transition:background .12s}
    tr.mbt-row:hover td{background:#eff6ff}
    tr.mbt-row.active td{background:#dbeafe;font-weight:600}
    .cum-bar{display:inline-block;height:12px;border-radius:3px;min-width:2px;vertical-align:middle}

    /* ── LINKS ── */
    a{color:#0070f3;text-decoration:none;font-size:13px}
    a:hover{text-decoration:underline}
    .lrow{display:flex;align-items:center;gap:8px;margin:6px 0}
    .mt{font-size:10px;font-weight:700;padding:2px 6px;border-radius:4px;font-family:monospace}
    .mget{background:#dbeafe;color:#1e40af}
    .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
    @media(max-width:600px){.grid2{grid-template-columns:1fr}}
  </style>
</head>
<body>

<!-- ═══ HEADER ═══════════════════════════════════════════════════════════ -->
<header>
  <div class="hdr">
    <div>
      <h1>⚡ Logesh Auto Trading Engine</h1>
      <div class="sub">NIFTY VWAP+EMA Breakout &nbsp;·&nbsp; 5-min candles &nbsp;·&nbsp; Options</div>
    </div>
    <div class="tabs">
      <button class="tab active" id="tab-btn-dashboard" onclick="switchTab('dashboard')">Dashboard</button>
      <button class="tab" id="tab-btn-backtest" onclick="switchTab('backtest')">Backtest</button>
    </div>
  </div>
</header>

<!-- ═══ DASHBOARD TAB ════════════════════════════════════════════════════ -->
<div id="page-dashboard" class="page">

  <!-- ENGINE CONTROL -->
  <div class="card engine-card">
    <div class="card-title">
      Auto Trading Engine
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
      <span class="sep">|</span>
      <span id="atm-info" style="font-weight:700;color:#0070f3">ATM: —</span>
    </div>

    <div class="btn-row">
      <button class="btn g-btn" id="btn-start" onclick="startEngine()">▶ Start Engine</button>
      <button class="btn r-btn" id="btn-stop"  onclick="stopEngine()" disabled>■ Stop Engine</button>
      <button class="btn b-btn" onclick="refreshAll()">↻ Refresh</button>
    </div>

    <div id="msg"></div>

    <div class="strat-note">
      VWAP + EMA(20) · Dynamic SL (12–22%) · Trailing SL from +20% · RSI + Volume filters · Max 2 trades/day · 9:50–14:00 entries · Force exit 3:20 PM
    </div>
  </div>

  <!-- MARKET DATA STRIP -->
  <div class="mstrip" id="mstrip" style="display:none">
    <div class="mtile">
      <div class="lbl">Nifty Spot (Index)</div>
      <div class="val" id="m-spot">—</div>
    </div>
    <div class="mtile">
      <div class="lbl">Nifty Futures</div>
      <div class="val" id="m-fut">—</div>
    </div>
    <div class="mtile">
      <div class="lbl">ATM CE LTP</div>
      <div class="val" id="m-ce">—</div>
      <div class="sub sub-ce" id="m-ce-sym">—</div>
    </div>
    <div class="mtile">
      <div class="lbl">ATM PE LTP</div>
      <div class="val" id="m-pe">—</div>
      <div class="sub sub-pe" id="m-pe-sym">—</div>
    </div>
  </div>

  <!-- INDICATORS STRIP -->
  <div class="istrip" id="istrip" style="display:none">
    <div class="itile">
      <div class="lbl">VWAP</div>
      <div class="val" id="i-vwap" style="color:#f59e0b">—</div>
    </div>
    <div class="itile">
      <div class="lbl">EMA 20</div>
      <div class="val" id="i-ema" style="color:#0070f3">—</div>
    </div>
    <div class="itile">
      <div class="lbl">RSI 14</div>
      <div class="val" id="i-rsi" style="color:#7c3aed">—</div>
    </div>
    <div class="itile">
      <div class="lbl">Vol Surge</div>
      <div class="val" id="i-vol">—</div>
    </div>
    <div class="itile">
      <div class="lbl">Last Signal</div>
      <div class="val" id="i-sig">—</div>
    </div>
    <div class="itile">
      <div class="lbl">Last Candle</div>
      <div class="val" id="i-candle">—</div>
    </div>
  </div>

  <!-- LIVE CHART -->
  <div class="card chart-card">
    <div class="card-title" style="justify-content:space-between">
      <span>Live Chart — NIFTY 5-min</span>
      <div class="chart-legend" id="chart-legend" style="display:none">
        <div class="leg"><div class="leg-line" style="background:#f59e0b"></div>VWAP</div>
        <div class="leg"><div class="leg-line" style="background:#0070f3"></div>EMA 20</div>
        <div class="leg"><div class="leg-line" style="background:#7c3aed"></div>RSI 14</div>
        <div class="leg"><div class="leg-line" style="background:#ef4444;height:1px"></div>OB 70</div>
        <div class="leg"><div class="leg-line" style="background:#22c55e;height:1px"></div>OS 30</div>
      </div>
    </div>
    <div id="chart-empty">
      Start the engine to see live candlestick chart with VWAP &amp; EMA overlays.
    </div>
    <div id="chart-wrap" style="display:none">
      <canvas id="chart-container"></canvas>
      <canvas id="rsi-container"></canvas>
    </div>
  </div>

  <!-- OPEN POSITION BANNER -->
  <div id="pos-banner">
    <div class="pos-row">
      <div>
        <div class="pos-sym" id="pos-sym">—</div>
        <div class="pos-meta" id="pos-meta">—</div>
        <div class="pos-sl" id="pos-sl">—</div>
      </div>
      <div style="text-align:right">
        <div class="pos-pnl" id="pos-pnl">—</div>
        <div class="pos-entry" id="pos-entry">—</div>
      </div>
    </div>
  </div>

  <!-- PAPER TRADE LOG -->
  <div class="card">
    <div class="card-title" style="justify-content:space-between">
      <span>📋 Paper Trade Log</span>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="btn s-btn" style="padding:5px 12px;font-size:12px" onclick="loadTrades()">↻ Refresh</button>
        <a href="/auto-trading/paper-log/download" style="font-size:12px">⬇ CSV</a>
      </div>
    </div>
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
            <th>Entry</th><th>Entry ₹</th>
            <th>Exit</th><th>Exit ₹</th>
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

  <!-- CANDLE LOG DOWNLOAD -->
  <div class="card">
    <div class="card-title" style="justify-content:space-between">
      <span>📈 Candle Log</span>
      <div style="display:flex;gap:8px;align-items:center">
        <input type="date" id="candle-log-date" style="font-size:12px;padding:4px 8px;border:1px solid #ddd;border-radius:4px;background:#fff;color:#333" />
        <button class="btn s-btn" style="padding:5px 12px;font-size:12px" onclick="downloadCandleLog()">⬇ Download CSV</button>
      </div>
    </div>
    <div style="font-size:12px;color:#aaa">Select a date and download the 5-min candle log with all indicator snapshots.</div>
  </div>

  <!-- BOTTOM LINKS -->
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

</div><!-- /page-dashboard -->

<!-- ═══ BACKTEST TAB ══════════════════════════════════════════════════════ -->
<div id="page-backtest" class="page" style="display:none">

  <!-- SINGLE DAY BACKTEST -->
  <div class="card" id="backtest-card">
    <div class="card-title">🔁 Strategy Backtest — Single Day</div>

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

    <div id="bt-summary" style="display:none;margin-bottom:14px">
      <div class="sum-grid" style="grid-template-columns:repeat(4,1fr)">
        <div class="stat"><div class="lbl">Trades</div><div class="val b" id="bt-s-total">0</div></div>
        <div class="stat"><div class="lbl">Win Rate</div><div class="val g" id="bt-s-wr">—</div></div>
        <div class="stat"><div class="lbl">Total Points</div><div class="val" id="bt-s-pts">—</div></div>
        <div class="stat"><div class="lbl">Avg Win / Loss pts</div><div class="val" id="bt-s-avg">—</div></div>
      </div>
    </div>

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

  <!-- MULTI-DAY BACKTEST -->
  <div class="card" id="multi-bt-card">
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
      Runs strategy on each trading day in the range. Click any date row to see full trade details + candle timeline. Max 90 days.
    </p>

    <div id="mbt-msg" style="display:none;padding:9px 13px;border-radius:8px;font-size:12px;margin-bottom:10px"></div>
    <div id="mbt-progress" style="display:none;margin-bottom:10px">
      <div style="background:#e2e8f0;border-radius:6px;height:6px;overflow:hidden">
        <div id="mbt-progress-bar" style="width:0%;height:100%;background:#0070f3;border-radius:6px;transition:width .3s"></div>
      </div>
      <div style="font-size:11px;color:#888;margin-top:4px" id="mbt-progress-text"></div>
    </div>

    <div id="mbt-summary" style="display:none;margin-bottom:16px">
      <div class="mbt-sum-grid">
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

    <div id="mbt-detail" style="display:none;border:2px solid #dbeafe;border-radius:10px;padding:16px;background:#fafbff">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <div style="font-size:14px;font-weight:800;color:#1d4ed8" id="mbt-detail-title">—</div>
        <button class="btn s-btn" style="padding:4px 12px;font-size:11px" onclick="closeMultiDetail()">✕ Close</button>
      </div>
      <div class="sum-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:14px">
        <div class="stat"><div class="lbl">Trades</div><div class="val b" id="mbt-d-total">0</div></div>
        <div class="stat"><div class="lbl">Win Rate</div><div class="val g" id="mbt-d-wr">—</div></div>
        <div class="stat"><div class="lbl">Total Points</div><div class="val" id="mbt-d-pts">—</div></div>
        <div class="stat"><div class="lbl">Est ₹</div><div class="val" id="mbt-d-rs">—</div></div>
      </div>
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

</div><!-- /page-backtest -->

<script>
/* ══════════════════════════════════════════════════════════════
   TABS
══════════════════════════════════════════════════════════════ */
function switchTab(tab) {
  ['dashboard','backtest'].forEach(t => {
    document.getElementById('page-'+t).style.display = t===tab ? '' : 'none';
    document.getElementById('tab-btn-'+t).classList.toggle('active', t===tab);
  });
}

/* ══════════════════════════════════════════════════════════════
   CANVAS CHART — pure HTML5, no external library
   Data source: GET /auto-trading/candles (in-memory candles)
══════════════════════════════════════════════════════════════ */
let chart = null;
let chartReady = false;

function niceStep(range, targetSteps) {
  const raw = range / targetSteps;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const n = raw / mag;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag;
}

function fmtTime(unixSec) {
  const d = new Date(unixSec * 1000);
  return d.getUTCHours().toString().padStart(2,'0') + ':' + d.getUTCMinutes().toString().padStart(2,'0');
}

function rrect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x+r, y);
  ctx.lineTo(x+w-r, y); ctx.quadraticCurveTo(x+w, y, x+w, y+r);
  ctx.lineTo(x+w, y+h-r); ctx.quadraticCurveTo(x+w, y+h, x+w-r, y+h);
  ctx.lineTo(x+r, y+h); ctx.quadraticCurveTo(x, y+h, x, y+h-r);
  ctx.lineTo(x, y+r); ctx.quadraticCurveTo(x, y, x+r, y);
  ctx.closePath();
}

class CandleChart {
  constructor(mc, rc) {
    this.mc = mc; this.rc = rc;
    this.mctx = mc.getContext('2d');
    this.rctx = rc.getContext('2d');
    this.candles = [];
    this.marker = null;
    this.hoverIdx = -1;

    mc.addEventListener('mousemove', e => this._hover(e));
    mc.addEventListener('mouseleave', () => { this.hoverIdx = -1; this._draw(); });

    new ResizeObserver(() => this._resize()).observe(mc.parentElement);
  }

  _resize() {
    const w = this.mc.parentElement.clientWidth;
    if (!w) return;
    this.mc.width = w; this.mc.style.width = w+'px';
    this.rc.width = w; this.rc.style.width = w+'px';
    this._draw();
  }

  setData(candles, marker) {
    this.candles = candles;
    this.marker  = marker;
    this._resize();
  }

  _hover(e) {
    if (!this.candles.length) return;
    const rect = this.mc.getBoundingClientRect();
    const x = (e.clientX - rect.left) * (this.mc.width / rect.width);
    const PR = 65, PL = 8;
    const cw = (this.mc.width - PL - PR) / this.candles.length;
    const idx = Math.floor((x - PL) / cw);
    if (idx >= 0 && idx < this.candles.length && idx !== this.hoverIdx) {
      this.hoverIdx = idx;
      this._draw();
    }
  }

  _draw() { this._drawMain(); this._drawRsi(); }

  _drawMain() {
    const { mc, mctx: ctx, candles, marker, hoverIdx } = this;
    const W = mc.width, H = mc.height;
    if (!W || !H) return;
    ctx.clearRect(0, 0, W, H);
    if (!candles.length) return;

    const PL = 8, PR = 65, PT = 16, PB = 28;
    const chartW = W - PL - PR;
    const chartH = H - PT - PB;
    const volH   = Math.max(30, Math.floor(chartH * 0.14));
    const priceH = chartH - volH - 4;

    const n  = candles.length;
    const cw = chartW / n;
    const bw = Math.max(1, Math.floor(cw * 0.6));
    const toX = i => PL + i * cw + cw / 2;

    // Price range
    let lo = Infinity, hi = -Infinity;
    for (const c of candles) {
      lo = Math.min(lo, c.low);  hi = Math.max(hi, c.high);
      if (c.vwap  != null) { lo = Math.min(lo, c.vwap);  hi = Math.max(hi, c.vwap);  }
      if (c.ema20 != null) { lo = Math.min(lo, c.ema20); hi = Math.max(hi, c.ema20); }
    }
    const pad = (hi - lo) * 0.06 || 1;
    lo -= pad; hi += pad;
    const toY = p => PT + priceH * (1 - (p - lo) / (hi - lo));

    // Volume
    const maxVol = Math.max(...candles.map(c => c.volume || 0), 1);
    const volTop = PT + priceH + 4;
    const toVH   = v => Math.max(1, (v / maxVol) * (volH - 2));

    // Background
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, W, H);

    // Horizontal grid + price axis
    const step = niceStep(hi - lo, 6);
    const first = Math.ceil(lo / step) * step;
    ctx.textAlign = 'left'; ctx.font = '10px system-ui'; ctx.fillStyle = '#94a3b8';
    for (let p = first; p <= hi + step * 0.1; p += step) {
      const y = toY(p);
      if (y < PT - 2 || y > PT + priceH + 2) continue;
      ctx.strokeStyle = '#f1f5f9'; ctx.lineWidth = 1; ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(PL, y); ctx.lineTo(W - PR, y); ctx.stroke();
      ctx.fillText(p.toFixed(0), W - PR + 5, y + 3);
    }

    // Time axis ticks (one label every ~60px)
    ctx.textAlign = 'center'; ctx.fillStyle = '#94a3b8'; ctx.font = '10px system-ui';
    const tStep = Math.max(1, Math.round(60 / cw));
    for (let i = 0; i < n; i += tStep) {
      ctx.fillText(fmtTime(candles[i].time), toX(i), H - PB + 14);
    }

    // Volume bars
    for (let i = 0; i < n; i++) {
      const c = candles[i];
      const x = toX(i), vh = toVH(c.volume || 0);
      ctx.fillStyle = c.close >= c.open ? '#bbf7d0' : '#fecaca';
      ctx.fillRect(x - bw/2, volTop + volH - vh, bw, vh);
    }

    // VWAP line
    ctx.strokeStyle = '#f59e0b'; ctx.lineWidth = 1.5; ctx.setLineDash([]);
    ctx.beginPath(); let started = false;
    for (let i = 0; i < n; i++) {
      if (candles[i].vwap == null) continue;
      const x = toX(i), y = toY(candles[i].vwap);
      started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true);
    }
    ctx.stroke();

    // EMA20 line
    ctx.strokeStyle = '#0070f3'; ctx.lineWidth = 1.5;
    ctx.beginPath(); started = false;
    for (let i = 0; i < n; i++) {
      if (candles[i].ema20 == null) continue;
      const x = toX(i), y = toY(candles[i].ema20);
      started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true);
    }
    ctx.stroke();

    // Candles
    for (let i = 0; i < n; i++) {
      const c = candles[i];
      const x = toX(i);
      const isUp  = c.close >= c.open;
      const body  = isUp ? '#22c55e' : '#ef4444';
      const wick  = isUp ? '#15803d' : '#b91c1c';
      ctx.strokeStyle = wick; ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, toY(c.high)); ctx.lineTo(x, toY(c.low));
      ctx.stroke();
      const bTop = toY(Math.max(c.open, c.close));
      const bBot = toY(Math.min(c.open, c.close));
      ctx.fillStyle = body;
      ctx.fillRect(x - bw/2, bTop, bw, Math.max(1, bBot - bTop));
    }

    // Entry marker
    if (marker) {
      const mi = candles.findIndex(c => c.time === marker.time);
      if (mi >= 0) {
        const x = toX(mi);
        const isCE = marker.type === 'CE';
        const cy = isCE ? toY(candles[mi].low) + 14 : toY(candles[mi].high) - 14;
        ctx.fillStyle = isCE ? '#16a34a' : '#dc2626';
        ctx.font = '13px system-ui'; ctx.textAlign = 'center';
        ctx.fillText(isCE ? '▲' : '▼', x, cy);
        ctx.font = 'bold 8px system-ui';
        ctx.fillText('ENTRY', x, isCE ? cy + 10 : cy - 5);
      }
    }

    // Chart border
    ctx.strokeStyle = '#e2e8f0'; ctx.lineWidth = 1; ctx.setLineDash([]);
    ctx.strokeRect(PL, PT, chartW, priceH);

    // Crosshair + tooltip
    if (hoverIdx >= 0 && hoverIdx < n) {
      const c = candles[hoverIdx];
      const x = toX(hoverIdx);

      ctx.strokeStyle = 'rgba(100,116,139,0.4)'; ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(x, PT); ctx.lineTo(x, PT + priceH); ctx.stroke();
      ctx.setLineDash([]);

      // Tooltip box
      const lines = [
        fmtTime(c.time),
        'O: '+c.open.toFixed(1)+'  H: '+c.high.toFixed(1),
        'L: '+c.low.toFixed(1)+'  C: '+c.close.toFixed(1),
        c.vwap  != null ? 'VWAP: '+c.vwap.toFixed(2)   : null,
        c.ema20 != null ? 'EMA20: '+c.ema20.toFixed(2)  : null,
        c.rsi14 != null ? 'RSI14: '+c.rsi14.toFixed(1)  : null,
        c.volume > 0    ? 'Vol: '+c.volume.toLocaleString() : null,
      ].filter(Boolean);

      const TW = 148, TH = lines.length * 16 + 12;
      let tx = x + 10;
      if (tx + TW > W - PR - 4) tx = x - TW - 10;
      const ty = PT + 6;

      ctx.fillStyle = 'rgba(15,23,42,0.88)';
      rrect(ctx, tx, ty, TW, TH, 5); ctx.fill();
      ctx.fillStyle = '#f1f5f9'; ctx.font = '11px system-ui'; ctx.textAlign = 'left';
      lines.forEach((l, j) => {
        ctx.fillStyle = j === 0 ? '#7dd3fc' : j <= 2 ? '#e2e8f0'
          : l.startsWith('VWAP') ? '#fcd34d'
          : l.startsWith('EMA')  ? '#93c5fd'
          : l.startsWith('RSI')  ? '#c4b5fd'
          : '#94a3b8';
        ctx.fillText(l, tx + 8, ty + 14 + j * 16);
      });
    }
  }

  _drawRsi() {
    const { rc, rctx: ctx, candles, hoverIdx } = this;
    const W = rc.width, H = rc.height;
    if (!W || !H) return;
    ctx.clearRect(0, 0, W, H);

    const PL = 8, PR = 65, PT = 8, PB = 18;
    const chartW = W - PL - PR;
    const chartH = H - PT - PB;
    const n = candles.length;
    const cw = chartW / n;
    const toX = i => PL + i * cw + cw / 2;
    const toY = v => PT + chartH * (1 - (v / 100));

    ctx.fillStyle = '#fafbff'; ctx.fillRect(0, 0, W, H);

    // OB / OS / mid levels
    for (const [level, color, dash] of [[70,'#fee2e2',[4,4]],[50,'#e2e8f0',[]],[30,'#dcfce7',[4,4]]]) {
      const y = toY(level);
      ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash(dash);
      ctx.beginPath(); ctx.moveTo(PL, y); ctx.lineTo(W - PR, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = '#94a3b8'; ctx.font = '9px system-ui'; ctx.textAlign = 'left';
      ctx.fillText(level, W - PR + 5, y + 3);
    }

    // RSI line
    ctx.strokeStyle = '#7c3aed'; ctx.lineWidth = 1.5;
    ctx.beginPath(); let started = false;
    for (let i = 0; i < n; i++) {
      if (candles[i].rsi14 == null) continue;
      const x = toX(i), y = toY(candles[i].rsi14);
      started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true);
    }
    ctx.stroke();

    // Label
    ctx.fillStyle = '#7c3aed'; ctx.font = 'bold 9px system-ui'; ctx.textAlign = 'left';
    ctx.fillText('RSI 14', PL + 4, PT + 10);

    // Border
    ctx.strokeStyle = '#e2e8f0'; ctx.lineWidth = 1; ctx.setLineDash([]);
    ctx.strokeRect(PL, PT, chartW, chartH);

    // Crosshair
    if (hoverIdx >= 0 && hoverIdx < n && candles[hoverIdx].rsi14 != null) {
      const x = toX(hoverIdx);
      ctx.strokeStyle = 'rgba(100,116,139,0.4)'; ctx.lineWidth = 1; ctx.setLineDash([4,4]);
      ctx.beginPath(); ctx.moveTo(x, PT); ctx.lineTo(x, PT + chartH); ctx.stroke();
      ctx.setLineDash([]);
      const rsi = candles[hoverIdx].rsi14;
      ctx.fillStyle = rsi >= 70 ? '#dc2626' : rsi <= 30 ? '#16a34a' : '#7c3aed';
      ctx.font = 'bold 10px system-ui'; ctx.textAlign = 'right';
      ctx.fillText(rsi.toFixed(1), W - PR - 4, PT + 10);
    }
  }
}

function initChart() {
  if (chartReady) return;
  const mc = document.getElementById('chart-container');
  const rc = document.getElementById('rsi-container');
  mc.height = 380; rc.height = 110;
  chart = new CandleChart(mc, rc);
  chartReady = true;
  document.getElementById('chart-empty').style.display = 'none';
  document.getElementById('chart-wrap').style.display = 'block';
  document.getElementById('chart-legend').style.display = 'flex';
}

function updateChart(candles, status) {
  if (!candles || !candles.length) return;
  initChart();

  const todayCandles = candles.filter(c => c.is_today);
  if (!todayCandles.length) return;

  let marker = null;
  if (status && status.position && status.position.entry_time) {
    const entryHHMM = status.position.entry_time.slice(0, 5);
    const found = todayCandles.find(c => fmtTime(c.time) === entryHHMM);
    if (found) marker = { time: found.time, type: status.position.option_type };
  }

  chart.setData(todayCandles, marker);
}

/* ══════════════════════════════════════════════════════════════
   ENGINE CONTROL
══════════════════════════════════════════════════════════════ */
let autoRefresh = null;

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
    await refreshAll();
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
  await refreshAll();
  loadTrades();
}

async function refreshAll() {
  try {
    const [sResp, cResp] = await Promise.all([
      fetch('/auto-trading/status'),
      fetch('/auto-trading/candles'),
    ]);
    const status     = await sResp.json();
    const candleData = await cResp.json();
    updateEngineUI(status);
    updateChart(candleData.candles, status);
  } catch(e){}
}

function updateEngineUI(d) {
  document.getElementById('engine-dot').className = 'dot '+(d.engine_running?'on':'off');
  document.getElementById('engine-status-text').textContent = d.engine_running?'Engine running ('+d.mode+')':'Engine stopped';
  document.getElementById('trades-count').textContent = 'Trades: '+d.trades_today+' / '+d.max_trades;
  document.getElementById('candle-info').textContent = 'Candles: '+d.candle_count+' / '+d.candles_needed;
  document.getElementById('market-state-top').textContent = 'Market: '+(d.market_state||'—');

  if(d.instruments && d.instruments.atm_strike) {
    document.getElementById('atm-info').textContent = 'ATM: '+d.instruments.atm_strike;
  }

  const badge = document.getElementById('mode-badge');
  badge.textContent = d.mode||'PAPER';
  badge.className = 'badge '+(d.mode==='LIVE'?'bl':'bp');

  document.getElementById('btn-start').disabled = d.engine_running;
  document.getElementById('btn-stop').disabled  = !d.engine_running;

  if(d.engine_running && !autoRefresh) startAutoRefresh();
  else if(!d.engine_running && autoRefresh) stopAutoRefresh();

  const show = d.engine_running || d.nifty_spot > 0;
  document.getElementById('mstrip').style.display = show ? 'grid' : 'none';
  document.getElementById('istrip').style.display = show ? 'grid' : 'none';

  if(show) {
    document.getElementById('m-spot').textContent = d.nifty_spot>0 ? d.nifty_spot.toFixed(2) : '—';
    document.getElementById('m-fut').textContent  = d.nifty_futures_ltp>0 ? d.nifty_futures_ltp.toFixed(2) : '—';
    document.getElementById('m-ce').textContent   = d.ce_ltp>0 ? '₹'+d.ce_ltp.toFixed(2) : '—';
    document.getElementById('m-pe').textContent   = d.pe_ltp>0 ? '₹'+d.pe_ltp.toFixed(2) : '—';
    if(d.instruments) {
      document.getElementById('m-ce-sym').textContent = d.instruments.ce||'—';
      document.getElementById('m-pe-sym').textContent = d.instruments.pe||'—';
    }

    // Indicators
    const ind = d.indicators || {};
    document.getElementById('i-vwap').textContent  = ind.vwap ? ind.vwap.toFixed(2) : '—';
    document.getElementById('i-ema').textContent   = ind.ema20 ? ind.ema20.toFixed(2) : '—';

    const rsiEl = document.getElementById('i-rsi');
    if(ind.rsi14 != null) {
      rsiEl.textContent = ind.rsi14.toFixed(1);
      rsiEl.style.color = ind.rsi14 >= 70 ? '#dc2626' : ind.rsi14 <= 30 ? '#16a34a' : '#7c3aed';
    } else {
      rsiEl.textContent = '—';
    }

    const volEl = document.getElementById('i-vol');
    if(ind.volume_surge != null) {
      volEl.textContent = ind.volume_surge ? 'YES' : 'NO';
      volEl.style.color = ind.volume_surge ? '#16a34a' : '#ef4444';
    } else {
      volEl.textContent = '—'; volEl.style.color = '#111';
    }

    const sigEl = document.getElementById('i-sig');
    const sig = d.last_signal || '—';
    sigEl.textContent = sig;
    sigEl.style.color = sig==='BUY_CE'?'#0369a1':sig==='BUY_PE'?'#9d174d':sig==='NO_SIGNAL'?'#888':'#111';

    document.getElementById('i-candle').textContent = d.last_candle_time||'—';
  }

  // Position banner
  const banner = document.getElementById('pos-banner');
  if(d.position) {
    banner.style.display='block';
    const p = d.position;
    const pnl = d.pnl;
    document.getElementById('pos-sym').textContent = p.symbol;
    document.getElementById('pos-meta').textContent =
      p.option_type+' · Strike '+p.strike+' · Expiry '+p.expiry+' · Qty '+p.qty;
    document.getElementById('pos-sl').textContent =
      p.trail_active ? 'Trail SL: ₹'+p.trailing_sl+' (trailing active)' : 'SL: ₹'+p.trailing_sl;
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

/* ══════════════════════════════════════════════════════════════
   CANDLE LOG DOWNLOAD
══════════════════════════════════════════════════════════════ */
(function initCandleLogDate() {
  const today = new Date().toISOString().slice(0, 10);
  const el = document.getElementById('candle-log-date');
  if (el) el.value = today;
})();

function downloadCandleLog() {
  const el = document.getElementById('candle-log-date');
  const date = el ? el.value : '';
  if (!date) { alert('Please select a date.'); return; }
  window.location.href = '/auto-trading/candle-log/download/' + date;
}

/* ══════════════════════════════════════════════════════════════
   PAPER TRADE LOG
══════════════════════════════════════════════════════════════ */
async function loadTrades() {
  try {
    const r = await fetch('/auto-trading/paper-log');
    const d = await r.json();
    renderSummary(d.summary);
    renderTrades(d.trades);
  } catch(e){}
}

function renderSummary(s) {
  if(!s || s.total_trades===0){ document.getElementById('sum-grid').style.display='none'; return; }
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
  if(!trades||trades.length===0){
    tbody.innerHTML='<tr><td colspan="12" class="empty-state">No paper trades yet.</td></tr>';
    return;
  }
  const rows = [...trades].reverse().map(t => {
    const pnl  = parseFloat(t.pnl_rupees||0);
    const pct  = parseFloat(t.pnl_pct||0);
    const sign = pnl>=0?'+':'';
    const typeP = t.option_type==='CE'
      ?'<span class="pill pill-ce">CE</span>'
      :'<span class="pill pill-pe">PE</span>';
    const pnlP  = pnl>=0
      ?'<span class="pill pill-win">'+sign+'₹'+pnl+'</span>'
      :'<span class="pill pill-loss">₹'+pnl+'</span>';
    const pctP  = pnl>=0
      ?'<span style="color:#16a34a;font-weight:700">'+sign+pct+'%</span>'
      :'<span style="color:#dc2626;font-weight:700">'+pct+'%</span>';
    const entryR = t.reason_for_entry
      ?'<span title="'+t.reason_for_entry+'" style="cursor:help;color:#888">ℹ hover</span>'
      :'—';
    return '<tr>'
      +'<td>'+t.trade_number+'</td>'
      +'<td>'+t.date+'</td>'
      +'<td>'+typeP+'</td>'
      +'<td style="font-weight:600;font-size:11px">'+t.option_symbol+'</td>'
      +'<td>'+t.entry_time+'</td>'
      +'<td>₹'+t.entry_price+'</td>'
      +'<td>'+t.exit_time+'</td>'
      +'<td>₹'+t.exit_price+'</td>'
      +'<td>'+pnlP+'</td>'
      +'<td>'+pctP+'</td>'
      +'<td><span class="pill pill-exit">'+t.reason_for_exit+'</span></td>'
      +'<td>'+entryR+'</td>'
      +'</tr>';
  }).join('');
  tbody.innerHTML = rows;
}

/* ══════════════════════════════════════════════════════════════
   HELPERS
══════════════════════════════════════════════════════════════ */
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
  autoRefresh = setInterval(()=>{refreshAll();loadTrades();},5000);
}
function stopAutoRefresh() {
  if(autoRefresh){clearInterval(autoRefresh);autoRefresh=null;}
}

/* ══════════════════════════════════════════════════════════════
   BACKTEST — SINGLE DAY
══════════════════════════════════════════════════════════════ */
function localDateStr(d) {
  return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');
}
(function initDatePicker(){
  const inp = document.getElementById('bt-date');
  const d = new Date(); d.setDate(d.getDate()-1);
  inp.value = localDateStr(d); inp.max = localDateStr(d);
})();

async function runBacktest() {
  const dateVal = document.getElementById('bt-date').value;
  if(!dateVal){btMsg('Please select a date','err');return;}
  const btn = document.getElementById('bt-btn');
  btn.disabled=true; btn.textContent='⏳ Running…';
  btMsg('Fetching historical data and replaying strategy…','info');
  ['bt-summary','bt-trades','bt-candles'].forEach(id=>document.getElementById(id).style.display='none');
  try {
    const r = await fetch('/backtest/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({date:dateVal})});
    const d = await r.json();
    if(!r.ok){btMsg(d.detail||'Error running backtest','err');return;}
    btMsg('','');
    renderBacktest(d);
  } catch(e){btMsg('Network error: '+e,'err');}
  finally{btn.disabled=false;btn.textContent='▶ Run Backtest';}
}

function renderBacktest(d) {
  const s = d.summary;
  document.getElementById('bt-summary').style.display='block';
  document.getElementById('bt-s-total').textContent = s.total_trades;
  document.getElementById('bt-s-wr').textContent = s.win_rate_pct+'%';
  const ptsEl=document.getElementById('bt-s-pts');
  const estRs=s.total_est_rs||0;
  ptsEl.textContent=(s.total_points>=0?'+':'')+s.total_points+' pts ('+(estRs>=0?'+':'')+'₹'+estRs+')';
  ptsEl.className='val '+(s.total_points>=0?'g':'r');
  document.getElementById('bt-s-avg').textContent='+'+s.avg_win_points+' / '+s.avg_loss_points;

  if(d.trades&&d.trades.length>0){
    document.getElementById('bt-trades').style.display='block';
    document.getElementById('bt-trade-tbody').innerHTML=d.trades.map(t=>{
      const sign=t.points>=0?'+':'';
      const sigPill=t.signal==='BUY_CE'?'<span class="pill pill-ce">CE</span>':'<span class="pill pill-pe">PE</span>';
      const resPill=t.result==='WIN'?'<span class="pill pill-win">WIN</span>':'<span class="pill pill-loss">LOSS</span>';
      const ptColor=t.points>=0?'#16a34a':'#dc2626';
      return '<tr><td>'+t.num+'</td><td>'+sigPill+'</td><td>'+t.entry_time+'</td><td>'+t.entry_nifty+'</td><td>'+t.exit_time+'</td><td>'+t.exit_nifty+'</td>'
        +'<td style="font-weight:700;color:'+ptColor+'">'+sign+t.points+' pts</td>'
        +'<td style="font-weight:700;color:'+ptColor+'">'+(t.est_options_pnl>=0?'+':'')+t.est_options_pnl+'</td>'
        +'<td><span class="pill pill-exit">'+t.exit_reason+'</span></td><td>'+resPill+'</td></tr>';
    }).join('');
  }

  if(d.candles&&d.candles.length>0){
    document.getElementById('bt-candles').style.display='block';
    document.getElementById('bt-candle-tbody').innerHTML=d.candles.map(c=>candleRow(c)).join('');
  }
}

function candleRow(c) {
  const bg=c.in_trade?'background:#eff6ff':'';
  const sigColor=c.signal==='BUY_CE'?'#0369a1':c.signal==='BUY_PE'?'#9d174d':'#aaa';
  const mktBadge=c.market_state==='TRENDING'
    ?'<span style="color:#16a34a;font-weight:700;font-size:10px">▲ TREND</span>'
    :c.market_state==='SIDEWAYS'
    ?'<span style="color:#f59e0b;font-weight:700;font-size:10px">↔ SIDE</span>'
    :'<span style="color:#aaa;font-size:10px">—</span>';
  const noteStyle=c.note.startsWith('ENTRY')?'color:#1d4ed8;font-weight:700':c.note.startsWith('EXIT')?'color:#dc2626;font-weight:700':'color:#888';
  return '<tr style="'+bg+'"><td style="font-weight:600">'+c.time+'</td><td>'+c.open+'</td><td>'+c.high+'</td><td>'+c.low+'</td>'
    +'<td style="font-weight:600">'+c.close+'</td><td>'+c.vwap+'</td><td>'+(c.ema20||'—')+'</td><td>'+mktBadge+'</td>'
    +'<td style="color:'+sigColor+';font-weight:700;font-size:11px">'+c.signal+'</td>'
    +'<td style="'+noteStyle+'">'+c.note+'</td></tr>';
}

function btMsg(text,type){
  const el=document.getElementById('bt-msg');
  el.textContent=text; el.style.display=text?'block':'none';
  el.style.background=type==='err'?'#fee2e2':type==='info'?'#eff6ff':'#dcfce7';
  el.style.color=type==='err'?'#991b1b':type==='info'?'#1e40af':'#166534';
}

/* ══════════════════════════════════════════════════════════════
   BACKTEST — MULTI DAY
══════════════════════════════════════════════════════════════ */
let multiData = null;

(function initMultiDatePickers(){
  const to=document.getElementById('mbt-to');
  const fr=document.getElementById('mbt-from');
  const d=new Date(); d.setDate(d.getDate()-1);
  to.value=localDateStr(d); to.max=localDateStr(d);
  const f=new Date(); f.setDate(f.getDate()-11);
  fr.value=localDateStr(f); fr.max=localDateStr(d);
})();

function setMultiRange(days){
  const to=document.getElementById('mbt-to');
  const fr=document.getElementById('mbt-from');
  const d=new Date(); d.setDate(d.getDate()-1);
  to.value=localDateStr(d);
  const f=new Date(); f.setDate(f.getDate()-1-days);
  fr.value=localDateStr(f);
}

async function runMultiBacktest(){
  const fromVal=document.getElementById('mbt-from').value;
  const toVal=document.getElementById('mbt-to').value;
  if(!fromVal||!toVal){mbtMsg('Select both dates','err');return;}
  const btn=document.getElementById('mbt-btn');
  btn.disabled=true; btn.textContent='⏳ Running…';
  mbtMsg('Fetching data & replaying strategy for each trading day…','info');
  document.getElementById('mbt-progress').style.display='block';
  document.getElementById('mbt-progress-bar').style.width='10%';
  document.getElementById('mbt-progress-text').textContent='Connecting to Kite API…';
  ['mbt-summary','mbt-daily','mbt-detail'].forEach(id=>document.getElementById(id).style.display='none');
  try{
    const r=await fetch('/backtest/run-multi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({from_date:fromVal,to_date:toVal})});
    const d=await r.json();
    if(!r.ok){mbtMsg(d.detail||'Error','err');return;}
    document.getElementById('mbt-progress-bar').style.width='100%';
    document.getElementById('mbt-progress-text').textContent='Done!';
    setTimeout(()=>{document.getElementById('mbt-progress').style.display='none';},800);
    mbtMsg('','');
    multiData=d;
    renderMultiBacktest(d);
  }catch(e){mbtMsg('Network error: '+e,'err');}
  finally{btn.disabled=false;btn.textContent='▶ Run Multi-Day';}
}

function renderMultiBacktest(d){
  const a=d.aggregate;
  document.getElementById('mbt-summary').style.display='block';
  document.getElementById('mbt-days').textContent=d.trading_days;
  document.getElementById('mbt-total').textContent=a.total_trades;
  document.getElementById('mbt-wr').textContent=a.win_rate_pct+'%';
  const pnlEl=document.getElementById('mbt-pnl');
  const sign=a.total_points>=0?'+':'';
  pnlEl.textContent=sign+a.total_points+' pts ('+(a.total_est_rs>=0?'+':'')+'₹'+a.total_est_rs+')';
  pnlEl.className='val '+(a.total_points>=0?'g':'r');
  document.getElementById('mbt-dd').textContent='-'+a.max_drawdown_pts+' pts';
  document.getElementById('mbt-avgw').textContent='+'+a.avg_win_points;
  document.getElementById('mbt-avgl').textContent=a.avg_loss_points;
  document.getElementById('mbt-best').textContent=a.best_day+' (+'+a.best_day_pts+')';
  document.getElementById('mbt-worst').textContent=a.worst_day+' ('+a.worst_day_pts+')';

  if(d.daily&&d.daily.length>0){
    document.getElementById('mbt-daily').style.display='block';
    const dow=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
    let cumPts=0;
    const maxAbsCum=Math.max(...d.daily.reduce((acc,day)=>{cumPts+=day.total_points;acc.push(Math.abs(cumPts));return acc;},[]),1);
    cumPts=0;
    document.getElementById('mbt-daily-tbody').innerHTML=d.daily.map((day,idx)=>{
      cumPts+=day.total_points;
      const dt=new Date(day.date+'T00:00:00');
      const dayName=dow[dt.getDay()];
      const ptColor=day.total_points>=0?'#16a34a':'#dc2626';
      const rsColor=day.total_est_rs>=0?'#16a34a':'#dc2626';
      const cumColor=cumPts>=0?'#16a34a':'#dc2626';
      const barW=Math.max(2,Math.abs(cumPts)/maxAbsCum*60);
      const barColor=cumPts>=0?'#22c55e':'#ef4444';
      const wrColor=day.win_rate_pct>=50?'#16a34a':day.win_rate_pct>0?'#f59e0b':'#dc2626';
      return '<tr class="mbt-row" data-idx="'+idx+'" onclick="showMultiDetail('+idx+')">'
        +'<td style="font-weight:600">'+day.date+'</td><td>'+dayName+'</td>'
        +'<td>'+day.total_trades+'</td>'
        +'<td style="color:#16a34a">'+day.wins+'</td>'
        +'<td style="color:#dc2626">'+day.losses+'</td>'
        +'<td style="color:'+wrColor+';font-weight:700">'+day.win_rate_pct+'%</td>'
        +'<td style="font-weight:700;color:'+ptColor+'">'+(day.total_points>=0?'+':'')+day.total_points+'</td>'
        +'<td style="color:'+rsColor+'">'+(day.total_est_rs>=0?'+':'')+'₹'+day.total_est_rs+'</td>'
        +'<td><span style="color:'+cumColor+';font-weight:700;margin-right:6px">'+(cumPts>=0?'+':'')+cumPts.toFixed(1)+'</span>'
        +'<span class="cum-bar" style="width:'+barW+'px;background:'+barColor+'"></span></td></tr>';
    }).join('');
  }
}

function showMultiDetail(idx){
  if(!multiData||!multiData.daily[idx]) return;
  const day=multiData.daily[idx];
  document.querySelectorAll('tr.mbt-row').forEach(r=>r.classList.remove('active'));
  const activeRow=document.querySelector('tr.mbt-row[data-idx="'+idx+'"]');
  if(activeRow) activeRow.classList.add('active');
  document.getElementById('mbt-detail').style.display='block';
  document.getElementById('mbt-detail-title').textContent='📅 '+day.date+' — Detailed View';
  document.getElementById('mbt-d-total').textContent=day.total_trades;
  document.getElementById('mbt-d-wr').textContent=day.win_rate_pct+'%';
  const ptsEl=document.getElementById('mbt-d-pts');
  ptsEl.textContent=(day.total_points>=0?'+':'')+day.total_points+' pts';
  ptsEl.className='val '+(day.total_points>=0?'g':'r');
  const rsEl=document.getElementById('mbt-d-rs');
  rsEl.textContent=(day.total_est_rs>=0?'+':'')+'₹'+day.total_est_rs;
  rsEl.className='val '+(day.total_est_rs>=0?'g':'r');

  document.getElementById('mbt-d-trade-tbody').innerHTML=(day.trades&&day.trades.length>0)
    ? day.trades.map(t=>{
        const sign=t.points>=0?'+':'';
        const sigPill=t.signal==='BUY_CE'?'<span class="pill pill-ce">CE</span>':'<span class="pill pill-pe">PE</span>';
        const resPill=t.result==='WIN'?'<span class="pill pill-win">WIN</span>':'<span class="pill pill-loss">LOSS</span>';
        const ptColor=t.points>=0?'#16a34a':'#dc2626';
        const reason=t.entry_reason||'—';
        const shortReason=reason.length>40?'<span title="'+reason.replace(/"/g,'&quot;')+'" style="cursor:help">'+reason.slice(0,38)+'…</span>':reason;
        return '<tr><td>'+t.num+'</td><td>'+sigPill+'</td><td>'+t.entry_time+'</td><td>'+t.entry_nifty+'</td>'
          +'<td style="font-size:11px;color:#666">'+shortReason+'</td>'
          +'<td>'+t.exit_time+'</td><td>'+t.exit_nifty+'</td>'
          +'<td style="font-weight:700;color:'+ptColor+'">'+sign+t.points+' pts</td>'
          +'<td style="font-weight:700;color:'+ptColor+'">'+(t.est_options_pnl>=0?'+':'')+t.est_options_pnl+'</td>'
          +'<td><span class="pill pill-exit">'+t.exit_reason+'</span></td><td>'+resPill+'</td></tr>';
      }).join('')
    : '<tr><td colspan="11" class="empty-state">No trades on this day</td></tr>';

  document.getElementById('mbt-d-candle-tbody').innerHTML=(day.candles&&day.candles.length>0)
    ? day.candles.map(c=>candleRow(c)).join('')
    : '';

  document.getElementById('mbt-detail').scrollIntoView({behavior:'smooth',block:'start'});
}

function closeMultiDetail(){
  document.getElementById('mbt-detail').style.display='none';
  document.querySelectorAll('tr.mbt-row').forEach(r=>r.classList.remove('active'));
}

function mbtMsg(text,type){
  const el=document.getElementById('mbt-msg');
  el.textContent=text; el.style.display=text?'block':'none';
  el.style.background=type==='err'?'#fee2e2':type==='info'?'#eff6ff':'#dcfce7';
  el.style.color=type==='err'?'#991b1b':type==='info'?'#1e40af':'#166534';
}

/* ══════════════════════════════════════════════════════════════
   INIT
══════════════════════════════════════════════════════════════ */
startAutoRefresh();   // always start 5-sec interval on page load
refreshAll();
loadTrades();
</script>
</body>
</html>"""
