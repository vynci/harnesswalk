import { execFile } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'
import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { closeSessionChip, Harness, lastTurnEvents, node, openSessionChip, pickFixture, send, settleTurns, status, turnDone } from './helpers'

// Phase 4 against the real harness (fake provider): the gate (edit a skill, run golden, see the
// regression; the P5 eval gate refuses the release, Release anyway forces it and the audit log
// records it; edit again, roll back, see the old version served), and the ops seams:
// the tenant's ops stream (a CLI release and feedback from another tab show up live), eval case
// turns replayed from their events_url, step-over, the trace query, and a restart that keeps a
// paused turn's output schema.

const PORT = Number(process.env.HARNESSWALK_E2E_PORT ?? 8765)
const repo = fileURLToPath(new URL('../..', import.meta.url))
const run = promisify(execFile)

async function setView(page: Page, view: 'Simple' | 'Expert') {
  const expert = page.getByRole('switch', { name: 'Advanced view' })
  const want = view === 'Expert' ? 'true' : 'false'
  if ((await expert.getAttribute('aria-checked')) === want) return
  await expert.click()
  await expect(expert).toHaveAttribute('aria-checked', want)
}

async function activeRelease(request: APIRequestContext): Promise<string> {
  const body = (await (await request.get('/v1/releases')).json()) as { active_release_id: string }
  return body.active_release_id
}

/** Open the Procedural inspector's Prompt tab and return the editor of one skill. */
async function skillEditor(page: Page, name: string) {
  await setView(page, 'Expert')
  await node(page, 'procedural').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Prompt' }).click()
  return inspector.locator(`[data-testid="artifact-editor"][data-name="${name}"]`)
}

async function editSkill(page: Page, name: string, from: string, to: string) {
  const editor = await skillEditor(page, name)
  await editor.getByTestId('artifact-edit').click()
  const textarea = editor.getByTestId('artifact-textarea')
  const body = await textarea.inputValue()
  expect(body).toContain(from)
  await textarea.fill(body.replace(from, to))
  await editor.getByTestId('artifact-note').fill(`${from} to ${to}`)
  await editor.getByTestId('artifact-save').click()
  await expect(editor.getByTestId('artifact-saved').locator('[data-diff="added"]').first()).toContainText(to)
  await page.keyboard.press('Escape')
  return editor
}

async function runGolden(page: Page) {
  await node(page, 'eval').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByRole('tab', { name: 'Runs' })).toHaveAttribute('aria-selected', 'true')
  const detail = inspector.getByTestId('eval-run-detail')
  const before = (await detail.count()) ? await detail.getAttribute('data-run') : null
  await inspector.getByTestId('eval-run-button').click()
  await expect(detail).not.toHaveAttribute('data-run', before ?? '__none__')
  await expect(detail).toHaveAttribute('data-status', 'done', { timeout: 60_000 })
  return detail
}

test.beforeEach(async ({ page, request }) => {
  await settleTurns(request)
  await page.goto('/')
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
})

test.afterEach(async ({ page }) => {
  await expect(page.getByText(/invalid events/)).toHaveCount(0)
})

test('the P4 gate: a skill edit regresses a golden case; release, edit again, roll back, and the next turn uses the old version', async ({ page, request }) => {
  test.setTimeout(180_000)
  const inspector = page.getByTestId('inspector')
  const base = await activeRelease(request)
  const started = new Date(Date.now() - 1000).toISOString()

  // The content stamp the turns record for the returns skill, before any edit.
  const original = await (await skillEditor(page, 'returns')).getByTestId('artifact-stamp').textContent()
  await page.keyboard.press('Escape')

  // A baseline golden run, so the next one has something to regress against.
  await setView(page, 'Simple')
  await runGolden(page)

  // 1. Edit the returns skill: the return window changes, so the returns_window case breaks.
  await editSkill(page, 'returns', 'within 30 days of delivery', 'within 14 days of delivery')
  await expect(page.getByTestId('ops-marker-text').last()).toHaveText(/^Skill returns v\d+ saved$/)
  await expect(status(page).getByTestId('release-dirty')).toBeVisible()

  // 2. Golden again: the case regresses, on the Eval node and in the run detail.
  await setView(page, 'Simple')
  await expect(node(page, 'release').getByTestId('ops-chip-release')).toHaveText('unreleased')
  const detail = await runGolden(page)
  const kase = detail.locator('[data-testid="eval-case"][data-case="returns_window"]')
  await expect(kase).toHaveAttribute('data-regression', 'true')
  // The fixture expected the 30 day window in the skill: the turn ends in error and the case fails.
  await expect(kase).toHaveAttribute('data-status', 'failed')
  await expect(inspector.getByTestId('eval-headline')).toContainText(/Ran \d+ cases, 1 failed/)
  await expect(inspector.getByTestId('eval-regression-note')).toContainText('failed or errored in this run and passed in run_')
  await expect(node(page, 'eval').getByTestId('alert-eval')).toContainText('regression')
  const latest = ((await (await request.get('/v1/evals/runs?limit=1')).json()) as { runs: { regressions: number; regression_cases: string[] }[] }).runs[0]!
  expect(latest.regression_cases).toEqual(['returns_window'])

  // The case's turn lives in the run's scratch tenant: it replays from its events_url.
  await kase.click()
  await detail.getByTestId('eval-case-replay').click()
  await expect(page.getByTestId('replay-label')).toHaveText('Replaying Eval case returns_window on the diagram')
  await expect(page.getByTestId('replay-bar')).toBeVisible()
  await page.getByRole('button', { name: 'Leave replay and return to live' }).click()

  // 3. Release the edit: a Checkpoint marker in the chat, the commit in the release diff.
  await node(page, 'release').click()
  await expect(inspector.getByRole('tab', { name: 'Releases' })).toHaveAttribute('aria-selected', 'true')
  await inspector.getByTestId('release-note').fill('returns: 14 days')
  await inspector.getByTestId('release-create').click()
  // The P5 eval gate runs the golden set first and refuses the regressed skill: nothing released.
  const refused = inspector.getByTestId('gate-failed')
  await expect(refused).toBeVisible({ timeout: 60_000 })
  await expect(refused.locator('[data-testid="gate-case"][data-case="returns_window"]')).toBeVisible()
  expect(await activeRelease(request)).toBe(base)
  // Release anyway: dev mode only, and audited.
  await expect(refused.getByTestId('gate-force-note')).toHaveText('Release anyway skips the gate. Dev mode only, and audited: the audit log records it as release_forced.')
  await refused.getByTestId('release-force').click()
  await expect(inspector.getByTestId('release-forced-note')).toContainText('The audit log records it as release_forced')
  await expect(page.getByTestId('ops-marker-text').last()).toHaveText(/^Release r\d+ active$/)
  const created = await activeRelease(request)
  expect(created).not.toBe(base)
  await expect(inspector.getByTestId('release-commit').filter({ hasText: 'Skill returns' })).toBeVisible()
  await expect(inspector.getByTestId('release-verify')).toHaveAttribute('data-ok', 'true')
  await expect(inspector.getByTestId('release-activations')).toHaveAttribute('data-ok', 'true')
  await expect(inspector.locator(`[data-testid="release-row"][data-release="${created}"]`).getByTestId('release-gate')).toHaveAttribute('data-status', 'forced')
  const audit = (await (await request.get(`/v1/audit?since=${encodeURIComponent(started)}`)).json()) as { records: { entry: { action: string; extra?: { release_id?: string } } }[] }
  expect(audit.records.some((r) => r.entry.action === 'release_forced' && r.entry.extra?.release_id === created)).toBe(true)

  // 4. Edit again, then roll back to the release from before the first edit.
  await editSkill(page, 'returns', 'within 14 days of delivery', 'within 7 days of delivery')
  await expect(page.getByTestId('ops-marker-text').last()).toHaveText(/^Skill returns v\d+ saved$/)
  await setView(page, 'Simple')
  await node(page, 'release').click()
  await inspector.locator(`[data-testid="release-row"][data-release="${base}"]`).click()
  await inspector.getByTestId('release-rollback').click()
  await inspector.getByTestId('rollback-confirm').getByRole('button', { name: 'Roll back' }).click()
  await expect(page.getByTestId('ops-marker-text').last()).toHaveText(`Rolled back: release ${base} active`)
  await expect((await openSessionChip(page)).getByTestId('release-status')).toHaveText(`Release ${base}`)
  await closeSessionChip(page)
  await expect(status(page).getByTestId('release-dirty')).toHaveCount(0)
  expect(await activeRelease(request)).toBe(base)

  // 5. The Procedural inspector shows the old content again, with the old content stamp.
  const editor = await skillEditor(page, 'returns')
  await expect(editor).toContainText('within 30 days of delivery')
  await expect(editor).not.toContainText('7 days')
  await expect(editor.getByTestId('artifact-stamp')).toHaveText(original!)
  await page.keyboard.press('Escape')

  // 6. The next turn runs on the rolled-back release and the old window answers it.
  await pickFixture(page, 'golden_returns_window')
  await send(page, 'How long do I have to return a shipped order?')
  await turnDone(page)
  await expect(page.getByTestId('assistant-message').last()).toContainText('within 30 days of delivery')
  const events = await lastTurnEvents(page, request)
  expect((events[0]!.summary as { release: { release_id: string } }).release.release_id).toBe(base)
  expect(events[events.length - 1]!.status).toBe('done')
})

test('a release made with the CLI shows up live: a chat marker and the session chip, no reload', async ({ page, request }) => {
  const before = await activeRelease(request)
  // The session chip stays open while the CLI releases: its release line follows live.
  const pop = await openSessionChip(page)
  await expect(pop.getByTestId('release-status')).toBeVisible()
  const env = {
    ...process.env,
    HARNESSWALK_ROOT: repo,
    HARNESSWALK_DATA_DIR: join(tmpdir(), `harnesswalk-e2e-${PORT}`),
    HARNESSWALK_PROFILE: 'web/e2e-live/profile.yaml',
    HARNESSWALK_DATASET: '',
    HARNESSWALK_OLLAMA_URL: '',
  }
  const { stdout } = await run('uv', ['run', 'harnesswalk', 'release', 'create', '--note', 'from the CLI'], { cwd: repo, env })
  const made = /created (r\d+)/.exec(stdout)?.[1]
  expect(made).toBeTruthy()
  expect(made).not.toBe(before)
  // The tenant's ops stream (GET /v1/events) polls the store, so another process's release arrives.
  await expect(page.getByTestId('ops-marker-text').last()).toHaveText(`Release ${made} active`, { timeout: 15_000 })
  await expect(pop.getByTestId('release-status')).toHaveText(`Release ${made}`)
  await closeSessionChip(page)
  await expect(page.getByTestId('ops-marker').last().getByTestId('ops-marker-restore')).toBeVisible()
})

test('feedback is stored, shown after a reload, and reaches another tab live', async ({ page, context }) => {
  await send(page, 'What are your support hours?')
  await turnDone(page)
  const turn = page.getByTestId('chat-turn').last()
  await turn.getByTestId('feedback-bad').click()
  await page.getByTestId('feedback-note').getByRole('textbox').fill('Too short.')
  await page.getByTestId('feedback-note').getByRole('button', { name: 'Send note' }).click()

  // The same session in a second tab: it loads the rating from the session listing.
  const other = await context.newPage()
  await other.goto('/')
  const there = other.getByTestId('chat-turn').last()
  await expect(there.getByTestId('feedback-bad')).toHaveAttribute('aria-pressed', 'true')
  await expect(there.getByTestId('feedback-note-text')).toHaveText('“Too short.”')

  // Good from the second tab reaches the first through feedback_recorded on the ops stream.
  await there.getByTestId('feedback-good').click()
  await expect(turn.getByTestId('feedback-good')).toHaveAttribute('aria-pressed', 'true')
  await expect(turn.getByTestId('feedback-bad')).toHaveAttribute('aria-pressed', 'false')
  await other.close()

  await page.reload()
  await expect(page.getByTestId('chat-turn').last().getByTestId('feedback-good')).toHaveAttribute('aria-pressed', 'true')
})

test('step-over runs past the expert children to the next simple-view node', async ({ page, request }) => {
  await pickFixture(page, 'plain_answer')
  await page.getByRole('switch', { name: /Step mode/ }).click()
  await send(page, 'What are your support hours?')
  const controls = page.getByTestId('step-controls')
  await expect(controls).toHaveAttribute('data-node', 'gateway')
  for (const label of ['Input guardrails', 'Context builder', 'Memory']) {
    await controls.getByRole('button', { name: 'Next node' }).click()
    await expect(controls).toContainText(`Paused at ${label}`)
  }
  // Next would stop at Procedural, an expert child of Memory; Step over runs on to the Loop.
  await controls.getByTestId('step-over').click()
  await expect(controls).toHaveAttribute('data-node', 'loop')
  await controls.getByRole('button', { name: 'Continue to the end' }).click()
  await turnDone(page)
  // The stored stream agrees: no breakpoint inside Memory after the step-over, the next at Loop.
  const paused = (await lastTurnEvents(page, request)).filter((e) => e.type === 'node_paused').map((e) => e.node_id)
  const at = paused.indexOf('memory')
  expect(paused.slice(at, at + 2)).toEqual(['memory', 'loop'])
})

test('the trace query lists chat and eval case turns with receipts, and a case turn replays', async ({ page }) => {
  await send(page, 'What are your support hours?')
  await turnDone(page)
  await runGolden(page)
  await node(page, 'trace').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByRole('tab', { name: 'Turns' })).toHaveAttribute('aria-selected', 'true')
  // This session's chat turn, with its receipt per node and its model call.
  const chat = inspector.getByTestId('trace-turn')
  await expect(chat).toHaveCount(1)
  await chat.first().click()
  await expect(inspector.getByTestId('trace-receipt').locator('[data-testid="receipt-row"][data-node="model_layer"]')).toBeVisible()
  await expect(inspector.getByTestId('receipt-calls')).toContainText('fake-small')

  // The case turns of the caller's eval runs: kind eval.
  await inspector.getByTestId('trace-kind').click()
  await page.getByRole('option', { name: 'eval case turns' }).click()
  await inspector.getByTestId('trace-search').click()
  const kase = inspector.getByTestId('trace-turn').filter({ hasText: 'support_hours:' }).first()
  await expect(kase).toBeVisible()
  await kase.click()
  await inspector.getByTestId('trace-replay').click()
  await expect(page.getByTestId('replay-label')).toHaveText(/^Replaying Eval case support_hours on the diagram$/)
})

test.describe('a harness of its own', () => {
  test.describe.configure({ timeout: 180_000 })

  test('restart_keeps_schema: a turn paused on approval keeps its output schema and memory proposal across a restart', async ({ browser }) => {
    const h = new Harness(PORT + 3)
    await h.start()
    try {
      const page = await browser.newPage()
      await page.goto(h.url)
      await pickFixture(page, 'restart_keeps_schema')
      await page.getByRole('button', { name: 'Turn options (dev mode)' }).click()
      await page
        .getByTestId('output-schema-input')
        .fill('{"type": "object", "required": ["order_id", "status"], "properties": {"order_id": {"type": "string"}, "status": {"enum": ["processing", "cancelled"]}}}')
      await send(page, 'Call me Sam, and please cancel order A-1002.')
      const card = page.getByTestId('approval-card').last()
      await expect(card).toContainText('Run cancel_order?')

      await h.restart() // SIGKILL, then a second process on the same data dir

      await expect(page.getByTestId('reattached-notice')).toBeVisible({ timeout: 40_000 })
      await expect(status(page)).toHaveAttribute('data-phase', 'paused')
      await card.getByRole('button', { name: 'Approve cancel_order' }).click()
      await turnDone(page)
      // The schema the turn started with still checked the answer, and the proposal from before
      // the pause was written.
      await expect(page.getByTestId('assistant-message').last()).toContainText('"status": "cancelled"')
      const events = await lastTurnEvents(page, page.request, h.url)
      const guard = events.find((e) => e.type === 'node_exit' && e.node_id === 'output_guardrails')
      expect(guard?.summary).toMatchObject({ action: 'pass', schema: 'valid' })
      await expect(page.getByTestId('memory-line').first()).toHaveAttribute('data-written', 'true')
      await expect(page.getByTestId('checkpoint-notice')).toBeVisible()
      await page.close()
    } finally {
      h.stop()
    }
  })
})
