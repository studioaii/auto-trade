<template>
  <div class="strat-banner" :class="bannerClass">
    <!-- Top strip: entry_mode + bias + VIX + event + force-exit -->
    <div class="strat-top">
      <div class="strat-tile">
        <div class="strat-lbl">Strategy</div>
        <div class="strat-val mode" :class="modeClass">{{ entryModeLabel }}</div>
      </div>

      <div class="strat-tile">
        <div class="strat-lbl">Day Bias</div>
        <div class="strat-val" :class="biasClass">
          {{ biasLabel }}
          <span v-if="status.day_bias_set_at" class="strat-sub">@ {{ status.day_bias_set_at }}</span>
        </div>
      </div>

      <div class="strat-tile">
        <div class="strat-lbl">India VIX</div>
        <div class="strat-val" :class="vixClass">
          {{ status.vix_ltp > 0 ? status.vix_ltp.toFixed(2) : '—' }}
          <span class="strat-sub">/ max {{ status.vix_max?.toFixed(0) || '22' }}</span>
        </div>
      </div>

      <div class="strat-tile">
        <div class="strat-lbl">Event Today</div>
        <div class="strat-val" :class="status.event_today ? 'evt-on' : 'evt-off'">
          {{ status.event_today || '— none' }}
        </div>
      </div>

      <div class="strat-tile">
        <div class="strat-lbl">Force Exit</div>
        <div class="strat-val">{{ status.force_exit_time || '14:30' }}</div>
      </div>

      <div class="strat-tile" v-if="entryMode === 'mean_reversion'">
        <div class="strat-lbl">Failed Fades</div>
        <div class="strat-val" :class="status.failed_reversion_attempts_today >= 2 ? 'pill-loss' : ''">
          {{ status.failed_reversion_attempts_today || 0 }} / 2
        </div>
      </div>
    </div>

    <!-- Block reason (only when entries are blocked AND no position open) -->
    <div v-if="blockMessage" class="strat-block">
      <span class="strat-block-icon">⛔</span>
      <span class="strat-block-msg">
        <b>Entries blocked:</b> {{ blockMessage }}
      </span>
    </div>

    <!-- Subtle hint when engine is running and waiting for setup -->
    <div v-else-if="status.engine_running && !status.position && !blockMessage"
         class="strat-watch">
      <span class="strat-watch-icon">⏳</span>
      <span>
        Waiting for {{ entryMode === 'mean_reversion' ? 'failed-spike pattern' : 'pullback + resume' }}…
        ({{ status.last_signal === 'NO_SIGNAL' ? 'no signal yet' : status.last_signal }})
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: { type: Object, required: true },
})

const status = computed(() => props.status || {})

const entryMode = computed(() => status.value.entry_mode || 'vwap_ema_breakout')

const entryModeLabel = computed(() => {
  const m = entryMode.value
  if (m === 'trend_pullback')   return 'Trend Pullback'
  if (m === 'mean_reversion')   return 'Mean Reversion'
  if (m === 'vwap_ema_breakout') return 'VWAP+EMA (legacy)'
  return m
})

const modeClass = computed(() => {
  const m = entryMode.value
  if (m === 'trend_pullback')   return 'mode-tp'
  if (m === 'mean_reversion')   return 'mode-mr'
  return 'mode-legacy'
})

const biasLabel = computed(() => {
  const b = status.value.day_bias
  if (b === 'UP')        return '▲ UP'
  if (b === 'DOWN')      return '▼ DOWN'
  if (b === 'NEUTRAL')   return '↔ NEUTRAL'
  if (b === 'NO_TRADE')  return '⛔ NO_TRADE'
  if (b === 'PENDING')   return '⏳ PENDING'
  return b || '—'
})

const biasClass = computed(() => {
  const b = status.value.day_bias
  if (b === 'UP')        return 'bias-up'
  if (b === 'DOWN')      return 'bias-down'
  if (b === 'NEUTRAL')   return 'bias-neutral'
  if (b === 'NO_TRADE')  return 'bias-notrade'
  if (b === 'PENDING')   return 'bias-pending'
  return ''
})

const vixClass = computed(() => {
  const v = status.value.vix_ltp || 0
  const max = status.value.vix_max || 22
  if (v <= 0)        return 'vix-na'
  if (v >= max)      return 'vix-hot'
  if (v >= max * 0.85) return 'vix-warn'
  return 'vix-ok'
})

const blockMessage = computed(() => {
  // Only show when engine running and no position (don't repeat trivial reasons)
  if (!status.value.engine_running) return ''
  if (status.value.position)        return ''
  const r = status.value.block_reason
  if (!r) return ''
  // Friendly translations of common machine reasons
  if (r.startsWith('EVENT_DAY:')) {
    return `Today is an event day (${r.split(':')[1]}). Skipping all entries.`
  }
  if (r.startsWith('VIX_HIGH:')) {
    return `India VIX ${r.split(':')[1]} above threshold (${status.value.vix_max}). Volatility too high.`
  }
  if (r === 'DAY_BIAS_NO_TRADE') {
    return 'Day-bias classified as NO_TRADE (extreme gap, opening RSI, or low efficiency).'
  }
  if (r.startsWith('Max trades')) {
    return r + ' — wait until tomorrow.'
  }
  if (r.startsWith('Second entry blocked')) {
    return 'First trade hit hard SL — second entry blocked for the day.'
  }
  if (r.startsWith('Too early')) {
    return 'Pre-market — entries open at 09:50 IST.'
  }
  if (r.startsWith('Past last entry')) {
    return 'Past 14:00 IST — no new entries (only managing open position).'
  }
  return r
})

const bannerClass = computed(() => {
  if (blockMessage.value) return 'strat-blocked'
  if (status.value.day_bias === 'UP')   return 'strat-bull'
  if (status.value.day_bias === 'DOWN') return 'strat-bear'
  return ''
})
</script>

<style scoped>
.strat-banner {
  background: linear-gradient(180deg, rgba(30,41,59,0.6), rgba(15,23,42,0.4));
  border: 1px solid rgba(148,163,184,0.15);
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 14px;
}
.strat-bull    { border-color: rgba(16,185,129,0.35); }
.strat-bear    { border-color: rgba(244,63,94,0.35); }
.strat-blocked { border-color: rgba(245,158,11,0.45); background: linear-gradient(180deg, rgba(120,53,15,0.18), rgba(30,17,7,0.3)); }

.strat-top {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
}

.strat-tile {
  padding: 6px 10px;
  background: rgba(15,23,42,0.45);
  border-radius: 6px;
  border: 1px solid rgba(148,163,184,0.08);
}
.strat-lbl {
  font-size: 9px;
  letter-spacing: 0.7px;
  color: var(--text-muted);
  text-transform: uppercase;
  font-weight: 600;
  margin-bottom: 2px;
}
.strat-val {
  font-size: 14px;
  font-weight: 700;
  color: var(--text-primary);
  font-family: var(--font-mono);
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.strat-sub {
  font-size: 10px;
  color: var(--text-muted);
  font-weight: 400;
}

/* mode colours */
.mode-tp     { color: #60a5fa; }
.mode-mr     { color: #c084fc; }
.mode-legacy { color: var(--text-muted); }

/* bias colours */
.bias-up      { color: var(--green); }
.bias-down    { color: var(--red); }
.bias-neutral { color: var(--amber); }
.bias-notrade { color: #f97316; }
.bias-pending { color: var(--text-muted); }

/* VIX colours */
.vix-ok    { color: var(--green); }
.vix-warn  { color: var(--amber); }
.vix-hot   { color: var(--red); }
.vix-na    { color: var(--text-muted); }

/* event tile */
.evt-on  { color: #fb923c; }
.evt-off { color: var(--text-muted); font-weight: 500; }

/* block + watch rows */
.strat-block {
  margin-top: 10px;
  padding: 8px 12px;
  background: rgba(245,158,11,0.12);
  border-left: 3px solid #f59e0b;
  border-radius: 4px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12.5px;
  color: #fde68a;
}
.strat-block-icon { font-size: 16px; }
.strat-block-msg b { color: #fbbf24; }

.strat-watch {
  margin-top: 10px;
  padding: 7px 12px;
  background: rgba(59,130,246,0.08);
  border-left: 3px solid #3b82f6;
  border-radius: 4px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: #93c5fd;
}
.strat-watch-icon { font-size: 14px; }

/* small loss-pill reuse */
.pill-loss { color: var(--red); }
</style>
