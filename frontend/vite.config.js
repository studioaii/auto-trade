import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/auto-trading': 'http://127.0.0.1:8000',
      '/backtest':     'http://127.0.0.1:8000',
      '/login':        'http://127.0.0.1:8000',
      '/logout':       'http://127.0.0.1:8000',
      '/callback':     'http://127.0.0.1:8000',
      '/profile':      'http://127.0.0.1:8000',
      '/holdings':     'http://127.0.0.1:8000',
      '/positions':    'http://127.0.0.1:8000',
      '/orders':       'http://127.0.0.1:8000',
    }
  }
})
