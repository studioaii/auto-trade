import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import DashboardView from './views/DashboardView.vue'
import BacktestView  from './views/BacktestView.vue'
import PortfolioView from './views/PortfolioView.vue'
import AuthView      from './views/AuthView.vue'
import './style.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/',          component: DashboardView },
    { path: '/backtest',  component: BacktestView  },
    { path: '/portfolio', component: PortfolioView },
    { path: '/auth',      component: AuthView      },
  ]
})

createApp(App).use(router).mount('#app')
