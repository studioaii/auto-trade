<template>
  <div ref="wrapper">
    <canvas ref="mainCanvas" style="display:block;width:100%;height:380px;cursor:crosshair"></canvas>
    <canvas ref="rsiCanvas"  style="display:block;width:100%;height:110px;margin-top:3px;cursor:crosshair"></canvas>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'

const props = defineProps({
  candles: { type: Array, default: () => [] },
  marker:  { type: Object, default: null }
})

const wrapper    = ref(null)
const mainCanvas = ref(null)
const rsiCanvas  = ref(null)
let chart        = null
let resizeObs    = null

// ─── helpers ───────────────────────────────────────────────────────────────
function niceStep(range, targetSteps) {
  const raw = range / targetSteps
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const n   = raw / mag
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * mag
}

function fmtTime(unixSec) {
  const d = new Date(unixSec * 1000)
  return d.getUTCHours().toString().padStart(2,'0') + ':' + d.getUTCMinutes().toString().padStart(2,'0')
}

function rrect(ctx, x, y, w, h, r) {
  ctx.beginPath()
  ctx.moveTo(x+r, y)
  ctx.lineTo(x+w-r, y); ctx.quadraticCurveTo(x+w, y, x+w, y+r)
  ctx.lineTo(x+w, y+h-r); ctx.quadraticCurveTo(x+w, y+h, x+w-r, y+h)
  ctx.lineTo(x+r, y+h); ctx.quadraticCurveTo(x, y+h, x, y+h-r)
  ctx.lineTo(x, y+r); ctx.quadraticCurveTo(x, y, x+r, y)
  ctx.closePath()
}

// Clamp a Y pixel to the chart area so out-of-range indicators don't vanish
function clampY(y, top, bottom) {
  return Math.max(top, Math.min(bottom, y))
}

// ─── chart class ───────────────────────────────────────────────────────────
class CandleChart {
  constructor(mc, rc) {
    this.mc = mc; this.rc = rc
    this.mctx = mc.getContext('2d')
    this.rctx = rc.getContext('2d')
    this.candles  = []
    this.marker   = null
    this.hoverIdx = -1

    mc.addEventListener('mousemove',  e => this._hover(e))
    mc.addEventListener('mouseleave', () => { this.hoverIdx = -1; this._draw() })
  }

  resize() {
    const w = this.mc.parentElement.clientWidth
    if (!w) return
    this.mc.width = w; this.mc.style.width = w+'px'
    this.rc.width = w; this.rc.style.width = w+'px'
    this._draw()
  }

  setData(candles, marker) {
    this.candles = candles
    this.marker  = marker
    this.resize()
  }

  _hover(e) {
    if (!this.candles.length) return
    const rect = this.mc.getBoundingClientRect()
    const x    = (e.clientX - rect.left) * (this.mc.width / rect.width)
    const PR   = 65, PL = 8
    const cw   = (this.mc.width - PL - PR) / this.candles.length
    const idx  = Math.floor((x - PL) / cw)
    if (idx >= 0 && idx < this.candles.length && idx !== this.hoverIdx) {
      this.hoverIdx = idx
      this._draw()
    }
  }

  _draw() { this._drawMain(); this._drawRsi() }

  _drawMain() {
    const { mc, mctx: ctx, candles, marker, hoverIdx } = this
    const W = mc.width, H = mc.height
    if (!W || !H) return
    ctx.clearRect(0, 0, W, H)
    if (!candles.length) return

    const PL = 8, PR = 65, PT = 16, PB = 28
    const chartW = W - PL - PR
    const chartH = H - PT - PB
    const volH   = Math.max(30, Math.floor(chartH * 0.14))
    const priceH = chartH - volH - 4

    const n  = candles.length
    const cw = chartW / n
    const bw = Math.max(1, Math.floor(cw * 0.6))
    const toX = i => PL + i * cw + cw / 2

    // ── Price range: OHLC sets the base; EMA20/VWAP can extend it by at most 1× the candle range
    let candleLo = Infinity, candleHi = -Infinity
    for (const c of candles) {
      const vals = [c.high, c.low, c.open, c.close].filter(v => v != null && isFinite(v))
      for (const v of vals) { candleLo = Math.min(candleLo, v); candleHi = Math.max(candleHi, v) }
    }
    const candleRange = candleHi - candleLo || 1
    let lo = candleLo, hi = candleHi
    for (const c of candles) {
      for (const v of [c.ema20, c.vwap]) {
        if (v == null || !isFinite(v)) continue
        lo = Math.min(lo, Math.max(candleLo - candleRange, v))
        hi = Math.max(hi, Math.min(candleHi + candleRange, v))
      }
    }
    if (!isFinite(lo) || !isFinite(hi)) return

    // Ensure a minimum visible range so flat-price days don't look like a line
    const minRange = Math.max(hi * 0.004, 10)
    if (hi - lo < minRange) {
      const mid = (hi + lo) / 2
      lo = mid - minRange / 2
      hi = mid + minRange / 2
    }

    const pad = (hi - lo) * 0.10   // 10% breathing room top and bottom
    lo -= pad; hi += pad

    const toY       = p  => PT + priceH * (1 - (p - lo) / (hi - lo))
    const toYClamped = p => clampY(toY(p), PT, PT + priceH)

    // Volume
    const maxVol = Math.max(...candles.map(c => c.volume || 0), 1)
    const volTop = PT + priceH + 4
    const toVH   = v => Math.max(1, (v / maxVol) * (volH - 2))

    // Background
    ctx.fillStyle = '#07090f'; ctx.fillRect(0, 0, W, H)

    // Grid + price axis
    const step  = niceStep(hi - lo, 6)
    const first = Math.ceil(lo / step) * step
    ctx.textAlign = 'left'; ctx.font = '10px system-ui'; ctx.fillStyle = '#4e6280'
    for (let p = first; p <= hi + step * 0.1; p += step) {
      const y = toY(p)
      if (y < PT - 2 || y > PT + priceH + 2) continue
      ctx.strokeStyle = 'rgba(26,46,74,0.7)'; ctx.lineWidth = 1; ctx.setLineDash([])
      ctx.beginPath(); ctx.moveTo(PL, y); ctx.lineTo(W - PR, y); ctx.stroke()
      ctx.fillText(p.toFixed(0), W - PR + 5, y + 3)
    }

    // Time axis
    ctx.textAlign = 'center'; ctx.fillStyle = '#4e6280'; ctx.font = '10px system-ui'
    const tStep = Math.max(1, Math.round(60 / cw))
    for (let i = 0; i < n; i += tStep) {
      ctx.fillText(fmtTime(candles[i].time), toX(i), H - PB + 14)
    }

    // Volume bars
    for (let i = 0; i < n; i++) {
      const c = candles[i]
      const x = toX(i), vh = toVH(c.volume || 0)
      ctx.fillStyle = c.close >= c.open ? 'rgba(16,185,129,0.25)' : 'rgba(244,63,94,0.25)'
      ctx.fillRect(x - bw/2, volTop + volH - vh, bw, vh)
    }

    // ── VWAP line (clipped to chart area — does NOT stretch Y scale)
    ctx.strokeStyle = '#f59e0b'; ctx.lineWidth = 1.8; ctx.setLineDash([])
    ctx.save(); ctx.beginPath(); ctx.rect(PL, PT, chartW, priceH); ctx.clip()
    ctx.beginPath(); let started = false
    for (let i = 0; i < n; i++) {
      if (candles[i].vwap == null || !isFinite(candles[i].vwap)) { started = false; continue }
      const x = toX(i), y = toY(candles[i].vwap)
      started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true)
    }
    ctx.stroke(); ctx.restore()

    // ── EMA20 line (clipped to chart area — does NOT stretch Y scale)
    ctx.strokeStyle = '#3b82f6'; ctx.lineWidth = 1.8
    ctx.save(); ctx.beginPath(); ctx.rect(PL, PT, chartW, priceH); ctx.clip()
    ctx.beginPath(); started = false
    for (let i = 0; i < n; i++) {
      if (candles[i].ema20 == null || !isFinite(candles[i].ema20)) { started = false; continue }
      const x = toX(i), y = toY(candles[i].ema20)
      started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true)
    }
    ctx.stroke(); ctx.restore()

    // ── Candlesticks
    for (let i = 0; i < n; i++) {
      const c = candles[i], x = toX(i)
      if (!isFinite(c.high) || !isFinite(c.low) || !isFinite(c.open) || !isFinite(c.close)) continue
      const isUp = c.close >= c.open
      const body = isUp ? '#10b981' : '#f43f5e'
      const wick = isUp ? '#059669' : '#e11d48'

      // Wick
      ctx.strokeStyle = wick; ctx.lineWidth = 1
      ctx.beginPath()
      ctx.moveTo(x, toYClamped(c.high))
      ctx.lineTo(x, toYClamped(c.low))
      ctx.stroke()

      // Body (open → close)
      const yTop = toYClamped(Math.max(c.open, c.close))
      const yBot = toYClamped(Math.min(c.open, c.close))
      ctx.fillStyle = body
      ctx.fillRect(x - bw/2, yTop, bw, Math.max(1, yBot - yTop))
    }

    // Entry marker
    if (marker) {
      const mi = candles.findIndex(c => c.time === marker.time)
      if (mi >= 0) {
        const x    = toX(mi)
        const isCE = marker.type === 'CE'
        const cy   = isCE ? toYClamped(candles[mi].low) + 14 : toYClamped(candles[mi].high) - 14
        ctx.fillStyle = isCE ? '#10b981' : '#f43f5e'
        ctx.font = '13px system-ui'; ctx.textAlign = 'center'
        ctx.fillText(isCE ? '▲' : '▼', x, cy)
        ctx.font = 'bold 8px system-ui'
        ctx.fillText('ENTRY', x, isCE ? cy + 10 : cy - 5)
      }
    }

    // Border
    ctx.strokeStyle = 'rgba(26,46,74,0.8)'; ctx.lineWidth = 1; ctx.setLineDash([])
    ctx.strokeRect(PL, PT, chartW, priceH)

    // Crosshair + tooltip
    if (hoverIdx >= 0 && hoverIdx < n) {
      const c = candles[hoverIdx], x = toX(hoverIdx)
      ctx.strokeStyle = 'rgba(148,163,184,0.35)'; ctx.lineWidth = 1; ctx.setLineDash([4,4])
      ctx.beginPath(); ctx.moveTo(x, PT); ctx.lineTo(x, PT + priceH); ctx.stroke()
      ctx.setLineDash([])

      const lines = [
        fmtTime(c.time),
        'O: '+c.open.toFixed(1)+'  H: '+c.high.toFixed(1),
        'L: '+c.low.toFixed(1)+'  C: '+c.close.toFixed(1),
        c.vwap  != null ? 'VWAP: '+c.vwap.toFixed(2)  : null,
        c.ema20 != null ? 'EMA20: '+c.ema20.toFixed(2) : null,
        c.rsi14 != null ? 'RSI14: '+c.rsi14.toFixed(1) : null,
        c.volume > 0    ? 'Vol: '+c.volume.toLocaleString() : null,
      ].filter(Boolean)

      const TW = 150, TH = lines.length * 16 + 12
      let tx = x + 12
      if (tx + TW > W - PR - 4) tx = x - TW - 12
      const ty = PT + 6

      ctx.fillStyle = 'rgba(10,14,26,0.94)'
      rrect(ctx, tx, ty, TW, TH, 6); ctx.fill()
      ctx.strokeStyle = 'rgba(26,46,74,0.9)'; ctx.lineWidth = 1
      rrect(ctx, tx, ty, TW, TH, 6); ctx.stroke()

      ctx.font = '11px system-ui'; ctx.textAlign = 'left'
      lines.forEach((l, j) => {
        ctx.fillStyle = j === 0 ? '#7dd3fc' : j <= 2 ? '#e2e8f0'
          : l.startsWith('VWAP') ? '#fcd34d'
          : l.startsWith('EMA')  ? '#93c5fd'
          : l.startsWith('RSI')  ? '#c4b5fd'
          : '#94a3b8'
        ctx.fillText(l, tx + 8, ty + 14 + j * 16)
      })
    }
  }

  _drawRsi() {
    const { rc, rctx: ctx, candles, hoverIdx } = this
    const W = rc.width, H = rc.height
    if (!W || !H) return
    ctx.clearRect(0, 0, W, H)

    const PL = 8, PR = 65, PT = 8, PB = 18
    const chartW = W - PL - PR
    const chartH = H - PT - PB
    const n  = candles.length
    const cw = chartW / n
    const toX = i => PL + i * cw + cw / 2
    const toY = v => PT + chartH * (1 - (v / 100))

    ctx.fillStyle = '#07090f'; ctx.fillRect(0, 0, W, H)

    for (const [level, color, dash] of [[70,'rgba(244,63,94,0.35)',[4,4]],[50,'rgba(26,46,74,0.8)',[]],[30,'rgba(16,185,129,0.35)',[4,4]]]) {
      const y = toY(level)
      ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.setLineDash(dash)
      ctx.beginPath(); ctx.moveTo(PL, y); ctx.lineTo(W - PR, y); ctx.stroke()
      ctx.setLineDash([])
      ctx.fillStyle = '#4e6280'; ctx.font = '9px system-ui'; ctx.textAlign = 'left'
      ctx.fillText(level, W - PR + 5, y + 3)
    }

    ctx.strokeStyle = '#8b5cf6'; ctx.lineWidth = 1.8
    ctx.beginPath(); let started = false
    for (let i = 0; i < n; i++) {
      if (candles[i].rsi14 == null) continue
      const x = toX(i), y = toY(candles[i].rsi14)
      started ? ctx.lineTo(x, y) : (ctx.moveTo(x, y), started = true)
    }
    ctx.stroke()

    ctx.fillStyle = '#8b5cf6'; ctx.font = 'bold 9px system-ui'; ctx.textAlign = 'left'
    ctx.fillText('RSI 14', PL + 4, PT + 10)

    ctx.strokeStyle = 'rgba(26,46,74,0.8)'; ctx.lineWidth = 1; ctx.setLineDash([])
    ctx.strokeRect(PL, PT, chartW, chartH)

    if (hoverIdx >= 0 && hoverIdx < n && candles[hoverIdx].rsi14 != null) {
      const x = toX(hoverIdx)
      ctx.strokeStyle = 'rgba(148,163,184,0.35)'; ctx.lineWidth = 1; ctx.setLineDash([4,4])
      ctx.beginPath(); ctx.moveTo(x, PT); ctx.lineTo(x, PT + chartH); ctx.stroke()
      ctx.setLineDash([])
      const rsi = candles[hoverIdx].rsi14
      ctx.fillStyle = rsi >= 70 ? '#f43f5e' : rsi <= 30 ? '#10b981' : '#8b5cf6'
      ctx.font = 'bold 10px system-ui'; ctx.textAlign = 'right'
      ctx.fillText(rsi.toFixed(1), W - PR - 4, PT + 10)
    }
  }
}

// ─── lifecycle ─────────────────────────────────────────────────────────────
onMounted(() => {
  const mc = mainCanvas.value
  const rc = rsiCanvas.value
  mc.height = 380; rc.height = 110
  chart = new CandleChart(mc, rc)

  resizeObs = new ResizeObserver(() => chart.resize())
  resizeObs.observe(wrapper.value)

  if (props.candles.length) {
    chart.setData(props.candles, props.marker)
  }
})

onUnmounted(() => {
  resizeObs?.disconnect()
})

watch(
  () => [props.candles, props.marker],
  ([candles, marker]) => {
    if (chart) chart.setData(candles, marker)
  },
  { deep: true }
)
</script>
