import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 15000,
  use: {
    baseURL: 'http://localhost:4173',
  },
  webServer: {
    command: 'npx vite preview',
    url: 'http://localhost:4173',
    reuseExistingServer: true,
    timeout: 15000,
  },
})
