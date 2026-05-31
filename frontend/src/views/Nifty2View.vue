<template>
  <div class="page">

    <!-- ── PAGE HEADER ── -->
    <div class="page-header">
      <div>
        <div class="page-title">Nifty 2.0 Dashboard</div>
        <div class="page-subtitle">Simple Early-Entry · 3 Models · Tight SL/Target · 5-min candles</div>
      </div>
      <div style="display:flex;align-items:center;gap:10px">
        <span style="font-size:10px;color:var(--text-muted);font-family:var(--font-mono)">
          Auto-refresh every 2s
        </span>
        <div class="dot" :class="status.engine_running ? 'on' : 'off'"></div>
      </div>
    </div>

    <!-- ── BLOCK BANNER ── -->
    <div v-if="status.first_trade_was_sl && (status.trades_today||0) >= 1" class="day-blocked-banner">
      <span class="blocked-icon">⛔</span>
      <span>
        <b>Second trade blocked</b> — first trade hit hard SL ·
        Realised P&amp;L:
        <b :style="{color: (status.realised_pnl||0)>=0 ? 'var(--green)' : 'var(--red)'}">
          ₹{{ status.realised_pnl?.toFixed?.(0) || 0 }}
        </b>
      </span>
    </div>

    <!-- ── ENGINE CONTROL ── -->
    <div class="card engine-card">
      <div class="card-header">
        <div class="card-title">
          Engine Control
          <span class="badge" :class="status.mode === 'LIVE' ? 'bl' : 'bp'">{{ status.mode || 'PAPER' }}</span>
          <span class="badge" style="background:#0f3a2c;color:#86efac;margin-left:6px">NIFTY v2</span>
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
        <span>OR:&nbsp;<b style="color:var(--accent);font-family:var(--font-mono)">{{ orText }}</b></span>
        <span class="sep">|</span>
        <span>ATM:&nbsp;<b style="color:var(--accent);font-family:var(--font-mono)">{{ status.instruments?.atm_strike || '—' }}</b></span>
      </div>

      <div class="btn-row">
        <button class="btn g-btn" :disabled="status.engine_running || loading" @click="startEngine">▶ Start NIFTY 2.0</button>
        <button class="btn r-btn" :disabled="!status.engine_running || loading" @click="stopEngine">■ Stop NIFTY 2.0</button>
      </div>

      <div v-if="msg.text" class="msg-box" :class="msg.type">{{ msg.text }}</div>

      <div class="strat-note">
        Models: M1 (VWAP Reclaim) · M2 (Opening-Range Breakout, 09:35–10:15, max 1/day) · M3 (Pullback Continuation)<br>
        Risk: Hard SL −10% · Target +12% · Breakeven at +3% · Trail activates +4% / 2.5% gap · Time-stop after 6 candles if losing<br>
        Max 2 trades/day · 09:35–13:30 entries · Force exit 15:15 · Lot size 65 · Block 2nd after hard SL
      </div>
    </div>

    <!-- ── DAY CONTEXT STRIP ── -->
    <div v-if="status.engine_running" class="mstrip">
      <div class="mtile">
        <div class="lbl">OR High</div>
        <div class="val" style="font-family:var(--font-mono)">
          {{ status.day_context?.or_high?.toFixed?.(0) ?? '—' }}
        </div>
        <div class="sub">09:15–09:30</div>
      </div>
      <div class="mtile">
        <div class="lbl">OR Low</div>
        <div class="val" style="font-family:var(--font-mono)">
          {{ status.day_context?.or_low?.toFixed?.(0) ?? '—' }}
        </div>
        <div class="sub">{{ status.day_context?.or_locked ? 'locked' : 'forming…' }}</div>
      </div>
      <div class="mtile">
        <div class="lbl">OR Used (ORB)</div>
        <div class="val" :style="{color: status.day_context?.orb_used ? 'var(--amber)' : 'var(--green)'}">
          {{ status.day_context?.orb_used ? '✓ used' : '— pending' }}
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
        <div class="lbl">ATM CE LTP</div>
        <div class="val" style="color:#60a5fa">{{ status.ce_ltp > 0 ? '₹' + status.ce_ltp.toFixed(2) : '—' }}</div>
        <div class="sub sub-ce">{{ status.instruments?.ce || '—' }}</div>
      </div>
      <div class="mtile">
        <div class="lbl">ATM PE LTP</div>
        <div class="val" style="color:#fb7185">{{ status.pe_ltp > 0 ? '₹' + status.pe_ltp.toFixed(2) : '—' }}</div>
        <div class="sub sub-pe">{{ status.instruments?.pe || '—' }}</div>
      </div>
      <div class="mtile">
        <div class="lbl">Last Signal</div>
        <div class="val" style="font-family:var(--font-mono);font-size:13px">
          {{ status.last_signal || 'NO_SIGNAL' }}
        </div>
        <div class="sub">at {{ status.last_candle_time || '—' }}</div>
      </div>
    </div>

    <!-- ── INDICATORS STRIP ── -->
    <div v-if="showData" class="istrip">
      <div class="itile">
        <div class="lbl">VWAP</div>
        <div class="val" style="color:var(--amber)">{{ status.indicators?.vwap ? status.indicators.vwap.toFixed(2) : '—' }}</div>
      </div>
      <div class="itile">
        <div class="lbl">EMA20</div>
        <div class="val">{{ status.indicators?.ema20 ? status.indicators.ema20.toFixed(2) : '—' }}</div>
      </div>
      <div class="itile">
        <div class="lbl">RSI(14)</div>
        <div class="val">{{ status.indicators?.rsi14 ? status.indicators.rsi14.toFixed(2) : '—' }}</div>
      </div>
      <div class="itile">
        <div class="lbl">Market</div>
        <div class="val" style="font-size:13px">{{ status.market_state || '—' }}</div>
      </div>
    </div>

    <!-- ── ACTIVE POSITION ── -->
    <div v-if="status.position" class="card" style="border-color:var(--accent)">
      <div class="card-header">
        <div class="card-title">📌 Active Position
          <span class="badge" style="background:#0f3a2c;color:#86efac;margin-left:6px">
            {{ modelLabel }}
          </span>
        </div>
        <div :style="{color: pnlColor, fontWeight:700,fontFamily:'var(--font-mono)'}">
          {{ status.pnl ? `${status.pnl.pnl_pct >= 0 ? '+' : ''}${status.pnl.pnl_pct?.toFixed?.(2)}%` : '—' }}
          ·
          ₹{{ status.pnl?.pnl_rupees?.toFixed?.(0) ?? '—' }}
        </div>
      </div>
      <div class="status-row">
        <span><b>{{ status.position.option_type }} {{ status.position.strike }}</b></span>
        <span class="sep">|</span>
        <span>Entry: {{ status.position.entry_price?.toFixed?.(2) }}</span>
        <span class="sep">|</span>
        <span>LTP: {{ status.position.current_price?.toFixed?.(2) }}</span>
        <span class="sep">|</span>
        <span>Qty: {{ status.position.qty }}</span>
        <span class="sep">|</span>
        <span>MFE: <b>{{ status.position.mfe_pct?.toFixed?.(1) ?? '—' }}%</b></span>
      </div>
      <div class="status-row">
        <span>Hard SL: <b style="color:var(--red)">{{ status.position.hard_sl_premium?.toFixed?.(2) ?? '—' }}</b></span>
        <span class="sep">|</span>
        <span>Trail SL:
          <b :style="{color: status.position.trail_active ? 'var(--green)' : 'var(--text-muted)'}">
            {{ status.position.trail_sl_premium?.toFixed?.(2) ?? '—' }}
          </b>
        </span>
        <span class="sep">|</span>
        <span>BE: <b>{{ status.position.breakeven_set ? '✓ set' : '— pending' }}</b></span>
        <span class="sep">|</span>
        <span>Candles: {{ status.position.candles_since_entry || 0 }}</span>
        <span class="sep">|</span>
        <span>Entry @ {{ status.position.entry_time || '—' }}</span>
      </div>
      <div class="strat-note">{{ status.position.reason_entry }}</div>
    </div>

    <!-- ── PAPER TRADES LIST ── -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Paper Trades — Today</div>
        <a class="btn s-btn" style="padding:5px 12px;font-size:11px;text-decoration:none" :href="downloadHref">⬇ Download CSV</a>
      </div>
      <div v-if="!trades || trades.length === 0" class="strat-note">No paper trades yet.</div>
      <table v-else class="trades-table">
        <thead>
          <tr>
            <th>Time</th><th>Model</th><th>Symbol</th><th>Side</th>
            <th>Entry</th><th>Exit</th><th>Qty</th><th>P&L</th><th>%</th>
            <th>MFE</th><th>Exit Layer</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(t, i) in todayTrades" :key="i" :class="parseFloat(t.pnl_rupees) >= 0 ? 'win' : 'loss'">
            <td>{{ t.exit_time }}</td>
            <td>{{ t.model?.split?.('_')?.[0] || '—' }}</td>
            <td>{{ t.option_symbol }}</td>
            <td>{{ t.option_type }}</td>
            <td>{{ t.entry_price }}</td>
            <td>{{ t.exit_price }}</td>
            <td>{{ t.qty }}</td>
            <td>₹{{ t.pnl_rupees }}</td>
            <td>{{ t.pnl_pct }}%</td>
            <td>{{ t.mfe_pct }}%</td>
            <td>{{ t.exit_layer }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="summary && summary.total_trades > 0" class="status-row" style="margin-top:8px">
        <span>Total: <b>{{ summary.total_trades }}</b></span>
        <span class="sep">|</span>
        <span>Wins: <b style="color:var(--green)">{{ summary.wins }}</b> / Losses: <b style="color:var(--red)">{{ summary.losses }}</b></span>
        <span class="sep">|</span>
        <span>Hit-rate: <b>{{ summary.win_rate_pct }}%</b></span>
        <span class="sep">|</span>
        <span>Net: <b :style="{color: (summary.total_pnl_rs||0)>=0 ? 'var(--green)' : 'var(--red)'}">₹{{ summary.total_pnl_rs }}</b></span>
        <span class="sep">|</span>
        <span>PF: <b>{{ summary.profit_factor ?? '—' }}</b></span>
      </div>
    </div>

    <div v-if="status.error" class="msg-box err" style="margin-top:10px">{{ status.error }}</div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'

const status   = reactive({})
const trades   = ref([])
const summary  = ref(null)
const msg      = reactive({ text: '', type: '' })
const loading  = ref(false)
let pollTimer  = null

const downloadHref = '/auto-trading/nifty2/paper-log/download'

const showData = computed(() => status.engine_running || (status.candle_count||0) > 0)

const pnlColor = computed(() => {
  const v = status.pnl?.pnl_pct
  if (v == null) return 'var(--text-muted)'
  return v >= 0 ? 'var(--green)' : 'var(--red)'
})

const orText = computed(() => {
  const ctx = status.day_context
  if (!ctx || ctx.or_high == null || ctx.or_low == null) return '—'
  return `${ctx.or_high.toFixed?.(0) ?? ctx.or_high} / ${ctx.or_low.toFixed?.(0) ?? ctx.or_low}`
})

const modelLabel = computed(() => {
  const m = status.position?.model || ''
  if (m.startsWith('M1')) return 'M1 · VWAP Reclaim'
  if (m.startsWith('M2')) return 'M2 · ORB'
  if (m.startsWith('M3')) return 'M3 · Pullback'
  return m || '—'
})

const todayTrades = computed(() => {
  if (!trades.value || trades.value.length === 0) return []
  const today = new Date().toISOString().slice(0, 10)
  return trades.value.filter(t => t.date === today).slice().reverse()
})

async function fetchStatus() {
  try {
    const r = await fetch('/auto-trading/nifty2/status')
    if (r.ok) {
      const d = await r.json()
      Object.keys(status).forEach(k => { if (!(k in d)) delete status[k] })
      Object.assign(status, d)
    }
  } catch (_) {}
}

async function fetchTrades() {
  try {
    const r = await fetch('/auto-trading/nifty2/paper-log')
    if (r.ok) {
      const d = await r.json()
      trades.value  = d.trades || []
      summary.value = d.summary || null
    }
  } catch (_) {}
}

async function refreshAll() {
  await Promise.all([fetchStatus(), fetchTrades()])
}

async function startEngine() {
  loading.value = true
  msg.text = 'Starting…'; msg.type = ''
  try {
    const r = await fetch('/auto-trading/nifty2/start', { method: 'POST' })
    const d = await r.json()
    if (!r.ok) { msg.text = d.detail || 'Error starting engine'; msg.type = 'err' }
    else { msg.text = d.message || 'Engine started'; msg.type = 'ok' }
    await refreshAll()
  } catch (e) { msg.text = 'Network error: ' + e; msg.type = 'err' }
  finally { loading.value = false }
}

async function stopEngine() {
  loading.value = true
  msg.text = 'Stopping…'; msg.type = ''
  try {
    const r = await fetch('/auto-trading/nifty2/stop', { method: 'POST' })
    const d = await r.json()
    if (!r.ok) { msg.text = d.detail || 'Error stopping engine'; msg.type = 'err' }
    else { msg.text = 'Engine stopped'; msg.type = 'ok' }
    await refreshAll()
  } catch (e) { msg.text = 'Network error: ' + e; msg.type = 'err' }
  finally { loading.value = false }
}

onMounted(() => {
  refreshAll()
  pollTimer = setInterval(refreshAll, 2000)
})

onUnmounted(() => {
  clearInterval(pollTimer)
})
</script>
