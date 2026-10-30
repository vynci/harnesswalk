import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { node, send, turnDone } from './helpers'

// Phase 4 against the real harness (written on the p4-web branch against phase-4 CONTRACT.md,
// before the ops API existed: the integration pass runs and tunes it). The gate: edit a skill,
// run the golden suite, see the regression; create a release, roll back, see the old version.
// Skipped while the API does not offer the P4 features, so the suite stays green without them.

async function features(request: APIRequestContext) {
  const caps = (await (await request.get('/v1/capabilities')).json()) as { features: Record<string, boolean | undefined> }
  return caps.features
}

async function setView(page: Page, view: 'Simple' | 'Expert') {
  const radio = page.getByRole('radiogroup', { name: 'Diagram view' }).getByRole('radio', { name: view })
  if ((await radio.getAttribute('aria-checked')) === 'true') return
  await radio.focus()
  await page.keyboard.press('Enter')
  await expect(radio).toHaveAttribute('aria-checked', 'true')
}

test.beforeEach(async ({ page, request }) => {
  const f = await features(request)
  test.skip(!(f.evals && f.release && f.artifact_edit), 'the API does not offer evals, releases and artifact edits yet')
  await page.goto('/')
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
})

test.afterEach(async ({ page }) => {
  await expect(page.getByText(/invalid events/)).toHaveCount(0)
})

test('the P4 gate: a skill edit regresses a golden case, and a rollback brings the old version back', async ({ page, request }) => {
  test.setTimeout(120_000)
  const inspector = page.getByTestId('inspector')
  const before = ((await (await request.get('/v1/releases')).json()) as { releases?: { release_id: string; active: boolean }[] }).releases
  const base = before?.find((r) => r.active)?.release_id

  // A baseline golden run, so the next one has something to regress against.
  await node(page, 'eval').click()
  await expect(inspector.getByRole('tab', { name: 'Runs' })).toHaveAttribute('aria-selected', 'true')
  await inspector.getByTestId('eval-run-button').click()
  await expect(inspector.getByTestId('eval-run-detail')).toHaveAttribute('data-status', 'done', { timeout: 60_000 })

  // Edit the order_status skill: the tool it teaches changes name.
  await setView(page, 'Expert')
  await node(page, 'procedural').click()
  await inspector.getByRole('tab', { name: 'Prompt' }).click()
  const editor = inspector.getByTestId('artifact-editor').and(page.locator('[data-name="order_status"]'))
  await editor.getByTestId('artifact-edit').click()
  const textarea = editor.getByTestId('artifact-textarea')
  const body = await textarea.inputValue()
  expect(body).toContain('lookup_order')
  await textarea.fill(body.replaceAll('lookup_order', 'find_order'))
  await editor.getByTestId('artifact-save').click()
  await expect(editor.getByTestId('artifact-saved').locator('[data-diff="added"]').first()).toContainText('find_order')
  await expect(page.getByTestId('release-status')).toHaveText('working set (unreleased changes)')

  // Golden again: at least one case regresses, and the Eval node says so.
  await setView(page, 'Simple')
  await node(page, 'eval').click()
  await inspector.getByTestId('eval-run-button').click()
  const detail = inspector.getByTestId('eval-run-detail')
  await expect(detail).toHaveAttribute('data-status', 'done', { timeout: 60_000 })
  await expect(detail.locator('[data-testid="eval-case"][data-regression="true"]').first()).toBeVisible()
  await expect(node(page, 'eval').getByTestId('alert-eval')).toContainText('regression')
  const runs = (await (await request.get('/v1/evals/runs?limit=1')).json()) as { runs?: { regressions: number }[] } | { regressions: number }[]
  const latest = Array.isArray(runs) ? runs[0] : runs.runs?.[0]
  expect(latest?.regressions).toBeGreaterThan(0)

  // Release the edit, then roll back to the release before it.
  await node(page, 'release').click()
  await inspector.getByTestId('release-note').fill('find_order experiment')
  await inspector.getByTestId('release-create').click()
  await expect(page.getByTestId('ops-marker-text').last()).toHaveText(/^Release .+ active$/)
  await expect(inspector.getByTestId('release-commit').filter({ hasText: 'order_status' })).toBeVisible()
  expect(base).toBeTruthy()
  await inspector.locator(`[data-testid="release-row"][data-release="${base}"]`).click()
  await inspector.getByTestId('release-rollback').click()
  await inspector.getByTestId('rollback-confirm').getByRole('button', { name: 'Roll back' }).click()
  await expect(page.getByTestId('ops-marker-text').last()).toHaveText(`Rolled back: release ${base} active`)
  await expect(page.getByTestId('release-status')).toHaveText(`release ${base}`)

  // The old version is served again: the skill teaches lookup_order, and a turn still answers.
  await setView(page, 'Expert')
  await node(page, 'procedural').click()
  await inspector.getByRole('tab', { name: 'Prompt' }).click()
  await expect(editor).toContainText('lookup_order')
  await expect(editor).not.toContainText('find_order')
  await send(page, 'What are your support hours?')
  await turnDone(page)
})

test('feedback on a turn is stored and shown again after a reload', async ({ page }) => {
  await send(page, 'What are your support hours?')
  await turnDone(page)
  const turn = page.getByTestId('chat-turn').last()
  await turn.getByTestId('feedback-bad').click()
  await page.getByTestId('feedback-note').getByRole('textbox').fill('Too short.')
  await page.getByTestId('feedback-note').getByRole('button', { name: 'Send note' }).click()
  await page.reload()
  const again = page.getByTestId('chat-turn').last()
  await expect(again.getByTestId('feedback-bad')).toHaveAttribute('aria-pressed', 'true')
  await expect(again.getByTestId('feedback-note-text')).toHaveText('“Too short.”')
})
