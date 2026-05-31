<template>
  <div class="page">

    <!-- ── PAGE HEADER ── -->
    <div class="page-header">
      <div>
        <div class="page-title">Bank Nifty 2.0 Dashboard</div>
        <div class="page-subtitle">High-Precision Engine · Models A/B/C/D · 3-Layer Exits · 5-min candles</div>
      </div>
      <div style="display:flex;align-items:center;gap:10px">
        <span style="font-size:10px;color:var(--text-muted);font-family:var(--font-mono)">
          Auto-refresh every 2s
        </span>
        <div class="dot" :class="status.engine_running ? 'on' : 'off'"></div>
      </div>
    </div>

    <!-- ── DAY LOCK BANNER ── -->
    <div v-if="status.forced_lock" class="day-blocked-banner">
      <span class="blocked-icon">⛔</span>
      <span>
        <b>Day locked</b> — {{ status.forced_lock }} ·
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
          <span class="badge" style="background:#1e293b;color:#a5b4fc;margin-left:6px">v2</span>
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
        <span>Day:&nbsp;<b :style="{color: dayClassColor}">{{ status.day_context?.class || '—' }}</b></span>
        <span class="sep">|</span>
        <span>ATM:&nbsp;<b style="color:var(--accent);font-family:var(--font-mono)">{{ status.instruments?.atm_strike || '—' }}</b></span>
      </div>

      <div class="btn-row">
        <button class="btn g-btn" :disabled="status.engine_running || loading" @click="startEngine">▶ Start BankNifty 2.0</button>
        <button class="btn r-btn" :disabled="!status.engine_running || loading" @click="stopEngine">■ Stop BankNifty 2.0</button>
      </div>

      <div v-if="msg.text" class="msg-box" :class="msg.type">{{ msg.text }}</div>

      <div class="strat-note">
        Models: A (Compression Breakout) · B (First Pullback) · C (Liquidity Sweep) · D (Flag Continuation)<br>
        Exits: Structure SL (8–18% cap) · Partial 50% at bucket target · Runner trail · Stall/Stagnation/Structure-break detection<br>
        Max 2 trades/day · 09:50–14:00 entries · Force exit 15:15 · Daily lock −₹6,000 / +₹15,000 · Lot size 30
      </div>
    </div>

    <!-- ── DAY CONTEXT STRIP ── -->
    <div v-if="status.engine_running" class="mstrip">
      <div class="mtile">
        <div class="lbl">Day Class</div>
        <div class="val" :style="{color: dayClassColor}">{{ status.day_context?.class || '—' }}</div>
        <div class="sub">classified at 09:50</div>
      </div>
      <div class="mtile">
        <div class="lbl">Gap %</div>
        <div class="val" :style="{color: (status.day_context?.gap_pct||0)>=0 ? 'var(--green)' : 'var(--red)'}">
          {{ status.day_context?.gap_pct != null ? status.day_context.gap_pct.toFixed(2) + '%' : '—' }}
        </div>
        <div class="sub">vs prev close</div>
      </div>
      <div class="mtile">
        <div class="lbl">VWAP Drift @ 09:50</div>
        <div class="val" :style="{color: (status.day_context?.vwap_drift_pct||0)>=0 ? 'var(--green)' : 'var(--red)'}">
          {{ status.day_context?.vwap_drift_pct != null ? status.day_context.vwap_drift_pct.toFixed(2) + '%' : '—' }}
        </div>
        <div class="sub">spot vs VWAP</div>
      </div>
      <div class="mtile">
        <div class="lbl">Opening Range</div>
        <div class="val" style="font-size:14px;font-family:var(--font-mono)">
          {{ orText }}
        </div>
        <div class="sub">{{ orRangePts }} pts</div>
      </div>
    </div>

    <!-- ── MARKET DATA STRIP ── -->
    <div v-if="showData" class="mstrip">
      <div class="mtile">
        <div class="lbl">BankNifty Spot</div>
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
        <div class="lbl">Realised P&amp;L</div>
        <div class="val" :style="{color: (status.realised_pnl||0)>=0 ? 'var(--green)' : 'var(--red)'}">
          ₹{{ status.realised_pnl?.toFixed?.(0) || 0 }}
        </div>
        <div class="sub">today</div>
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
        <div class="lbl">Vol Surge</div>
        <div class="val">{{ status.indicators?.vol_surge ? '✓' : '—' }}</div>
      </div>
    </div>

    <!-- ── PENDING LIMIT (Model A) ── -->
    <div v-if="status.pending_limit" class="card" style="border-color:#fbbf24">
      <div class="card-header">
        <div class="card-title">⏳ Model A — Pending Limit Order</div>
      </div>
      <div class="status-row">
        <span><b>Side:</b> {{ status.pending_limit.side }}</span>
        <span class="sep">|</span>
        <span><b>Trigger:</b> {{ status.pending_limit.trigger_spot }}</span>
        <span class="sep">|</span>
        <span><b>Structure SL:</b> {{ status.pending_limit.structure_sl }}</span>
        <span class="sep">|</span>
        <span><b>Alive:</b> {{ status.pending_limit.candles_alive }} candles</span>
      </div>
      <div class="strat-note">{{ status.pending_limit.reason }}</div>
    </div>

    <!-- ── ACTIVE POSITION ── -->
    <div v-if="status.position" class="card" style="border-color:var(--accent)">
      <div class="card-header">
        <div class="card-title">📌 Active Position
          <span class="badge" style="background:#3730a3;color:#c7d2fe;margin-left:6px">
            Model {{ status.position.model?.charAt?.(0) || '?' }}
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
        <span>SL: <b style="color:var(--red)">{{ status.position.structure_sl_premium?.toFixed?.(2) ?? '—' }}</b></span>
        <span class="sep">|</span>
        <span>Trail SL:
          <b :style="{color: status.position.runner_trail_sl ? 'var(--green)' : 'var(--text-muted)'}">
            {{ status.position.runner_trail_sl?.toFixed?.(2) ?? 'inactive' }}
          </b>
        </span>
        <span class="sep">|</span>
        <span>Partial: <b>{{ status.position.partial_booked ? '✓ booked' : '— pending' }}</b></span>
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
            <th>Time</th><th>Leg</th><th>Model</th><th>Symbol</th><th>Side</th>
            <th>Entry</th><th>Exit</th><th>Qty</th><th>P&L</th><th>%</th>
            <th>MFE</th><th>Exit Layer</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(t, i) in todayTrades" :key="i" :class="parseFloat(t.pnl_rupees) >= 0 ? 'win' : 'loss'">
            <td>{{ t.exit_time }}</td>
            <td>{{ t.leg }}</td>
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
      <div v-if="summary" class="status-row" style="margin-top:8px">
        <span>Total Legs: <b>{{ summary.total_legs }}</b></span>
        <span class="sep">|</span>
        <span>Wins: <b style="color:var(--green)">{{ summary.wins }}</b> / Losses: <b style="color:var(--red)">{{ summary.losses }}</b></span>
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

const downloadHref = '/auto-trading/banknifty2/paper-log/download'

const showData = computed(() => status.engine_running || (status.candle_count||0) > 0)

const pnlColor = computed(() => {
  const v = status.pnl?.pnl_pct
  if (v == null) return 'var(--text-muted)'
  return v >= 0 ? 'var(--green)' : 'var(--red)'
})

const dayClassColor = computed(() => {
  const cls = status.day_context?.class
  switch (cls) {
    case 'TREND_DAY':    return 'var(--green)'
    case 'REVERSAL_DAY': return 'var(--amber)'
    case 'CHOP_DAY':     return 'var(--red)'
    case 'NORMAL_DAY':   return 'var(--text-primary)'
    default:             return 'var(--text-muted)'
  }
})

const orText = computed(() => {
  const ctx = status.day_context
  if (!ctx || ctx.or_high == null || ctx.or_low == null) return '—'
  return `${ctx.or_high.toFixed?.(0) ?? ctx.or_high} / ${ctx.or_low.toFixed?.(0) ?? ctx.or_low}`
})

const orRangePts = computed(() => {
  const ctx = status.day_context
  if (!ctx || ctx.or_high == null || ctx.or_low == null) return '—'
  return Math.round(ctx.or_high - ctx.or_low)
})

const todayTrades = computed(() => {
  if (!trades.value || trades.value.length === 0) return []
  const today = new Date().toISOString().slice(0, 10)
  return trades.value.filter(t => t.date === today).slice().reverse()
})

async function fetchStatus() {
  try {
    const r = await fetch('/auto-trading/banknifty2/status')
    if (r.ok) {
      const d = await r.json()
      Object.keys(status).forEach(k => { if (!(k in d)) delete status[k] })
      Object.assign(status, d)
    }
  } catch (_) {}
}

async function fetchTrades() {
  try {
    const r = await fetch('/auto-trading/banknifty2/paper-log')
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
    const r = await fetch('/auto-trading/banknifty2/start', { method: 'POST' })
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
    const r = await fetch('/auto-trading/banknifty2/stop', { method: 'POST' })
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
