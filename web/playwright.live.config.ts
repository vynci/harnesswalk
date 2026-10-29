import { defineConfig, devices } from '@playwright/test'

// Smoke tests against a running harnesswalk API in dev mode with the fake provider, for example
//   uv run harnesswalk serve --provider fake --fixture plain_answer --port 8765
//   HARNESSWALK_API_URL=http://127.0.0.1:8765 pnpm test:e2e:live
// Vite proxies /v1 to that API, so the UI talks to it same-origin.
const PORT = 5174
const api = process.env.HARNESSWALK_API_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  testDir: './e2e-live',
  timeout: 45_000,
  expect: { timeout: 15_000 },
  workers: 1,
  reporter: 'list',
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: 'retain-on-failure' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `pnpm exec vite --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    reuseExistingServer: false,
    env: { HARNESSWALK_API_URL: api },
    timeout: 60_000,
  },
})
