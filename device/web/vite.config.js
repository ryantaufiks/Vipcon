import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev server proxies; override for a local backend on other ports (VIPCON_API, VIPCON_RTC).
const API = process.env.VIPCON_API || 'http://127.0.0.1:8000'
const RTC = process.env.VIPCON_RTC || 'ws://127.0.0.1:7880'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: API, changeOrigin: true },
      '/rtc': { target: RTC, ws: true },
    },
  },
  build: {
    outDir: 'dist',
  },
})
