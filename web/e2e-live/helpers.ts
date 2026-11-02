import { spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, type APIRequestContext, type Locator, type Page } from '@playwright/test'

export const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)
export const status = (page: Page) => page.getByTestId('turn-status')

export async function pickFixture(page: Page, name: string) {
  await page.getByTestId('fixture-select').click()
  await page.getByRole('option', { name, exact: true }).click()
}

export async function send(page: Page, text: string) {
  await page.getByTestId('composer-input').fill(text)
  await page.getByTestId('send-button').click()
}

export async function turnDone(page: Page) {
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
}

export async function newSession(page: Page) {
  await page.getByTestId('session-menu').click()
  await page.getByTestId('session-popover').getByRole('button', { name: 'New session' }).click()
}

/**
 * Open the session chip's popover (the header's top right) and return it: the session, its
 * totals, the sandbox's limits (public mode) and the release line (`release-status`).
 */
export async function openSessionChip(page: Page): Promise<Locator> {
  const pop = page.getByTestId('session-popover')
  if (!(await pop.isVisible())) await page.getByTestId('session-menu').click()
  await expect(pop).toBeVisible()
  return pop
}

/**
 * Close the session chip's popover, if it is open. The chip's tooltip, shown while the pointer
 * rests on it, is the top layer and takes the first Escape, so press again until it closes.
 */
export async function closeSessionChip(page: Page) {
  const pop = page.getByTestId('session-popover')
  await expect(async () => {
    if (await pop.isVisible()) await page.keyboard.press('Escape')
    await expect(pop).toBeHidden({ timeout: 500 })
  }).toPass()
}

/**
 * Open a turn's report and return its card. With no index, the status bar's readout opens the
 * report of the turn shown in a popover; with an index (1 for the session's first turn), that
 * turn's row on the chat timeline opens. Both hold the facts (turn-exit, turn-duration,
 * turn-tool-calls, turn-events, turn-tokens, turn-cost), the budget-row bars and the release.
 */
export async function openTurnReport(page: Page, n?: number): Promise<Locator> {
  if (n === undefined) {
    const pop = page.getByTestId('turn-report-popover')
    if (!(await pop.isVisible())) await page.getByTestId('budget-trigger').click()
    await expect(pop).toBeVisible()
    return pop
  }
  const row = page.locator(`[data-testid="turn-report"][data-turn="${n}"]`)
  if ((await row.getAttribute('data-open')) !== 'true') await row.getByTestId('turn-report-toggle').click()
  await expect(row).toHaveAttribute('data-open', 'true')
  const card = row.getByTestId('turn-report-card')
  await expect(card).toBeVisible()
  return card
}

/** Close the status bar's turn report popover, if it is open (Escape, past the readout's tooltip). */
export async function closeTurnReport(page: Page) {
  const pop = page.getByTestId('turn-report-popover')
  await expect(async () => {
    if (await pop.isVisible()) await page.keyboard.press('Escape')
    await expect(pop).toBeHidden({ timeout: 500 })
  }).toPass()
}

/** The last turn's events, read back from the API (rule 1: the stream is the source of truth). */
export async function lastTurnEvents(page: Page, request: APIRequestContext, base = '') {
  const turnId = await page.getByTestId('chat-turn').last().getAttribute('data-turn-id')
  const r = await request.get(`${base}/v1/turns/${turnId}/events?follow=false`)
  expect(r.ok()).toBe(true)
  const text = await r.text()
  return text
    .split('\n')
    .filter((l) => l.startsWith('data: '))
    .map((l) => JSON.parse(l.slice(6)) as { type: string; node_id?: string; status: string; summary?: Record<string, unknown> })
}

/** Press Next at every breakpoint until the turn ends. Returns the paused node ids. */
export async function stepThrough(page: Page, max = 120): Promise<string[]> {
  const controls = page.getByTestId('step-controls')
  const visited: string[] = []
  let lastSeq = -1
  for (let i = 0; i < max; i++) {
    await expect
      .poll(async () => {
        if ((await status(page).getAttribute('data-phase')) === 'done') return 'done'
        if (!(await controls.isVisible())) return 'moving'
        return Number(await controls.getAttribute('data-seq')) > lastSeq ? 'paused' : 'moving'
      })
      .not.toBe('moving')
    if ((await status(page).getAttribute('data-phase')) === 'done') return visited
    visited.push((await controls.getAttribute('data-node'))!)
    lastSeq = Number(await controls.getAttribute('data-seq'))
    await controls.getByRole('button', { name: 'Next node' }).click()
  }
  throw new Error(`the turn did not end after ${max} steps: ${visited.join(', ')}`)
}

const repo = fileURLToPath(new URL('../..', import.meta.url))
const dist = fileURLToPath(new URL('../dist-e2e', import.meta.url))

/** P6: a harness in another mode (public), on another fixture, with extra environment. */
export interface HarnessOptions {
  mode?: 'dev' | 'public'
  fixture?: string
  env?: Record<string, string>
}

/**
 * A second harness of its own, for tests that restart the server or need another config: the
 * same build and fake provider as the suite's server, on its own port and scratch data dir.
 * `kill()` is a SIGKILL to the whole process group (uv and the Python it started), like a crash;
 * `restart()` starts it again on the same data dir.
 */
export class Harness {
  readonly url: string
  private proc: ChildProcess | null = null
  private readonly data: string

  constructor(
    readonly port: number,
    private readonly profile = 'web/e2e-live/profile.yaml',
    private readonly options: HarnessOptions = {},
  ) {
    this.url = `http://127.0.0.1:${port}`
    this.data = mkdtempSync(join(tmpdir(), `harnesswalk-e2e-${port}-`))
  }

  async start() {
    this.proc = spawn(
      'uv',
      ['run', 'harnesswalk', 'serve', '--mode', this.options.mode ?? 'dev', '--provider', 'fake', '--fixture', this.options.fixture ?? 'plain_answer', '--host', '127.0.0.1', '--port', String(this.port)],
      {
        cwd: repo,
        detached: true,
        stdio: 'ignore',
        env: {
          ...process.env,
          HARNESSWALK_ROOT: repo,
          HARNESSWALK_DATA_DIR: this.data,
          HARNESSWALK_WEB_DIST: dist,
          HARNESSWALK_PROFILE: this.profile,
          HARNESSWALK_DATASET: '',
          HARNESSWALK_OLLAMA_URL: '',
          ...this.options.env,
        },
      },
    )
    for (let i = 0; i < 150; i++) {
      try {
        if ((await fetch(`${this.url}/healthz`)).ok) return
      } catch {
        // not up yet
      }
      await new Promise((r) => setTimeout(r, 200))
    }
    throw new Error(`harness on ${this.port} did not start`)
  }

  kill() {
    if (this.proc?.pid) {
      try {
        process.kill(-this.proc.pid, 'SIGKILL')
      } catch {
        // already gone
      }
    }
    this.proc = null
  }

  async restart() {
    this.kill()
    await new Promise((r) => setTimeout(r, 300))
    await this.start()
  }

  stop() {
    this.kill()
    rmSync(this.data, { recursive: true, force: true })
  }
}

/**
 * Cancel every unfinished turn the suite's tenants left behind (a test that ends on an approval
 * card, a reload mid-turn). Artifact edits and releases are refused while any turn of the
 * process runs (409 turn_running), so the P4 tests start from a quiet harness.
 */
export async function settleTurns(request: APIRequestContext, base = '', tenants = ['dev', 'e2e-memory', 'e2e-budget', 'e2e-step']) {
  for (const tenant of tenants) {
    const headers = { 'X-Harnesswalk-Tenant': tenant }
    for (const status of ['paused', 'running']) {
      const r = await request.get(`${base}/v1/trace/turns?status=${status}&limit=200`, { headers })
      if (!r.ok()) continue
      const { turns } = (await r.json()) as { turns: { turn_id: string }[] }
      for (const t of turns) await request.post(`${base}/v1/turns/${t.turn_id}/cancel`, { headers, data: {} })
    }
  }
  await expect
    .poll(async () => {
      let open = 0
      for (const tenant of tenants) {
        const r = await request.get(`${base}/v1/trace/turns?status=running&limit=200`, { headers: { 'X-Harnesswalk-Tenant': tenant } })
        if (r.ok()) open += ((await r.json()) as { turns: unknown[] }).turns.length
      }
      return open
    })
    .toBe(0)
}
