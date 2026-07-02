import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import DashboardView   from './views/DashboardView.vue'
import Nifty2View      from './views/Nifty2View.vue'
import PortfolioView   from './views/PortfolioView.vue'
import AuthView        from './views/AuthView.vue'
import './style.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/',          component: DashboardView },
    { path: '/nifty2',    component: Nifty2View    },
    { path: '/portfolio', component: PortfolioView },
    { path: '/auth',      component: AuthView      },
  ]
})

createApp(App).use(router).mount('#app')
