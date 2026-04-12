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
          <div class="brand-sub">NIFTY Engine</div>
        </div>
      </div>

      <nav class="sidebar-nav">
        <RouterLink to="/" class="nav-item" exact-active-class="router-link-exact-active">
          <span class="nav-icon">📊</span>
          <span class="nav-label">Dashboard</span>
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
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'

const router         = useRouter()
const authChecking   = ref(true)
const isAuthenticated = ref(false)
const userName       = ref('')

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
      // Redirect to Zerodha login when no token
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

onMounted(() => {
  checkAuth()
})
</script>
