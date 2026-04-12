<template>
  <div class="page">

    <!-- ── PAGE HEADER ── -->
    <div class="page-header">
      <div>
        <div class="page-title">Strategy Backtest</div>
        <div class="page-subtitle">Replay VWAP+EMA Breakout on historical Nifty data · P&amp;L in Nifty index points</div>
      </div>
    </div>

    <!-- ── SINGLE DAY BACKTEST ── -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Single Day Replay</div>
      </div>

      <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px">
        <div>
          <label class="form-label">Select Date</label>
          <input type="date" v-model="btDate" :max="yesterday" />
        </div>
        <button class="btn b-btn" :disabled="btLoading" @click="runBacktest">
          {{ btLoading ? '⏳ Running…' : '▶ Run Backtest' }}
        </button>
      </div>

      <div style="font-size:11px;color:var(--text-muted);margin-bottom:12px">
        Replays the VWAP+EMA strategy on historical Nifty 5-min data.
        P&amp;L shown in Nifty index points — estimated options ₹ ≈ points × 0.5 × 75 lot.
      </div>

      <div v-if="btMsg.text" class="msg-box" :class="btMsg.type === 'err' ? 'err' : btMsg.type === 'info' ? '' : 'ok'">
        {{ btMsg.text }}
      </div>

      <div v-if="btResult">
        <!-- summary -->
        <div class="sum-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:16px">
          <div class="stat">
            <div class="lbl">Trades</div>
            <div class="val b">{{ btResult.summary.total_trades }}</div>
          </div>
          <div class="stat">
            <div class="lbl">Win Rate</div>
            <div class="val g">{{ btResult.summary.win_rate_pct }}%</div>
          </div>
          <div class="stat">
            <div class="lbl">Total Points</div>
            <div class="val" :class="btResult.summary.total_points >= 0 ? 'g' : 'r'">
              {{ btResult.summary.total_points >= 0 ? '+' : '' }}{{ btResult.summary.total_points }} pts
            </div>
          </div>
          <div class="stat">
            <div class="lbl">Est. ₹ P&amp;L</div>
            <div class="val" :class="(btResult.summary.total_est_rs ?? 0) >= 0 ? 'g' : 'r'">
              {{ (btResult.summary.total_est_rs ?? 0) >= 0 ? '+' : '' }}₹{{ btResult.summary.total_est_rs ?? 0 }}
            </div>
          </div>
        </div>

        <!-- trades table -->
        <div v-if="btResult.trades?.length" style="margin-bottom:16px">
          <div class="section-label">Trades</div>
          <div class="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Signal</th>
                  <th>Entry Time</th>
                  <th>Entry Nifty</th>
                  <th>Exit Time</th>
                  <th>Exit Nifty</th>
                  <th>Nifty Pts</th>
                  <th>Est ₹ P&amp;L</th>
                  <th>Exit Reason</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="t in btResult.trades" :key="t.num">
                  <td style="color:var(--text-muted)">{{ t.num }}</td>
                  <td>
                    <span class="pill" :class="t.signal === 'BUY_CE' ? 'pill-ce' : 'pill-pe'">
                      {{ t.signal === 'BUY_CE' ? 'CE' : 'PE' }}
                    </span>
                  </td>
                  <td>{{ t.entry_time }}</td>
                  <td style="color:var(--text-primary);font-weight:600">{{ t.entry_nifty }}</td>
                  <td>{{ t.exit_time }}</td>
                  <td style="color:var(--text-primary);font-weight:600">{{ t.exit_nifty }}</td>
                  <td :style="{ fontWeight: 700, color: t.points >= 0 ? 'var(--green)' : 'var(--red)' }">
                    {{ t.points >= 0 ? '+' : '' }}{{ t.points }} pts
                  </td>
                  <td :style="{ fontWeight: 700, color: t.est_options_pnl >= 0 ? 'var(--green)' : 'var(--red)' }">
                    {{ t.est_options_pnl >= 0 ? '+' : '' }}₹{{ t.est_options_pnl }}
                  </td>
                  <td><span class="pill pill-exit">{{ t.exit_reason }}</span></td>
                  <td>
                    <span class="pill" :class="t.result === 'WIN' ? 'pill-win' : 'pill-loss'">{{ t.result }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- candle timeline -->
        <div v-if="btResult.candles?.length">
          <div class="section-label">
            5-Min Candle Timeline
            <span style="font-size:9px;letter-spacing:0;text-transform:none;color:var(--text-muted);margin-left:4px">
              highlighted rows = active trade
            </span>
          </div>
          <div class="tbl-wrap" style="max-height:380px;overflow-y:auto">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Open</th><th>High</th><th>Low</th><th>Close</th>
                  <th>VWAP</th><th>EMA 20</th>
                  <th>Market</th><th>Signal</th><th>Note</th>
                </tr>
              </thead>
              <tbody>
                <CandleRow v-for="c in btResult.candles" :key="c.time" :c="c" />
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

    <!-- ── MULTI DAY BACKTEST ── -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Multi-Day Backtest</div>
        <div style="display:flex;gap:4px;flex-wrap:wrap">
          <button class="btn s-btn" style="padding:4px 10px;font-size:11px" @click="setRange(5)">5D</button>
          <button class="btn s-btn" style="padding:4px 10px;font-size:11px" @click="setRange(10)">10D</button>
          <button class="btn s-btn" style="padding:4px 10px;font-size:11px" @click="setRange(30)">30D</button>
          <button class="btn s-btn" style="padding:4px 10px;font-size:11px" @click="setRange(60)">60D</button>
        </div>
      </div>

      <div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin-bottom:12px">
        <div>
          <label class="form-label">From Date</label>
          <input type="date" v-model="mbtFrom" :max="yesterday" />
        </div>
        <div>
          <label class="form-label">To Date</label>
          <input type="date" v-model="mbtTo" :max="yesterday" />
        </div>
        <button class="btn b-btn" :disabled="mbtLoading" @click="runMultiBacktest">
          {{ mbtLoading ? '⏳ Running…' : '▶ Run Multi-Day' }}
        </button>
      </div>

      <div style="font-size:11px;color:var(--text-muted);margin-bottom:12px">
        Runs strategy on each trading day in the range. Click any date row to see full trade details + candle timeline. Max 90 days.
      </div>

      <div v-if="mbtMsg.text" class="msg-box" :class="mbtMsg.type === 'err' ? 'err' : mbtMsg.type === 'info' ? '' : 'ok'">
        {{ mbtMsg.text }}
      </div>

      <div v-if="mbtLoading" style="margin-bottom:12px">
        <div class="progress-wrap"><div class="progress-bar"></div></div>
        <div class="progress-text">Fetching data &amp; replaying strategy…</div>
      </div>

      <div v-if="mbtResult">
        <!-- aggregate summary -->
        <div class="mbt-sum-grid" style="margin-bottom:10px">
          <div class="stat">
            <div class="lbl">Trading Days</div>
            <div class="val b">{{ mbtResult.trading_days }}</div>
          </div>
          <div class="stat">
            <div class="lbl">Total Trades</div>
            <div class="val b">{{ mbtResult.aggregate.total_trades }}</div>
          </div>
          <div class="stat">
            <div class="lbl">Win Rate</div>
            <div class="val g">{{ mbtResult.aggregate.win_rate_pct }}%</div>
          </div>
          <div class="stat">
            <div class="lbl">Total P&amp;L</div>
            <div class="val" :class="mbtResult.aggregate.total_points >= 0 ? 'g' : 'r'">
              {{ mbtResult.aggregate.total_points >= 0 ? '+' : '' }}{{ mbtResult.aggregate.total_points }} pts
            </div>
          </div>
          <div class="stat">
            <div class="lbl">Max Drawdown</div>
            <div class="val r">-{{ mbtResult.aggregate.max_drawdown_pts }} pts</div>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px">
          <div class="stat">
            <div class="lbl">Avg Win Pts</div>
            <div class="val g">+{{ mbtResult.aggregate.avg_win_points }}</div>
          </div>
          <div class="stat">
            <div class="lbl">Avg Loss Pts</div>
            <div class="val r">{{ mbtResult.aggregate.avg_loss_points }}</div>
          </div>
          <div class="stat">
            <div class="lbl">Best Day</div>
            <div class="val g" style="font-size:13px">{{ mbtResult.aggregate.best_day }} (+{{ mbtResult.aggregate.best_day_pts }})</div>
          </div>
          <div class="stat">
            <div class="lbl">Worst Day</div>
            <div class="val r" style="font-size:13px">{{ mbtResult.aggregate.worst_day }} ({{ mbtResult.aggregate.worst_day_pts }})</div>
          </div>
        </div>

        <!-- daily table -->
        <div v-if="mbtResult.daily?.length" style="margin-bottom:14px">
          <div class="section-label">
            Day-by-Day Results
            <span style="font-size:9px;letter-spacing:0;text-transform:none;color:var(--text-muted);margin-left:4px">
              click a row to expand details
            </span>
          </div>
          <div class="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Day</th>
                  <th>Trades</th>
                  <th>Wins</th>
                  <th>Losses</th>
                  <th>Win Rate</th>
                  <th>Nifty Pts</th>
                  <th>Est ₹ P&amp;L</th>
                  <th>Cumulative</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(day, idx) in mbtDailyRows"
                  :key="day.date"
                  class="mbt-row"
                  :class="{ active: selectedDay === idx }"
                  @click="showDetail(idx)"
                >
                  <td style="font-weight:600;color:var(--text-primary)">{{ day.date }}</td>
                  <td style="color:var(--text-muted)">{{ day.dow }}</td>
                  <td>{{ day.total_trades }}</td>
                  <td style="color:var(--green)">{{ day.wins }}</td>
                  <td style="color:var(--red)">{{ day.losses }}</td>
                  <td :style="{ color: day.win_rate_pct >= 50 ? 'var(--green)' : day.win_rate_pct > 0 ? 'var(--amber)' : 'var(--red)', fontWeight: 700 }">
                    {{ day.win_rate_pct }}%
                  </td>
                  <td :style="{ fontWeight: 700, color: day.total_points >= 0 ? 'var(--green)' : 'var(--red)' }">
                    {{ day.total_points >= 0 ? '+' : '' }}{{ day.total_points }}
                  </td>
                  <td :style="{ color: day.total_est_rs >= 0 ? 'var(--green)' : 'var(--red)' }">
                    {{ day.total_est_rs >= 0 ? '+' : '' }}₹{{ day.total_est_rs }}
                  </td>
                  <td>
                    <span :style="{ color: day.cumPts >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700, marginRight: '8px', fontFamily: 'var(--font-mono)' }">
                      {{ day.cumPts >= 0 ? '+' : '' }}{{ day.cumPts.toFixed(1) }}
                    </span>
                    <span class="cum-bar" :style="{ width: day.barW + 'px', background: day.cumPts >= 0 ? 'var(--green)' : 'var(--red)' }"></span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- day detail panel -->
        <div v-if="selectedDay !== null && mbtResult.daily[selectedDay]" class="bt-detail">
          <div class="bt-detail-header">
            <div class="bt-detail-title">{{ mbtResult.daily[selectedDay].date }} — Detailed View</div>
            <button class="btn s-btn" style="padding:4px 12px;font-size:11px" @click="selectedDay = null">✕ Close</button>
          </div>

          <div class="sum-grid" style="grid-template-columns:repeat(4,1fr);margin-bottom:14px">
            <div class="stat">
              <div class="lbl">Trades</div>
              <div class="val b">{{ mbtResult.daily[selectedDay].total_trades }}</div>
            </div>
            <div class="stat">
              <div class="lbl">Win Rate</div>
              <div class="val g">{{ mbtResult.daily[selectedDay].win_rate_pct }}%</div>
            </div>
            <div class="stat">
              <div class="lbl">Total Points</div>
              <div class="val" :class="mbtResult.daily[selectedDay].total_points >= 0 ? 'g' : 'r'">
                {{ mbtResult.daily[selectedDay].total_points >= 0 ? '+' : '' }}{{ mbtResult.daily[selectedDay].total_points }} pts
              </div>
            </div>
            <div class="stat">
              <div class="lbl">Est ₹</div>
              <div class="val" :class="mbtResult.daily[selectedDay].total_est_rs >= 0 ? 'g' : 'r'">
                {{ mbtResult.daily[selectedDay].total_est_rs >= 0 ? '+' : '' }}₹{{ mbtResult.daily[selectedDay].total_est_rs }}
              </div>
            </div>
          </div>

          <div class="section-label" style="margin-bottom:10px">Trades</div>
          <div class="tbl-wrap" style="margin-bottom:14px">
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>Signal</th>
                  <th>Entry Time</th>
                  <th>Entry Nifty</th>
                  <th>Entry Reason</th>
                  <th>Exit Time</th>
                  <th>Exit Nifty</th>
                  <th>Nifty Pts</th>
                  <th>Est ₹</th>
                  <th>Exit Reason</th>
                  <th>Result</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="!mbtResult.daily[selectedDay].trades?.length">
                  <td colspan="11" class="empty-state">No trades on this day</td>
                </tr>
                <tr v-for="t in mbtResult.daily[selectedDay].trades" :key="t.num">
                  <td style="color:var(--text-muted)">{{ t.num }}</td>
                  <td>
                    <span class="pill" :class="t.signal === 'BUY_CE' ? 'pill-ce' : 'pill-pe'">
                      {{ t.signal === 'BUY_CE' ? 'CE' : 'PE' }}
                    </span>
                  </td>
                  <td>{{ t.entry_time }}</td>
                  <td style="color:var(--text-primary);font-weight:600">{{ t.entry_nifty }}</td>
                  <td style="font-size:11px;color:var(--text-muted)">
                    <span v-if="t.entry_reason && t.entry_reason.length > 40" :title="t.entry_reason" style="cursor:help">{{ t.entry_reason.slice(0, 38) }}…</span>
                    <span v-else>{{ t.entry_reason || '—' }}</span>
                  </td>
                  <td>{{ t.exit_time }}</td>
                  <td style="color:var(--text-primary);font-weight:600">{{ t.exit_nifty }}</td>
                  <td :style="{ fontWeight: 700, color: t.points >= 0 ? 'var(--green)' : 'var(--red)' }">
                    {{ t.points >= 0 ? '+' : '' }}{{ t.points }} pts
                  </td>
                  <td :style="{ fontWeight: 700, color: t.est_options_pnl >= 0 ? 'var(--green)' : 'var(--red)' }">
                    {{ t.est_options_pnl >= 0 ? '+' : '' }}₹{{ t.est_options_pnl }}
                  </td>
                  <td><span class="pill pill-exit">{{ t.exit_reason }}</span></td>
                  <td>
                    <span class="pill" :class="t.result === 'WIN' ? 'pill-win' : 'pill-loss'">{{ t.result }}</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="section-label" style="margin-bottom:10px">
            5-Min Candle Timeline
            <span style="font-size:9px;letter-spacing:0;text-transform:none;color:var(--text-muted);margin-left:4px">
              highlighted = in trade
            </span>
          </div>
          <div class="tbl-wrap" style="max-height:380px;overflow-y:auto">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Open</th><th>High</th><th>Low</th><th>Close</th>
                  <th>VWAP</th><th>EMA 20</th>
                  <th>Market</th><th>Signal</th><th>Note</th>
                </tr>
              </thead>
              <tbody>
                <CandleRow v-for="c in mbtResult.daily[selectedDay].candles" :key="c.time" :c="c" />
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed } from 'vue'

// ── tiny inline component for candle rows ──────────────────────────────────
const CandleRow = {
  props: ['c'],
  template: `
    <tr :style="c.in_trade ? 'background:rgba(59,130,246,0.07)' : ''">
      <td style="font-weight:600;color:var(--text-primary)">{{ c.time }}</td>
      <td>{{ c.open }}</td><td>{{ c.high }}</td><td>{{ c.low }}</td>
      <td style="font-weight:600;color:var(--text-primary)">{{ c.close }}</td>
      <td style="color:var(--amber)">{{ c.vwap }}</td>
      <td style="color:var(--accent)">{{ c.ema20 || '—' }}</td>
      <td>
        <span v-if="c.market_state === 'TRENDING'" style="color:var(--green);font-weight:700;font-size:10px">▲ TREND</span>
        <span v-else-if="c.market_state === 'SIDEWAYS'" style="color:var(--amber);font-weight:700;font-size:10px">↔ SIDE</span>
        <span v-else style="color:var(--text-muted);font-size:10px">—</span>
      </td>
      <td :style="{ color: c.signal === 'BUY_CE' ? '#60a5fa' : c.signal === 'BUY_PE' ? '#fb7185' : 'var(--text-muted)', fontWeight: 700, fontSize: '11px' }">{{ c.signal }}</td>
      <td :style="{ color: c.note.startsWith('ENTRY') ? 'var(--accent)' : c.note.startsWith('EXIT') ? 'var(--red)' : 'var(--text-muted)', fontWeight: (c.note.startsWith('ENTRY') || c.note.startsWith('EXIT')) ? 700 : 400, fontSize: '11px' }">{{ c.note }}</td>
    </tr>
  `
}

// ─── helpers ────────────────────────────────────────────────────────────────
function localDateStr(d) {
  return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0')
}

// ─── single-day state ────────────────────────────────────────────────────────
function makeYesterday() {
  const d = new Date(); d.setDate(d.getDate() - 1); return d
}
const yesterday = computed(() => localDateStr(makeYesterday()))
const btDate    = ref(localDateStr(makeYesterday()))
const btLoading = ref(false)
const btResult  = ref(null)
const btMsg     = ref({ text: '', type: '' })

async function runBacktest() {
  if (!btDate.value) { btMsg.value = { text: 'Please select a date', type: 'err' }; return }
  btLoading.value = true
  btResult.value  = null
  btMsg.value     = { text: 'Fetching historical data and replaying strategy…', type: 'info' }
  try {
    const r = await fetch('/backtest/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date: btDate.value })
    })
    const d = await r.json()
    if (!r.ok) { btMsg.value = { text: d.detail || 'Error running backtest', type: 'err' }; return }
    btMsg.value    = { text: '', type: '' }
    btResult.value = d
  } catch (e) { btMsg.value = { text: 'Network error: ' + e, type: 'err' } }
  finally { btLoading.value = false }
}

// ─── multi-day state ─────────────────────────────────────────────────────────
const initTo   = new Date(); initTo.setDate(initTo.getDate()-1)
const initFrom = new Date(); initFrom.setDate(initFrom.getDate()-11)
const mbtFrom   = ref(localDateStr(initFrom))
const mbtTo     = ref(localDateStr(initTo))
const mbtLoading = ref(false)
const mbtResult  = ref(null)
const mbtMsg     = ref({ text: '', type: '' })
const selectedDay = ref(null)

function setRange(days) {
  const to   = new Date(); to.setDate(to.getDate()-1)
  const from = new Date(); from.setDate(from.getDate()-1-days)
  mbtTo.value   = localDateStr(to)
  mbtFrom.value = localDateStr(from)
}

async function runMultiBacktest() {
  if (!mbtFrom.value || !mbtTo.value) { mbtMsg.value = { text: 'Select both dates', type: 'err' }; return }
  mbtLoading.value = true
  mbtResult.value  = null
  selectedDay.value = null
  mbtMsg.value     = { text: 'Fetching data & replaying strategy for each trading day…', type: 'info' }
  try {
    const r = await fetch('/backtest/run-multi', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_date: mbtFrom.value, to_date: mbtTo.value })
    })
    const d = await r.json()
    if (!r.ok) { mbtMsg.value = { text: d.detail || 'Error', type: 'err' }; return }
    mbtMsg.value    = { text: '', type: '' }
    mbtResult.value = d
  } catch (e) { mbtMsg.value = { text: 'Network error: ' + e, type: 'err' } }
  finally { mbtLoading.value = false }
}

const DOW = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']

const mbtDailyRows = computed(() => {
  if (!mbtResult.value?.daily) return []
  const days = mbtResult.value.daily
  const maxAbsCum = Math.max(
    ...days.reduce((acc, day, i) => {
      const prev = acc[i-1] || 0
      acc.push(prev + day.total_points)
      return acc
    }, []).map(Math.abs),
    1
  )
  let cumPts = 0
  return days.map(day => {
    cumPts += day.total_points
    const dt = new Date(day.date + 'T00:00:00')
    return {
      ...day,
      dow:    DOW[dt.getDay()],
      cumPts,
      barW:   Math.max(2, Math.abs(cumPts) / maxAbsCum * 60),
    }
  })
})

function showDetail(idx) {
  selectedDay.value = idx
  setTimeout(() => {
    document.querySelector('.bt-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, 50)
}
</script>
