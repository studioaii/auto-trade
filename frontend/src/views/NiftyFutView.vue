<template>
  <div class="page">

    <!-- ── PAGE HEADER ── -->
    <div class="page-header">
      <div>
        <div class="page-title">NIFTY Futures Dashboard</div>
        <div class="page-subtitle">Opening-Range Breakout · Trades the future directly (Long/Short) · 5-min candles</div>
      </div>
      <div style="display:flex;align-items:center;gap:10px">
        <span style="font-size:10px;color:var(--text-muted);font-family:var(--font-mono)">Auto-refresh every 2s</span>
        <div class="dot" :class="status.engine_running ? 'on' : 'off'"></div>
      </div>
    </div>

    <!-- ── ENGINE CONTROL ── -->
    <div class="card engine-card">
      <div class="card-header">
        <div class="card-title">
          Engine Control
          <span class="badge" :class="status.mode === 'LIVE' ? 'bl' : 'bp'">{{ status.mode || 'PAPER' }}</span>
          <span class="badge" style="background:#0f2e3a;color:#7dd3fc;margin-left:6px">FUTURES</span>
        </div>
        <button class="btn s-btn" style="padding:5px 12px;font-size:11px" @click="refreshAll">↻ Refresh</button>
      </div>

      <div class="status-row">
        <div class="dot" :class="status.engine_running ? 'on' : 'off'"></div>
        <span style="font-weight:600;color:var(--text-primary)">
          {{ status.engine_running ? 'Engine Running' : 'Engine Stopped' }}
        </span>
        <span class="sep">|</span>
        <span>Trades:&nbsp;<b style="color:var(--text-primary)">{{ status.trades_today || 0 }}</b> / {{ status.max_trades || 1 }}</span>
        <span class="sep">|</span>
        <span>Candles:&nbsp;<b style="color:var(--text-primary)">{{ status.candle_count || 0 }}</b> / {{ status.candles_needed || 22 }}</span>
        <span class="sep">|</span>
        <span>ORB:&nbsp;<b :style="{color: status.orb_used ? 'var(--amber)' : 'var(--green)'}">{{ status.orb_used ? '✓ used' : '— pending' }}</b></span>
        <span class="sep">|</span>
        <span>Contract:&nbsp;<b style="color:var(--accent);font-family:var(--font-mono)">{{ status.instruments?.futures_symbol || '—' }}</b></span>
      </div>

      <div class="btn-row">
        <button class="btn g-btn" :disabled="status.engine_running || loading" @click="startEngine">▶ Start NIFTY Futures</button>
        <button class="btn r-btn" :disabled="!status.engine_running || loading" @click="stopEngine">■ Stop NIFTY Futures</button>
      </div>

      <div v-if="msg.text" class="msg-box" :class="msg.type">{{ msg.text }}</div>

      <div class="strat-note">
        Opening-Range Breakout (hardened): OR = 09:15–09:25 · breakout entries 09:35–11:30 · 0.07% buffer + volume surge + body filter + RSI cap<br>
        Risk: Fixed SL −30 pts · Target +70 pts (no trailing) · 1 trade/day · Force exit 15:15 · Lot 65 (₹65/pt) · Always PAPER<br>
        Backtest (Apr–May 2026 futures): PF 1.77, OOS 1.57 — forward-paper-test before any live capital.
      </div>
    </div>

    <!-- ── DAY CONTEXT STRIP ── -->
    <div v-if="status.engine_running" class="mstrip">
      <div class="mtile">
        <div class="lbl">OR High</div>
        <div class="val" style="font-family:var(--font-mono)">{{ orbHigh }}</div>
        <div class="sub">09:15–09:25</div>
      </div>
      <div class="mtile">
        <div class="lbl">OR Low</div>
        <div class="val" style="font-family:var(--font-mono)">{{ orbLow }}</div>
        <div class="sub">first 3 bars</div>
      </div>
      <div class="mtile">
        <div class="lbl">Breakout Taken</div>
        <div class="val" :style="{color: status.orb_used ? 'var(--amber)' : 'var(--green)'}">
          {{ status.orb_used ? '✓ used' : '— pending' }}
        </div>
        <div class="sub">max 1/day</div>
      </div>
      <div class="mtile">
        <div class="lbl">Realised P&amp;L</div>
        <div class="val" :style="{color: (status.realised_pnl||0)>=0 ? 'var(--green)' : 'var(--red)'}">
          ₹{{ status.realised_pnl?.toFixed?.(0) || 0 }}
        </div>
        <div class="sub">today</div>
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
        <div class="val" style="color:#7dd3fc">{{ status.futures_ltp > 0 ? status.futures_ltp.toFixed(2) : '—' }}</div>
        <div class="sub">{{ status.instruments?.futures_symbol || 'FUT LTP' }}</div>
      </div>
      <div class="mtile">
        <div class="lbl">Last Signal</div>
        <div class="val" :style="{color: signalColor}" style="font-family:var(--font-mono);font-size:14px">{{ signalLabel }}</div>
        <div class="sub">at {{ status.last_candle_time || '—' }}</div>
      </div>
      <div class="mtile">
        <div class="lbl">Lot Size</div>
        <div class="val">{{ status.instruments?.lot_size || 65 }}</div>
        <div class="sub">₹{{ status.instruments?.lot_size || 65 }}/pt</div>
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
        <div class="val itile-rsi-row" :style="{ color: rsiColor }">
          {{ status.indicators?.rsi14 != null ? status.indicators.rsi14.toFixed(1) : '—' }}
          <span v-if="status.indicators?.rsi14 != null" class="rsi-zone-tag" :style="{ color: rsiColor, borderColor: rsiColor }">{{ rsiZone }}</span>
        </div>
      </div>
      <div class="itile">
        <div class="lbl">Market</div>
        <div class="val" style="font-size:13px">{{ status.market_state || '—' }}</div>
      </div>
    </div>

    <!-- ── POSITION BANNER ── -->
    <div v-if="status.position" class="pos-banner">
      <div class="pos-row">
        <div>
          <div class="pos-sym">{{ status.position.futures_symbol }}</div>
          <div class="pos-meta">
            <span class="pill" :class="status.position.direction === 'LONG' ? 'pill-ce' : 'pill-pe'" style="margin-right:6px">{{ status.position.direction }}</span>
            Qty {{ status.position.qty }} · SL {{ status.position.sl_price }} · Target {{ status.position.target_price }}
          </div>
          <div class="pos-sl">LTP {{ status.position.current_price }}</div>
        </div>
        <div style="text-align:right">
          <div class="pos-pnl" :style="{ color: (status.pnl?.rupees ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }">
            {{ pnlDisplay }}
          </div>
          <div class="pos-entry">Entry {{ status.position.entry_price }} @ {{ status.position.entry_time }}</div>
        </div>
      </div>
    </div>

    <!-- ── LIVE CHART ── -->
    <div class="card chart-card">
      <div class="chart-title-row">
        <div class="card-title" style="margin-bottom:0">Live Chart — NIFTY Futures 5-min</div>
        <div v-if="todayCandles.length" class="chart-legend">
          <div class="leg"><div class="leg-line" style="background:#f59e0b"></div>VWAP</div>
          <div class="leg"><div class="leg-line" style="background:#3b82f6"></div>EMA 20</div>
          <div class="leg"><div class="leg-line" style="background:#8b5cf6"></div>RSI 14</div>
        </div>
      </div>
      <div v-if="!todayCandles.length" class="chart-empty">
        Start the engine to see the live futures candlestick chart with VWAP &amp; EMA overlays.
      </div>
      <CandlestickChart v-else :candles="todayCandles" :liveCandle="liveCandle" :marker="chartMarker" />
    </div>

    <!-- ── PAPER TRADE LOG ── -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Paper Trade Log</div>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="btn s-btn" style="padding:5px 12px;font-size:11px" @click="loadTrades">↻ Refresh</button>
          <a href="/auto-trading/nifty-fut/paper-log/download" class="btn s-btn" style="padding:5px 12px;font-size:11px">⬇ CSV</a>
        </div>
      </div>

      <div v-if="summary && summary.total_trades > 0" class="sum-grid">
        <div class="stat">
          <div class="lbl">Total Trades</div>
          <div class="val b">{{ summary.total_trades }}</div>
        </div>
        <div class="stat">
          <div class="lbl">Win Rate</div>
          <div class="val g">{{ summary.win_rate }}%</div>
        </div>
        <div class="stat">
          <div class="lbl">Net P&amp;L</div>
          <div class="val" :class="summary.net_rupees >= 0 ? 'g' : 'r'">
            {{ summary.net_rupees >= 0 ? '+' : '' }}₹{{ summary.net_rupees }}
          </div>
        </div>
        <div class="stat">
          <div class="lbl">Profit Factor</div>
          <div class="val" style="font-size:14px">{{ summary.profit_factor }}</div>
        </div>
      </div>

      <div class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>#</th><th>Date</th><th>Side</th><th>Entry Time</th><th>Entry</th>
              <th>Exit Time</th><th>Exit</th><th>Pts</th><th>P&amp;L ₹</th><th>P&amp;L %</th><th>Exit</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!trades.length">
              <td colspan="11" class="empty-state">No paper trades yet. Start the engine to begin.</td>
            </tr>
            <tr v-for="t in [...trades].reverse()" :key="t.trade_number + t.entry_time">
              <td style="color:var(--text-muted)">{{ t.trade_number }}</td>
              <td>{{ t.date }}</td>
              <td><span class="pill" :class="t.direction === 'LONG' ? 'pill-ce' : 'pill-pe'">{{ t.direction }}</span></td>
              <td>{{ t.entry_time }}</td>
              <td style="color:var(--text-primary)">{{ t.entry_price }}</td>
              <td>{{ t.exit_time }}</td>
              <td style="color:var(--text-primary)">{{ t.exit_price }}</td>
              <td :style="{ color: parseFloat(t.pnl_points) >= 0 ? 'var(--green)' : 'var(--red)' }">
                {{ parseFloat(t.pnl_points) >= 0 ? '+' : '' }}{{ t.pnl_points }}
              </td>
              <td>
                <span class="pill" :class="parseFloat(t.pnl_rupees) >= 0 ? 'pill-win' : 'pill-loss'">
                  {{ parseFloat(t.pnl_rupees) >= 0 ? '+' : '' }}₹{{ t.pnl_rupees }}
                </span>
              </td>
              <td :style="{ color: parseFloat(t.pnl_pct) >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }">
                {{ parseFloat(t.pnl_pct) >= 0 ? '+' : '' }}{{ t.pnl_pct }}%
              </td>
              <td><span class="pill pill-exit">{{ t.exit_layer }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="status.error" class="msg-box err" style="margin-top:10px">{{ status.error }}</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import CandlestickChart from '../components/CandlestickChart.vue'

const status     = ref({})
const candles    = ref([])
const liveCandle = ref(null)
const trades     = ref([])
const summary    = ref(null)
const loading    = ref(false)
const msg        = ref({ text: '', type: '' })
let refreshTimer = null
let tradesTimer  = null

const showData = computed(() => status.value.engine_running || (status.value.candle_count || 0) > 0)
const todayCandles = computed(() => candles.value.filter(c => c.is_today))

const orbHigh = computed(() => {
  const c = todayCandles.value.slice(0, 3)
  return c.length === 3 ? Math.max(...c.map(x => x.high)).toFixed(0) : '—'
})
const orbLow = computed(() => {
  const c = todayCandles.value.slice(0, 3)
  return c.length === 3 ? Math.min(...c.map(x => x.low)).toFixed(0) : '—'
})

const chartMarker = computed(() => {
  const pos = status.value.position
  if (!pos?.entry_time) return null
  const entryHHMM = pos.entry_time.slice(0, 5)
  const found = todayCandles.value.find(c => {
    const d = new Date(c.time * 1000)
    const t = d.getUTCHours().toString().padStart(2, '0') + ':' + d.getUTCMinutes().toString().padStart(2, '0')
    return t === entryHHMM
  })
  // Reuse the chart's CE/PE arrow convention: LONG → up arrow, SHORT → down arrow
  return found ? { time: found.time, type: pos.direction === 'LONG' ? 'CE' : 'PE' } : null
})

const rsiColor = computed(() => {
  const rsi = status.value.indicators?.rsi14
  if (rsi == null) return 'var(--text-muted)'
  return rsi >= 70 ? 'var(--red)' : rsi <= 30 ? 'var(--green)' : 'var(--purple)'
})
const rsiZone = computed(() => {
  const rsi = status.value.indicators?.rsi14
  if (rsi == null) return ''
  return rsi >= 70 ? 'OB' : rsi <= 30 ? 'OS' : 'NEUTRAL'
})

const signalColor = computed(() => {
  const s = status.value.last_signal
  if (s === 'LONG') return 'var(--green)'
  if (s === 'SHORT') return 'var(--red)'
  return 'var(--text-muted)'
})
const signalLabel = computed(() => {
  const s = status.value.last_signal
  if (s === 'LONG') return '▲ LONG'
  if (s === 'SHORT') return '▼ SHORT'
  return s || '— NO SIGNAL'
})

const pnlDisplay = computed(() => {
  const pnl = status.value.pnl
  if (!pnl) return '—'
  const sign = pnl.rupees >= 0 ? '+' : ''
  return `${sign}₹${pnl.rupees} (${sign}${pnl.points} pts)`
})

async function refreshAll() {
  try {
    const [s, c] = await Promise.all([
      fetch('/auto-trading/nifty-fut/status'),
      fetch('/auto-trading/nifty-fut/candles'),
    ])
    if (s.ok) status.value = await s.json()
    if (c.ok) {
      const d = await c.json()
      candles.value = d.candles || []
      liveCandle.value = d.live_candle || null
    }
  } catch (_) {}
}

async function loadTrades() {
  try {
    const r = await fetch('/auto-trading/nifty-fut/paper-log')
    if (r.ok) {
      const d = await r.json()
      trades.value = d.trades || []
      summary.value = d.summary || null
    }
  } catch (_) {}
}

async function startEngine() {
  loading.value = true; msg.value = { text: 'Starting…', type: '' }
  try {
    const r = await fetch('/auto-trading/nifty-fut/start', { method: 'POST' })
    const d = await r.json()
    msg.value = r.ok ? { text: d.message || 'Engine started', type: 'ok' }
                     : { text: d.detail || 'Error', type: 'err' }
    await refreshAll()
  } catch (e) { msg.value = { text: 'Network error: ' + e, type: 'err' } }
  finally { loading.value = false }
}

async function stopEngine() {
  loading.value = true; msg.value = { text: 'Stopping…', type: '' }
  try {
    const r = await fetch('/auto-trading/nifty-fut/stop', { method: 'POST' })
    const d = await r.json()
    msg.value = r.ok ? { text: 'Engine stopped', type: 'ok' } : { text: d.detail || 'Error', type: 'err' }
    await refreshAll(); loadTrades()
  } catch (e) { msg.value = { text: 'Error: ' + e, type: 'err' } }
  finally { loading.value = false }
}

onMounted(() => {
  refreshAll(); loadTrades()
  refreshTimer = setInterval(refreshAll, 2000)
  tradesTimer = setInterval(loadTrades, 10000)
})
onUnmounted(() => { clearInterval(refreshTimer); clearInterval(tradesTimer) })
</script>
