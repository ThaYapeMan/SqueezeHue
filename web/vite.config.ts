import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  // base defaults to '/' which is correct for the dev server.
  // The build script passes --base=/new/ for the temporary FastAPI mount;
  // remove that flag when the app moves to / permanently.
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
