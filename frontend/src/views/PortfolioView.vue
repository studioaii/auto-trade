<template>
  <div class="page">

    <div class="page-header">
      <div>
        <div class="page-title">Portfolio</div>
        <div class="page-subtitle">Live account data from Zerodha Kite Connect</div>
      </div>
      <button class="btn s-btn" style="padding:6px 14px;font-size:12px" @click="loadAll">↻ Refresh All</button>
    </div>

    <!-- ── PROFILE ── -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Account Profile</div>
        <span v-if="profile" class="badge" style="background:rgba(16,185,129,0.15);color:var(--green);border:1px solid rgba(16,185,129,0.3)">Connected</span>
      </div>
      <div v-if="loadingProfile" class="empty-state" style="padding:24px">Loading…</div>
      <div v-else-if="profile" class="profile-grid">
        <div class="profile-field"><div class="lbl">User ID</div><div class="profile-val mono">{{ profile.user_id }}</div></div>
        <div class="profile-field"><div class="lbl">Name</div><div class="profile-val">{{ profile.user_name }}</div></div>
        <div class="profile-field"><div class="lbl">Email</div><div class="profile-val">{{ profile.email }}</div></div>
        <div class="profile-field"><div class="lbl">Broker</div><div class="profile-val">{{ profile.broker }}</div></div>
        <div class="profile-field"><div class="lbl">Exchanges</div><div class="profile-val">{{ profile.exchanges?.join(', ') || '—' }}</div></div>
        <div class="profile-field"><div class="lbl">Products</div><div class="profile-val">{{ profile.products?.join(', ') || '—' }}</div></div>
      </div>
      <div v-else class="empty-state" style="padding:20px">Could not load profile. <a href="/login">Login with Zerodha</a></div>
    </div>

    <!-- ── POSITIONS ── -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Open Positions</div>
        <button class="btn s-btn" style="padding:4px 10px;font-size:11px" @click="loadPositions">↻</button>
      </div>
      <div v-if="loadingPositions" class="empty-state" style="padding:20px">Loading…</div>
      <div v-else-if="positions && positions.length" class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>Symbol</th><th>Exchange</th><th>Product</th><th>Qty</th>
              <th>Avg Price</th><th>LTP</th><th>P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in positions" :key="p.tradingsymbol">
              <td style="color:var(--text-primary);font-weight:600">{{ p.tradingsymbol }}</td>
              <td>{{ p.exchange }}</td>
              <td>{{ p.product }}</td>
              <td :style="{ color: p.quantity > 0 ? 'var(--green)' : p.quantity < 0 ? 'var(--red)' : 'var(--text-muted)' }">{{ p.quantity }}</td>
              <td>₹{{ p.average_price }}</td>
              <td style="color:var(--text-primary)">₹{{ p.last_price }}</td>
              <td :style="{ color: p.pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }">
                {{ p.pnl >= 0 ? '+' : '' }}₹{{ p.pnl?.toFixed(2) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-state" style="padding:20px">No open positions.</div>
    </div>

    <!-- ── HOLDINGS ── -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Holdings</div>
        <button class="btn s-btn" style="padding:4px 10px;font-size:11px" @click="loadHoldings">↻</button>
      </div>
      <div v-if="loadingHoldings" class="empty-state" style="padding:20px">Loading…</div>
      <div v-else-if="holdings && holdings.length" class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>Symbol</th><th>Exchange</th><th>Qty</th>
              <th>Avg Cost</th><th>LTP</th><th>Current Value</th><th>P&amp;L</th><th>P&amp;L %</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="h in holdings" :key="h.tradingsymbol">
              <td style="color:var(--text-primary);font-weight:600">{{ h.tradingsymbol }}</td>
              <td>{{ h.exchange }}</td>
              <td>{{ h.quantity }}</td>
              <td>₹{{ h.average_price }}</td>
              <td style="color:var(--text-primary)">₹{{ h.last_price }}</td>
              <td>₹{{ (h.last_price * h.quantity)?.toFixed(2) }}</td>
              <td :style="{ color: h.pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }">
                {{ h.pnl >= 0 ? '+' : '' }}₹{{ h.pnl?.toFixed(2) }}
              </td>
              <td :style="{ color: h.pnl >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }">
                {{ h.day_change_percentage >= 0 ? '+' : '' }}{{ h.day_change_percentage?.toFixed(2) }}%
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-state" style="padding:20px">No holdings found.</div>
    </div>

    <!-- ── ORDERS ── -->
    <div class="card">
      <div class="card-header">
        <div class="card-title">Today's Orders</div>
        <button class="btn s-btn" style="padding:4px 10px;font-size:11px" @click="loadOrders">↻</button>
      </div>
      <div v-if="loadingOrders" class="empty-state" style="padding:20px">Loading…</div>
      <div v-else-if="orders && orders.length" class="tbl-wrap">
        <table>
          <thead>
            <tr>
              <th>Time</th><th>Symbol</th><th>Type</th><th>Transaction</th>
              <th>Qty</th><th>Price</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="o in [...orders].reverse()" :key="o.order_id">
              <td style="color:var(--text-muted)">{{ o.order_timestamp?.slice(11,19) || '—' }}</td>
              <td style="color:var(--text-primary);font-weight:600;font-size:11px">{{ o.tradingsymbol }}</td>
              <td>{{ o.order_type }}</td>
              <td>
                <span class="pill" :class="o.transaction_type === 'BUY' ? 'pill-ce' : 'pill-pe'">{{ o.transaction_type }}</span>
              </td>
              <td>{{ o.quantity }}</td>
              <td>₹{{ o.average_price || o.price || '—' }}</td>
              <td>
                <span class="pill" :class="o.status === 'COMPLETE' ? 'pill-win' : o.status === 'REJECTED' ? 'pill-loss' : 'pill-exit'">
                  {{ o.status }}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty-state" style="padding:20px">No orders today.</div>
    </div>

  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'

const profile          = ref(null)
const positions        = ref(null)
const holdings         = ref(null)
const orders           = ref(null)
const loadingProfile   = ref(false)
const loadingPositions = ref(false)
const loadingHoldings  = ref(false)
const loadingOrders    = ref(false)

async function loadProfile() {
  loadingProfile.value = true
  try {
    const r = await fetch('/profile')
    if (r.ok) profile.value = await r.json()
  } catch (_) {}
  finally { loadingProfile.value = false }
}

async function loadPositions() {
  loadingPositions.value = true
  try {
    const r = await fetch('/positions')
    if (r.ok) {
      const d = await r.json()
      positions.value = (d.net || d || []).filter(p => p.quantity !== 0)
    }
  } catch (_) {}
  finally { loadingPositions.value = false }
}

async function loadHoldings() {
  loadingHoldings.value = true
  try {
    const r = await fetch('/holdings')
    if (r.ok) holdings.value = await r.json()
  } catch (_) {}
  finally { loadingHoldings.value = false }
}

async function loadOrders() {
  loadingOrders.value = true
  try {
    const r = await fetch('/orders')
    if (r.ok) orders.value = await r.json()
  } catch (_) {}
  finally { loadingOrders.value = false }
}

function loadAll() {
  loadProfile()
  loadPositions()
  loadHoldings()
  loadOrders()
}

onMounted(() => {
  loadAll()
})
</script>
