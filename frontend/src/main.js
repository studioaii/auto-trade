import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import DashboardView   from './views/DashboardView.vue'
import BankNiftyView   from './views/BankNiftyView.vue'
import BankNifty2View  from './views/BankNifty2View.vue'
import Nifty2View      from './views/Nifty2View.vue'
import NiftyFutView    from './views/NiftyFutView.vue'
import PortfolioView   from './views/PortfolioView.vue'
import AuthView        from './views/AuthView.vue'
import './style.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/',           component: DashboardView  },
    { path: '/banknifty',  component: BankNiftyView  },
    { path: '/banknifty2', component: BankNifty2View },
    { path: '/nifty2',     component: Nifty2View     },
    { path: '/nifty-fut',  component: NiftyFutView   },
    { path: '/portfolio',  component: PortfolioView  },
    { path: '/auth',       component: AuthView       },
  ]
})

createApp(App).use(router).mount('#app')
