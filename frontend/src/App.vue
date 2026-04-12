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
        <RouterLink to="/backtest" class="nav-item">
          <span class="nav-icon">🔬</span>
          <span class="nav-label">Backtest</span>
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
        </div>
        <div class="engine-ctrl-btns">
          <button class="btn g-btn engine-ctrl-btn" :disabled="globalLoading || (niftyRunning && bnRunning)" @click="startAll">
            ▶ Start All
          </button>
          <button class="btn r-btn engine-ctrl-btn" :disabled="globalLoading || (!niftyRunning && !bnRunning)" @click="stopAll">
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
    } else {
      isAuthenticated.value = false
      window.location.href = '/login'
      return
    }
  } catch {
    isAuthenticated.value = false
    window.location.href = '/login'
    return
  }
  authChecking.value = false
}

async function pollEngineStatus() {
  try {
    const [nr, br] = await Promise.all([
      fetch('/auto-trading/status'),
      fetch('/auto-trading/banknifty/status'),
    ])
    if (nr.ok) { const d = await nr.json(); niftyRunning.value = d.engine_running }
    if (br.ok) { const d = await br.json(); bnRunning.value    = d.engine_running }
  } catch (_) {}
}

async function startAll() {
  globalLoading.value = true
  globalMsg.value = 'Starting all engines…'
  globalMsgType.value = ''
  try {
    const r = await fetch('/auto-trading/start-all', { method: 'POST' })
    const d = await r.json()
    if (!r.ok) { globalMsg.value = d.detail || 'Error starting engines'; globalMsgType.value = 'err'; return }
    const started = Object.entries(d.results)
      .filter(([, v]) => v.status === 'started')
      .map(([k]) => k)
    const errors = Object.keys(d.errors || {})
    if (errors.length) {
      globalMsg.value = `Started: ${started.join(', ') || 'none'} | Errors: ${errors.join(', ')}`
      globalMsgType.value = 'err'
    } else {
      globalMsg.value = `Both engines started (${started.join(', ')})`
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
  try {
    const r = await fetch('/auto-trading/stop-all', { method: 'POST' })
    const d = await r.json()
    if (!r.ok) { globalMsg.value = d.detail || 'Error stopping engines'; globalMsgType.value = 'err'; return }
    globalMsg.value = 'All engines stopped'
    globalMsgType.value = 'ok'
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
