import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const proxy = { target: 'http://127.0.0.1:8420', ws: true }

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/health': proxy,
      '/containers': proxy,
      '/images': proxy,
      '/profiles': proxy,
      '/system': proxy,
      '/experiments': proxy,
      '/topology': proxy,
    }
  },
  base: '/ui/',
})
