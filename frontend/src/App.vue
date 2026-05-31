<template>
  <div v-if="authChecking" class="auth-loading">
    <div class="auth-loading-inner">
      <div class="auth-loading-icon">⚡</div>
      <div class="auth-loading-text">Connecting to Zerodha…</div>
      <div class="auth-loading-sub">Verifying session token</div>
    </div>
  </div>

  <div v-else class="app">
    <aside class="sidebar">
      <div class="sidebar-brand">
        <div class="brand-icon">⚡</div>
        <div class="brand-text">
          <div class="brand-name">AutoTrade</div>
          <div class="brand-sub">Multi-Instrument Engine</div>
        </div>
      </div>

      <nav class="sidebar-nav">
        <RouterLink to="/" class="nav-item" exact-active-class="router-link-exact-active">
          <span class="nav-icon">📊</span>
          <span class="nav-label">NIFTY 50</span>
        </RouterLink>
        <RouterLink to="/banknifty" class="nav-item">
          <span class="nav-icon">🏦</span>
          <span class="nav-label">BANK NIFTY</span>
        </RouterLink>
        <RouterLink to="/banknifty2" class="nav-item">
          <span class="nav-icon">🚀</span>
          <span class="nav-label">BANK NIFTY 2.0</span>
        </RouterLink>
        <RouterLink to="/nifty2" class="nav-item">
          <span class="nav-icon">✨</span>
          <span class="nav-label">NIFTY 2.0</span>
        </RouterLink>
        <RouterLink to="/nifty-fut" class="nav-item">
          <span class="nav-icon">📈</span>
          <span class="nav-label">NIFTY FUTURES</span>
        </RouterLink>
        <RouterLink to="/portfolio" class="nav-item">
          <span class="nav-icon">💼</span>
          <span class="nav-label">Portfolio</span>
        </RouterLink>
        <RouterLink to="/auth" class="nav-item">
          <span class="nav-icon">🔐</span>
          <span class="nav-label">Auth &amp; Settings</span>
        </RouterLink>
      </nav>

      <!-- Global engine control -->
      <div class="sidebar-engine-ctrl">
        <div class="engine-ctrl-label">ALL ENGINES</div>
        <div class="engine-ctrl-status">
          <div class="engine-status-row">
            <div class="dot" :class="niftyRunning ? 'on' : 'off'" style="flex-shrink:0"></div>
            <span>NIFTY 50</span>
          </div>
          <div class="engine-status-row">
            <div class="dot" :class="bnRunning ? 'on' : 'off'" style="flex-shrink:0"></div>
            <span>BANK NIFTY</span>
          </div>
          <div class="engine-status-row">
            <div class="dot" :class="bn2Running ? 'on' : 'off'" style="flex-shrink:0"></div>
            <span>BANK NIFTY 2.0</span>
          </div>
          <div class="engine-status-row">
            <div class="dot" :class="n2Running ? 'on' : 'off'" style="flex-shrink:0"></div>
            <span>NIFTY 2.0</span>
          </div>
          <div class="engine-status-row">
            <div class="dot" :class="futRunning ? 'on' : 'off'" style="flex-shrink:0"></div>
            <span>NIFTY FUTURES</span>
          </div>
        </div>
        <div class="engine-ctrl-btns">
          <button class="btn g-btn engine-ctrl-btn" :disabled="globalLoading || (niftyRunning && bnRunning && bn2Running && n2Running && futRunning)" @click="startAll">
            ▶ Start All
          </button>
          <button class="btn r-btn engine-ctrl-btn" :disabled="globalLoading || (!niftyRunning && !bnRunning && !bn2Running && !n2Running && !futRunning)" @click="stopAll">
            ■ Stop All
          </button>
        </div>
        <div v-if="globalMsg" class="engine-ctrl-msg" :class="globalMsgType">{{ globalMsg }}</div>
      </div>

      <!-- Auth status indicator -->
      <div class="sidebar-auth" :class="isAuthenticated ? 'auth-connected' : 'auth-disconnected-bar'">
        <div class="sidebar-auth-dot" :class="isAuthenticated ? 'dot-green' : 'dot-red'"></div>
        <div class="sidebar-auth-text">
          <div class="sidebar-auth-name">{{ isAuthenticated ? (userName || 'Connected') : 'Not Connected' }}</div>
          <div class="sidebar-auth-sub">{{ isAuthenticated ? 'Zerodha · Kite' : 'Login required' }}</div>
        </div>
      </div>

      <div class="sidebar-footer">
        <div class="strategy-badge">VWAP + EMA Breakout v2<br>5-min candles · Options</div>
      </div>
    </aside>

    <main class="main-content">
      <RouterView />
    </main>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'

const router          = useRouter()
const authChecking    = ref(true)
const isAuthenticated = ref(false)
const userName        = ref('')

// Global engine state (polled every 5s)
const niftyRunning  = ref(false)
const bnRunning     = ref(false)
const bn2Running    = ref(false)
const n2Running     = ref(false)
const futRunning    = ref(false)
const globalLoading = ref(false)
const globalMsg     = ref('')
const globalMsgType = ref('')
let statusTimer     = null

async function checkAuth() {
  authChecking.value = true
  try {
    const r = await fetch('/profile')
    if (r.ok) {
      const d = await r.json()
      isAuthenticated.value = true
      userName.value = d.user_name || d.user_id || ''
    } else if (r.status === 401) {
      // Not authenticated — redirect to login once
      isAuthenticated.value = false
      window.location.href = '/login'
      return
    } else {
      // Server/Kite error (5xx) — show disconnected state, don't redirect (avoids redirect loop)
      isAuthenticated.value = false
    }
  } catch {
    // Network error — show disconnected state, don't redirect
    isAuthenticated.value = false
  }
  authChecking.value = false
}

async function pollEngineStatus() {
  try {
    const [nr, br, br2, n2, fut] = await Promise.all([
      fetch('/auto-trading/status'),
      fetch('/auto-trading/banknifty/status'),
      fetch('/auto-trading/banknifty2/status'),
      fetch('/auto-trading/nifty2/status'),
      fetch('/auto-trading/nifty-fut/status'),
    ])
    if (nr.ok)  { const d = await nr.json();  niftyRunning.value = d.engine_running }
    if (br.ok)  { const d = await br.json();  bnRunning.value    = d.engine_running }
    if (br2.ok) { const d = await br2.json(); bn2Running.value   = d.engine_running }
    if (n2.ok)  { const d = await n2.json();  n2Running.value    = d.engine_running }
    if (fut.ok) { const d = await fut.json(); futRunning.value   = d.engine_running }
  } catch (_) {}
}

async function startAll() {
  globalLoading.value = true
  globalMsg.value = 'Starting all engines…'
  globalMsgType.value = ''
  const startedList = []
  const errorList   = []
  try {
    // v1: NIFTY + BANKNIFTY in one call
    const r = await fetch('/auto-trading/start-all', { method: 'POST' })
    const d = await r.json()
    if (r.ok && d.results) {
      Object.entries(d.results).forEach(([k, v]) => {
        if (v.status === 'started') startedList.push(k)
      })
      Object.keys(d.errors || {}).forEach(k => errorList.push(k))
    } else if (d?.detail) {
      errorList.push('v1: ' + d.detail)
    }

    // v2: BANKNIFTY_2 separate call
    if (!bn2Running.value) {
      try {
        const r2 = await fetch('/auto-trading/banknifty2/start', { method: 'POST' })
        const d2 = await r2.json()
        if (r2.ok) startedList.push('BANKNIFTY_2')
        else errorList.push('BANKNIFTY_2: ' + (d2.detail || 'unknown'))
      } catch (e) {
        errorList.push('BANKNIFTY_2: ' + e)
      }
    }

    // v2: NIFTY_2 separate call
    if (!n2Running.value) {
      try {
        const r3 = await fetch('/auto-trading/nifty2/start', { method: 'POST' })
        const d3 = await r3.json()
        if (r3.ok) startedList.push('NIFTY_2')
        else errorList.push('NIFTY_2: ' + (d3.detail || 'unknown'))
      } catch (e) {
        errorList.push('NIFTY_2: ' + e)
      }
    }

    // Futures: NIFTY_FUT separate call
    if (!futRunning.value) {
      try {
        const r4 = await fetch('/auto-trading/nifty-fut/start', { method: 'POST' })
        const d4 = await r4.json()
        if (r4.ok) startedList.push('NIFTY_FUT')
        else errorList.push('NIFTY_FUT: ' + (d4.detail || 'unknown'))
      } catch (e) {
        errorList.push('NIFTY_FUT: ' + e)
      }
    }

    if (errorList.length) {
      globalMsg.value = `Started: ${startedList.join(', ') || 'none'} | Errors: ${errorList.join(' · ')}`
      globalMsgType.value = 'err'
    } else {
      globalMsg.value = `Started: ${startedList.join(', ')}`
      globalMsgType.value = 'ok'
    }
    await pollEngineStatus()
  } catch (e) { globalMsg.value = 'Network error: ' + e; globalMsgType.value = 'err' }
  finally { globalLoading.value = false }
}

async function stopAll() {
  globalLoading.value = true
  globalMsg.value = 'Stopping all engines…'
  globalMsgType.value = ''
  const errorList = []
  try {
    const r = await fetch('/auto-trading/stop-all', { method: 'POST' })
    const d = await r.json()
    if (!r.ok && d?.detail) errorList.push('v1: ' + d.detail)

    if (bn2Running.value) {
      try {
        const r2 = await fetch('/auto-trading/banknifty2/stop', { method: 'POST' })
        const d2 = await r2.json()
        if (!r2.ok) errorList.push('BANKNIFTY_2: ' + (d2.detail || 'unknown'))
      } catch (e) {
        errorList.push('BANKNIFTY_2: ' + e)
      }
    }

    if (n2Running.value) {
      try {
        const r3 = await fetch('/auto-trading/nifty2/stop', { method: 'POST' })
        const d3 = await r3.json()
        if (!r3.ok) errorList.push('NIFTY_2: ' + (d3.detail || 'unknown'))
      } catch (e) {
        errorList.push('NIFTY_2: ' + e)
      }
    }

    if (futRunning.value) {
      try {
        const r4 = await fetch('/auto-trading/nifty-fut/stop', { method: 'POST' })
        const d4 = await r4.json()
        if (!r4.ok) errorList.push('NIFTY_FUT: ' + (d4.detail || 'unknown'))
      } catch (e) {
        errorList.push('NIFTY_FUT: ' + e)
      }
    }

    if (errorList.length) {
      globalMsg.value = `Errors: ${errorList.join(' · ')}`
      globalMsgType.value = 'err'
    } else {
      globalMsg.value = 'All engines stopped'
      globalMsgType.value = 'ok'
    }
    await pollEngineStatus()
  } catch (e) { globalMsg.value = 'Network error: ' + e; globalMsgType.value = 'err' }
  finally { globalLoading.value = false }
}

onMounted(() => {
  checkAuth()
  pollEngineStatus()
  statusTimer = setInterval(pollEngineStatus, 5000)
})

onUnmounted(() => {
  clearInterval(statusTimer)
})
</script>
