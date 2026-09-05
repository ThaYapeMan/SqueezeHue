import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  // Restrict to .spec.ts files only.  Without this, Playwright's default
  // testMatch also covers **.test.tsx which would pick up the Vitest unit
  // tests in src/__tests__/ and cause "did not expect test.describe()" errors.
  testMatch: ['**/*.spec.ts'],
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
