<template>
  <div class="page">

    <div class="page-header">
      <div>
        <div class="page-title">Authentication &amp; Settings</div>
        <div class="page-subtitle">Zerodha Kite Connect OAuth · API access management</div>
      </div>
    </div>

    <!-- ── AUTH STATUS CARD ── -->
    <div class="card" :style="{ borderColor: isAuthenticated ? 'rgba(16,185,129,0.3)' : 'rgba(244,63,94,0.3)' }">
      <div class="card-header">
        <div class="card-title">Connection Status</div>
        <span
          class="auth-status-badge"
          :class="isAuthenticated ? 'auth-ok' : 'auth-fail'"
        >
          {{ isAuthenticated === null ? '● Checking…' : isAuthenticated ? '● Authenticated' : '● Not Connected' }}
        </span>
      </div>

      <div v-if="isAuthenticated" class="auth-profile-row">
        <div class="auth-avatar">{{ initials }}</div>
        <div>
          <div class="auth-name">{{ profile?.user_name || profile?.user_id || '—' }}</div>
          <div class="auth-meta">{{ profile?.email || '—' }}</div>
          <div class="auth-meta" style="margin-top:2px">Broker: {{ profile?.broker || '—' }} · ID: {{ profile?.user_id || '—' }}</div>
        </div>
      </div>
      <div v-else-if="isAuthenticated === false" class="auth-disconnected">
        <div style="font-size:13px;color:var(--text-secondary);margin-bottom:12px">
          No active Zerodha session. Click below to authenticate via Kite Connect OAuth.
        </div>
        <a href="/login" class="btn g-btn" style="text-decoration:none">
          🔗 Login with Zerodha
        </a>
      </div>

      <div v-if="isAuthenticated" style="margin-top:14px;display:flex;gap:8px;flex-wrap:wrap">
        <a href="/logout" class="btn r-btn" style="text-decoration:none;font-size:12px">↩ Logout</a>
        <a href="/login" class="btn s-btn" style="text-decoration:none;font-size:12px">↻ Re-authenticate</a>
      </div>
    </div>

    <!-- ── QUICK LINKS ── -->
    <div class="grid2">
      <div class="card">
        <div class="card-title" style="margin-bottom:14px">Zerodha OAuth</div>
        <div class="auth-link-row">
          <span class="mt mget">GET</span>
          <a href="/login">Login — Start OAuth flow</a>
        </div>
        <div class="auth-link-row">
          <span class="mt mget">GET</span>
          <a href="/logout">Logout — Clear session token</a>
        </div>
        <div class="auth-link-row">
          <span class="mt mget">GET</span>
          <a href="/callback">Callback URL (auto-used by Zerodha)</a>
        </div>
      </div>

      <div class="card">
        <div class="card-title" style="margin-bottom:14px">API Documentation</div>
        <div class="auth-link-row">
          <span class="mt mget">GET</span>
          <a href="/docs" target="_blank">Swagger UI — Interactive API explorer</a>
        </div>
        <div class="auth-link-row">
          <span class="mt mget">GET</span>
          <a href="/redoc" target="_blank">ReDoc — API reference docs</a>
        </div>
      </div>
    </div>

    <!-- ── TRADING CONFIG ── -->
    <div class="card">
      <div class="card-title" style="margin-bottom:14px">Configuration</div>
      <div class="config-grid">
        <div class="config-item">
          <div class="lbl">Trading Mode</div>
          <div class="config-val">
            <span class="badge" :class="config.trading_mode === 'LIVE' ? 'bl' : 'bp'">
              {{ config.trading_mode || '—' }}
            </span>
          </div>
        </div>
        <div class="config-item">
          <div class="lbl">Max Trades / Day</div>
          <div class="config-val mono">{{ config.max_trades || '—' }}</div>
        </div>
        <div class="config-item">
          <div class="lbl">Entry Window</div>
          <div class="config-val mono">09:50 — 14:00 IST</div>
        </div>
        <div class="config-item">
          <div class="lbl">Force Exit</div>
          <div class="config-val mono">15:20 IST</div>
        </div>
        <div class="config-item">
          <div class="lbl">Stop Loss</div>
          <div class="config-val mono">20% fixed</div>
        </div>
        <div class="config-item">
          <div class="lbl">Target</div>
          <div class="config-val mono">35%</div>
        </div>
        <div class="config-item">
          <div class="lbl">Trailing SL trigger</div>
          <div class="config-val mono">+20% → trails 10% below peak</div>
        </div>
        <div class="config-item">
          <div class="lbl">Strategy</div>
          <div class="config-val" style="font-size:12px;color:var(--text-secondary)">VWAP + EMA Breakout v2</div>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'

const profile         = ref(null)
const isAuthenticated = ref(null)
const config          = ref({})

const initials = computed(() => {
  const name = profile.value?.user_name || ''
  return name.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase() || '??'
})

async function checkAuth() {
  try {
    const r = await fetch('/profile')
    if (r.ok) {
      profile.value = await r.json()
      isAuthenticated.value = true
    } else {
      isAuthenticated.value = false
    }
  } catch {
    isAuthenticated.value = false
  }
}

async function loadConfig() {
  try {
    const r = await fetch('/auto-trading/status')
    if (r.ok) {
      const d = await r.json()
      config.value = { trading_mode: d.mode, max_trades: d.max_trades }
    }
  } catch (_) {}
}

onMounted(() => {
  checkAuth()
  loadConfig()
})
</script>
