import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    // Output directly into the Python package so it is included after pip install.
    outDir: '../src/huesync/webui',
    emptyOutDir: true,
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8420',
      '/ws': {
        target: 'ws://localhost:8420',
        ws: true,
      },
    },
  },
})
