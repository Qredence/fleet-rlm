import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const backend = 'http://localhost:8000'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    proxy: {
      '/api': {
        target: backend,
        changeOrigin: true,
        configure(proxy) {
          proxy.on('proxyRes', (response) => {
            if (response.headers['content-type']?.startsWith('text/event-stream')) {
              // Vite pipes the response as it arrives. Preserve this for upstream proxies.
              response.headers['x-accel-buffering'] = 'no'
            }
          })
        },
      },
      '/healthz': backend,
      '/readyz': backend,
    },
  },
})
