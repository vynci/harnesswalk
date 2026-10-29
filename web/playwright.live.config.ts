import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'

// End-to-end tests against the real harness: no mock. Playwright builds the web app (with the
// event schema validator switched on), then starts `harnesswalk serve` in dev mode with the fake
// provider on a scratch port, with its stores in a scratch directory, and the API serves the
// build at /. So the suite covers the production path: static files, same-origin cookies and
// the SSE stream straight from uvicorn, without a dev proxy in between.
//
//   pnpm test:e2e:live                    # or: make e2e
//   HARNESSWALK_E2E_PORT=8899 pnpm test:e2e:live
//
// Playwright stops the server when the run ends. Set HARNESSWALK_SCREENSHOTS=1 to also write
// the README screenshots to docs/screenshots/ (make screenshots).
const PORT = Number(process.env.HARNESSWALK_E2E_PORT ?? 8765)
const repo = fileURLToPath(new URL('..', import.meta.url))
const dist = fileURLToPath(new URL('./dist-e2e', import.meta.url))
const data = join(tmpdir(), `harnesswalk-e2e-${PORT}`)

export default defineConfig({
  testDir: './e2e-live',
  timeout: 45_000,
  expect: { timeout: 15_000 },
  // One server and one dev tenant: tests run one at a time, each in its own session.
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    colorScheme: 'light',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: [
      'pnpm build:e2e',
      `rm -rf "${data}"`,
      `cd "${repo}"`,
      `uv run harnesswalk serve --mode dev --provider fake --fixture plain_answer --host 127.0.0.1 --port ${PORT}`,
    ].join(' && '),
    url: `http://127.0.0.1:${PORT}/healthz`,
    reuseExistingServer: false,
    timeout: 180_000,
    stdout: 'ignore',
    stderr: 'pipe',
    env: {
      HARNESSWALK_ROOT: repo,
      HARNESSWALK_DATA_DIR: data,
      HARNESSWALK_WEB_DIST: dist,
      // Nothing from the caller's shell may change the harness under test. The one profile
      // only adds a tenant with a tool-call budget of two (e2e-live/profile.yaml).
      HARNESSWALK_PROFILE: 'web/e2e-live/profile.yaml',
      HARNESSWALK_DATASET: '',
      HARNESSWALK_OLLAMA_URL: '',
    },
  },
})
