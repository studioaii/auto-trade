<template>
  <div class="page">

    <!-- ── PAGE HEADER ── -->
    <div class="page-header">
      <div>
        <div class="page-title">Live Dashboard</div>
        <div class="page-subtitle">NIFTY 50 Options · VWAP+EMA Breakout · 5-min candles</div>
      </div>
      <div style="display:flex;align-items:center;gap:10px">
        <span style="font-size:10px;color:var(--text-muted);font-family:var(--font-mono)">
          Auto-refresh every 5s
        </span>
        <div class="dot" :class="status.engine_running ? 'on' : 'off'"></div>
      </div>
    </div>

    <!-- ── ENGINE CONTROL ── -->
    <div class="card engine-card">
      <div class="card-header">
        <div class="card-title">
          Engine Control
          <span class="badge" :class="status.mode === 'LIVE' ? 'bl' : 'bp'">
            {{ status.mode || 'PAPER' }}
          </span>
        </div>
        <button class="btn s-btn" style="padding:5px 12px;font-size:11px" @click="refreshAll">↻ Refresh</button>
      </div>

      <div class="status-row">
        <div class="dot" :class="status.engine_running ? 'on' : 'off'"></div>
        <span style="font-weight:600;color:var(--text-primary)">
          {{ status.engine_running ? 'Engine Running' : 'Engine Stopped' }}
        </span>
        <span class="sep">|</span>
        <span>Trades:&nbsp;<b style="color:var(--text-primary)">{{ status.trades_today || 0 }}</b> / {{ status.max_trades || 2 }}</span>
        <span class="sep">|</span>
        <span>Candles:&nbsp;<b style="color:var(--text-primary)">{{ status.candle_count || 0 }}</b> / {{ status.candles_needed || 22 }}</span>
        <span class="sep">|</span>
        <span>Market:&nbsp;<b :style="{ color: status.market_state === 'TRENDING' ? (trendDirection === 'UP' ? 'var(--green)' : 'var(--red)') : status.market_state === 'SIDEWAYS' ? 'var(--amber)' : 'var(--text-muted)' }">{{ marketStateLabel }}</b></span>
        <span class="sep">|</span>
        <span>ATM:&nbsp;<b style="color:var(--accent);font-family:var(--font-mono)">{{ status.instruments?.atm_strike || '—' }}</b></span>
      </div>

      <div class="btn-row">
        <button class="btn g-btn" :disabled="status.engine_running || loading" @click="startEngine">
          ▶ Start Engine
        </button>
        <button class="btn r-btn" :disabled="!status.engine_running || loading" @click="stopEngine">
          ■ Stop Engine
        </button>
      </div>

      <div v-if="msg.text" class="msg-box" :class="msg.type">{{ msg.text }}</div>

      <div class="strat-note">
        VWAP + EMA(20) · Dynamic SL (12–22%) · Trailing SL from +20% · RSI + Volume filters · Max 2 trades/day · 9:50–14:00 entries · Force exit 3:20 PM
      </div>
    </div>

    <!-- ── MARKET DATA STRIP ── -->
    <div v-if="showData" class="mstrip">
      <div class="mtile">
        <div class="lbl">Nifty Spot</div>
        <div class="val">{{ status.nifty_spot > 0 ? status.nifty_spot.toFixed(2) : '—' }}</div>
        <div class="sub">INDEX</div>
      </div>
      <div class="mtile">
        <div class="lbl">Nifty Futures</div>
        <div class="val">{{ status.nifty_futures_ltp > 0 ? status.nifty_futures_ltp.toFixed(2) : '—' }}</div>
        <div class="sub">FUTURES LTP</div>
      </div>
      <div class="mtile">
        <div class="lbl">ATM CE LTP</div>
        <div class="val" style="color:#60a5fa">{{ status.ce_ltp > 0 ? '₹' + status.ce_ltp.toFixed(2) : '—' }}</div>
        <div class="sub sub-ce">{{ status.instruments?.ce || '—' }}</div>
      </div>
      <div class="mtile">
        <div class="lbl">ATM PE LTP</div>
        <div class="val" style="color:#fb7185">{{ status.pe_ltp > 0 ? '₹' + status.pe_ltp.toFixed(2) : '—' }}</div>
        <div class="sub sub-pe">{{ status.instruments?.pe || '—' }}</div>
      </div>
    </div>

    <!-- ── INDICATORS STRIP ── -->
    <div v-if="showData" class="istrip">
      <div class="itile">
        <div class="lbl">VWAP</div>
        <div class="val" style="color:var(--amber)">{{ status.indicators?.vwap ? status.indicators.vwap.toFixed(2) : '—' }}</div>
      </div>
      <div class="itile">
        <div class="lbl">EMA 20</div>
        <div class="val" style="color:var(--accent)">{{ status.indicators?.ema20 ? status.indicators.ema20.toFixed(2) : '—' }}</div>
      </div>
      <div class="itile">
        <div class="lbl">RSI 14</div>
        <div class="val" :style="{ color: rsiColor }">{{ status.indicators?.rsi14 != null ? status.indicators.rsi14.toFixed(1) : '—' }}</div>
      </div>
      <div class="itile">
        <div class="lbl">Vol Surge</div>
        <div class="val" :style="{ color: status.indicators?.volume_surge ? 'var(--green)' : 'var(--red)' }">
          {{ status.indicators?.volume_surge != null ? (status.indicators.volume_surge ? 'YES' : 'NO') : '—' }}
        </div>
      </div>
      <div class="itile">
        <div class="lbl">Last Signal</div>
        <div class="val" :style="{ color: signalColor }">{{ status.last_signal || '—' }}</div>
      </div>
      <div class="itile">
        <div class="lbl">Last Candle</div>
        <div class="val" style="font-size:13px">{{ status.last_candle_time || '—' }}</div>
      </div>
    </div>

    <!-- ── POSITION BANNER ── -->
    <div v-if="status.position" class="pos-banner">
      <div class="pos-row">
        <div>
          <div class="pos-sym">{{ status.position.symbol }}</div>
          <div class="pos-meta">
            <span class="pill" :class="status.position.option_type === 'CE' ? 'pill-ce' : 'pill-pe'" style="margin-right:6px">{{ status.position.option_type }}</span>
            Strike {{ status.position.strike }} · Expiry {{ status.position.expiry }} · Qty {{ status.position.qty }}
          </div>
          <div class="pos-sl">
            {{ status.position.trail_active ? '🔁 Trail SL: ₹' + status.position.trailing_sl + ' (trailing active)' : 'SL: ₹' + status.position.trailing_sl }}
          </div>
        </div>
        <div style="text-align:right">
          <div class="pos-pnl" :style="{ color: (status.pnl?.pnl_rupees ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }">
            {{ pnlDisplay }}
          </div>
          <div class="pos-entry">Entry ₹{{ status.position.entry_price }} @ {{ status.position.entry_time }}</div>
        </div>
      </div>
    </div>

    <!-- ── LIVE CHART ── -->
    <div class="card chart-card">
      <div class="chart-title-row">
        <div class="card-title" style="margin-bottom:0">
          Live Chart — NIFTY 5-min
        </div>
        <div v-if="todayCandles.length" class="chart-legend">
          <div class="leg"><div class="leg-line" style="background:#f59e0b"></div>VWAP</div>
          <div class="leg"><div class="leg-line" style="background:#3b82f6"></div>EMA 20</div>
          <div class="leg"><div class="leg-line" style="background:#8b5cf6"></div>RSI 14</div>
          <div class="leg"><div class="leg-line" style="background:#f43f5e;height:1px;border-top:1px dashed #f43f5e"></div>OB 70</div>
          <div class="leg"><div class="leg-line" style="background:#10b981;height:1px;border-top:1px dashed #10b981"></div>OS 30</div>
        </div>
      </div>
      <div v-if="!todayCandles.length" class="chart-empty">
        Start the engine to see the live candlestick chart with VWAP &amp; EMA overlays.
      </div>
      <CandlestickChart v-else :candles="todayCandles" :marker="chartMarker" />
    </div>

    <!-- ── PAPER TRADE LOG ── -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Paper Trade Log</div>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="btn s-btn" style="padding:5px 12px;font-size:11px" @click="loadTrades">↻ Refresh</button>
          <a href="/auto-trading/paper-log/download" class="btn s-btn" style="padding:5px 12px;font-size:11px">⬇ CSV</a>
        </div>
      </div>

      <div v-if="summary && summary.total_trades > 0" class="sum-grid">
        <div class="stat">
          <div class="lbl">Total Trades</div>
          <div class="val b">{{ summary.total_trades }}</div>
        </div>
        <div class="stat">
          <div class="lbl">Win Rate</div>
          <div class="val g">{{ summary.win_rate_pct }}%</div>
        </div>
        <div class="stat">
          <div class="lbl">Total P&amp;L</div>
          <div class="val" :class="summary.total_pnl_rs >= 0 ? 'g' : 'r'">
            {{ summary.total_pnl_rs >= 0 ? '+' : '' }}₹{{ summary.total_pnl_rs }}
          </div>
        </div>
        <div class="stat">
          <div class="lbl">Avg Win / Loss</div>
          <div class="val" style="font-size:14px">₹{{ summary.avg_win_rs }} / ₹{{ summary.avg_loss_rs }}</div>
        </div>
      </div>

      <div class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Date</th>
              <th>Type</th>
              <th>Symbol</th>
              <th>Entry Time</th>
              <th>Entry ₹</th>
              <th>Exit Time</th>
              <th>Exit ₹</th>
              <th>P&amp;L ₹</th>
              <th>P&amp;L %</th>
              <th>Exit Reason</th>
              <th>Entry Reason</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!trades.length">
              <td colspan="12" class="empty-state">No paper trades yet. Start the engine to begin.</td>
            </tr>
            <tr v-for="t in [...trades].reverse()" :key="t.trade_number">
              <td style="color:var(--text-muted)">{{ t.trade_number }}</td>
              <td>{{ t.date }}</td>
              <td>
                <span class="pill" :class="t.option_type === 'CE' ? 'pill-ce' : 'pill-pe'">{{ t.option_type }}</span>
              </td>
              <td style="color:var(--text-primary);font-weight:600;font-size:11px">{{ t.option_symbol }}</td>
              <td>{{ t.entry_time }}</td>
              <td style="color:var(--text-primary)">₹{{ t.entry_price }}</td>
              <td>{{ t.exit_time }}</td>
              <td style="color:var(--text-primary)">₹{{ t.exit_price }}</td>
              <td>
                <span class="pill" :class="parseFloat(t.pnl_rupees) >= 0 ? 'pill-win' : 'pill-loss'">
                  {{ parseFloat(t.pnl_rupees) >= 0 ? '+' : '' }}₹{{ t.pnl_rupees }}
                </span>
              </td>
              <td :style="{ color: parseFloat(t.pnl_pct) >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }">
                {{ parseFloat(t.pnl_pct) >= 0 ? '+' : '' }}{{ t.pnl_pct }}%
              </td>
              <td><span class="pill pill-exit">{{ t.reason_for_exit }}</span></td>
              <td>
                <span v-if="t.reason_for_entry" :title="t.reason_for_entry" style="cursor:help;color:var(--text-muted);font-size:11px">ℹ hover</span>
                <span v-else style="color:var(--text-muted)">—</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ── CANDLE LOG ── -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Candle Log Export</div>
        <div class="candle-log-row">
          <input type="date" v-model="candleLogDate" />
          <button class="btn s-btn" style="padding:5px 12px;font-size:11px" @click="downloadCandleLog">⬇ Download CSV</button>
        </div>
      </div>
      <div style="font-size:11px;color:var(--text-muted)">Select a date to download the 5-min candle log with all indicator snapshots.</div>
    </div>

    <!-- Portfolio and Auth moved to sidebar tabs -->

  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import CandlestickChart from '../components/CandlestickChart.vue'

// ─── state ─────────────────────────────────────────────────────────────────
const status      = ref({})
const candles     = ref([])
const trades      = ref([])
const summary     = ref(null)
const loading     = ref(false)
const msg         = ref({ text: '', type: '' })
const candleLogDate = ref(new Date().toISOString().slice(0, 10))
let refreshTimer  = null

// ─── computed ───────────────────────────────────────────────────────────────
const showData = computed(() =>
  status.value.engine_running || status.value.nifty_spot > 0
)

const todayCandles = computed(() =>
  candles.value.filter(c => c.is_today)
)

const chartMarker = computed(() => {
  const pos = status.value.position
  if (!pos?.entry_time) return null
  const entryHHMM = pos.entry_time.slice(0, 5)
  const found = todayCandles.value.find(c => {
    const d = new Date(c.time * 1000)
    const t = d.getUTCHours().toString().padStart(2,'0') + ':' + d.getUTCMinutes().toString().padStart(2,'0')
    return t === entryHHMM
  })
  return found ? { time: found.time, type: pos.option_type } : null
})

const trendDirection = computed(() => {
  if (status.value.market_state !== 'TRENDING') return null
  const spot = status.value.nifty_spot
  const vwap = status.value.indicators?.vwap
  const ema  = status.value.indicators?.ema20
  if (spot && vwap) return spot >= vwap ? 'UP' : 'DOWN'
  if (spot && ema)  return spot >= ema  ? 'UP' : 'DOWN'
  return null
})

const marketStateLabel = computed(() => {
  const st = status.value.market_state
  if (!st) return '—'
  if (st === 'TRENDING') {
    if (trendDirection.value === 'UP')   return '▲ UPTREND'
    if (trendDirection.value === 'DOWN') return '▼ DOWNTREND'
    return '▲ TRENDING'
  }
  if (st === 'SIDEWAYS') return '↔ SIDEWAYS'
  return st
})

const rsiColor = computed(() => {
  const rsi = status.value.indicators?.rsi14
  if (rsi == null) return 'var(--text-muted)'
  return rsi >= 70 ? 'var(--red)' : rsi <= 30 ? 'var(--green)' : 'var(--purple)'
})

const signalColor = computed(() => {
  const sig = status.value.last_signal
  if (sig === 'BUY_CE') return '#60a5fa'
  if (sig === 'BUY_PE') return '#fb7185'
  if (sig === 'NO_SIGNAL') return 'var(--text-muted)'
  return 'var(--text-muted)'
})

const pnlDisplay = computed(() => {
  const pnl = status.value.pnl
  if (!pnl) return '—'
  const sign = pnl.pnl_rupees >= 0 ? '+' : ''
  return sign + '₹' + pnl.pnl_rupees + ' (' + sign + pnl.pnl_pct + '%)'
})

// ─── API ────────────────────────────────────────────────────────────────────
async function refreshAll() {
  try {
    const [sRes, cRes] = await Promise.all([
      fetch('/auto-trading/status'),
      fetch('/auto-trading/candles'),
    ])
    status.value  = await sRes.json()
    const cData   = await cRes.json()
    candles.value = cData.candles || []
  } catch (_) {}
}

async function loadTrades() {
  try {
    const r = await fetch('/auto-trading/paper-log')
    const d = await r.json()
    trades.value  = d.trades  || []
    summary.value = d.summary || null
  } catch (_) {}
}

async function startEngine() {
  loading.value = true
  setMsg('Starting engine…', '')
  try {
    const r = await fetch('/auto-trading/start', { method: 'POST' })
    const d = await r.json()
    if (!r.ok) { setMsg(d.detail || 'Error', 'err'); return }
    setMsg('Engine started (' + d.mode + '). CE: ' + d.instruments.ce + ' | PE: ' + d.instruments.pe, 'ok')
    await refreshAll()
  } catch (e) { setMsg('Network error: ' + e, 'err') }
  finally { loading.value = false }
}

async function stopEngine() {
  loading.value = true
  setMsg('Stopping…', '')
  try {
    const r = await fetch('/auto-trading/stop', { method: 'POST' })
    const d = await r.json()
    if (!r.ok) { setMsg(d.detail || 'Error', 'err'); return }
    const p = d.final_pnl ? ' | PnL: ₹' + d.final_pnl.pnl_rupees : ''
    setMsg('Stopped. Trades: ' + d.trades_today + p, 'ok')
  } catch (e) { setMsg('Error: ' + e, 'err') }
  finally { loading.value = false }
  await refreshAll()
  loadTrades()
}

function setMsg(text, type) {
  msg.value = { text, type }
}

function downloadCandleLog() {
  if (!candleLogDate.value) { alert('Please select a date.'); return }
  window.location.href = '/auto-trading/candle-log/download/' + candleLogDate.value
}

// ─── lifecycle ───────────────────────────────────────────────────────────────
onMounted(() => {
  refreshAll()
  loadTrades()
  refreshTimer = setInterval(() => { refreshAll(); loadTrades() }, 5000)
})

onUnmounted(() => {
  clearInterval(refreshTimer)
})
</script>
