import { expect, test, type Page } from '@playwright/test'

// Phase 4 on the mock build: the gate (edit a skill, run golden, see the regression; create a
// release, roll back, see the old version), feedback, step-over and the trace query.
const URL = '/?mockStepMs=10'

const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)

async function send(page: Page, text: string) {
  await page.getByTestId('composer-input').fill(text)
  await page.getByTestId('send-button').click()
}

/**
 * Switch the diagram view from the keyboard: a toast from the last save may sit over the header
 * at this viewport, and a click would wait for it to go.
 */
async function setView(page: Page, view: 'Simple' | 'Expert') {
  const expert = page.getByRole('switch', { name: 'Expert view' })
  const want = view === 'Expert' ? 'true' : 'false'
  if ((await expert.getAttribute('aria-checked')) === want) return
  await expert.focus()
  await page.keyboard.press('Space')
  await expect(expert).toHaveAttribute('aria-checked', want)
}

async function openSkill(page: Page, name: string) {
  await setView(page, 'Expert')
  await node(page, 'procedural').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Prompt' }).click()
  return inspector.getByTestId('artifact-editor').and(page.locator(`[data-name="${name}"]`))
}

test.beforeEach(async ({ page }) => {
  await page.goto(URL)
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
})

test('the gate: edit a skill, run golden, see the regression; release, roll back, see the old version', async ({ page }) => {
  await expect(page.getByTestId('release-status')).toHaveText('release r1')

  // 1. Edit the order_status skill: the tool name it teaches changes.
  const editor = await openSkill(page, 'order_status')
  await expect(editor.getByTestId('artifact-version')).toContainText('v1')
  await editor.getByRole('button', { name: 'Edit Skill order_status' }).click()
  const textarea = editor.getByTestId('artifact-textarea')
  const body = await textarea.inputValue()
  expect(body).toContain('lookup_order')
  await textarea.fill(body.replaceAll('lookup_order', 'find_order'))
  // The preview shows the change before it is saved.
  await editor.getByRole('button', { name: /Review the change/ }).click()
  await expect(editor.getByTestId('diff-view').locator('[data-diff="removed"]').first()).toContainText('lookup_order')
  await editor.getByTestId('artifact-save').click()
  const saved = editor.getByTestId('artifact-saved')
  await expect(saved).toContainText('Saved as v2')
  await expect(saved.locator('[data-diff="added"]').first()).toContainText('find_order')
  await expect(editor.getByTestId('artifact-version')).toContainText('v2')
  await expect(page.getByTestId('ops-marker-text').last()).toHaveText('Skill order_status v2 saved')
  await expect(page.getByTestId('release-status')).toHaveText('working set (unreleased changes)')

  // 2. Run the golden suite from the Eval node: three cases regress against the seeded run.
  await setView(page, 'Simple')
  await node(page, 'eval').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByRole('tab', { name: 'Runs' })).toHaveAttribute('aria-selected', 'true')
  await expect(inspector.getByTestId('eval-headline')).toHaveText(/Ran 12 cases, none failed/)
  await inspector.getByTestId('eval-run-button').click()
  const detail = inspector.getByTestId('eval-run-detail')
  await expect(detail).toHaveAttribute('data-status', 'done')
  await expect(inspector.getByTestId('eval-headline')).toContainText('Ran 12 cases, 3 failed')
  await expect(inspector.getByTestId('eval-headline')).toContainText('3 regressions')
  const regressions = detail.locator('[data-testid="eval-case"][data-regression="true"]')
  await expect(regressions).toHaveCount(3)
  await expect(detail.getByTestId('eval-case').first()).toHaveAttribute('data-case', 'status_shipped')
  await expect(detail.getByTestId('eval-case').first()).toContainText('was passed')
  await expect(node(page, 'eval').getByTestId('alert-eval')).toHaveText('3 regressions')

  // A failed case opens to expected against actual, and to its turn in replay.
  await detail.getByRole('button', { name: /Order status: shipped/ }).click()
  const caseDetail = detail.getByTestId('eval-case-detail').first()
  await expect(caseDetail).toContainText('expected lookup_order')
  await expect(caseDetail).toContainText('actual none')
  await expect(caseDetail.locator('[data-scorer="tool_calls"]')).toHaveAttribute('data-status', 'error')
  // Grouped by scorer, the failing suites come first: without the tool there is no lookup in
  // the path, no tool call and no ship date in the reply.
  await detail.getByRole('radio', { name: 'By scorer' }).click()
  const suites = detail.getByTestId('eval-scorers').locator(':scope > [data-slot=trace-collapsible] > button [data-scorer]')
  await expect(suites).toHaveCount(4)
  expect(await suites.evaluateAll((els) => els.map((e) => `${e.getAttribute('data-scorer')}:${e.getAttribute('data-status') ?? 'ok'}`))).toEqual([
    'answer_match:error',
    'path:error',
    'tool_calls:error',
    'pii_leak:ok',
  ])
  await detail.getByRole('radio', { name: 'By case' }).click()
  await detail.getByRole('button', { name: /Order status: shipped/ }).click()
  await detail.getByTestId('eval-case-detail').first().getByTestId('eval-case-replay').click()
  await expect(page.getByTestId('replay-bar')).toBeVisible()
  await expect(node(page, 'tools')).toHaveAttribute('data-state', 'idle')
  await page.getByTestId('replay-exit').click()

  // 3. Release the working set from the Release node, and read its diff as a commit line.
  await node(page, 'release').click()
  await expect(inspector.getByRole('tab', { name: 'Releases' })).toHaveAttribute('aria-selected', 'true')
  await expect(inspector.getByTestId('release-dirty')).toHaveAttribute('data-dirty', 'true')
  await inspector.getByTestId('release-note').fill('find_order experiment')
  await inspector.getByTestId('release-create').click()
  await expect(inspector.getByTestId('release-row').first()).toHaveAttribute('data-release', 'r2')
  await expect(inspector.getByTestId('release-row').first()).toHaveAttribute('data-active', 'true')
  await expect(page.getByTestId('ops-marker-text').last()).toHaveText('Release r2 active')
  await expect(page.getByTestId('release-status')).toHaveText('release r2')
  const commit = inspector.getByTestId('release-commit')
  await expect(commit).toHaveText(/Skill order_status v1 → v2/)
  await commit.click()
  await expect(inspector.getByTestId('release-detail').getByTestId('diff-view').locator('[data-diff="removed"]').first()).toContainText('lookup_order')

  // 4. Roll back to r1: Cancel first leaves everything as it is, then Roll back.
  await inspector.locator('[data-testid="release-row"][data-release="r1"]').click()
  await inspector.getByTestId('release-rollback').click()
  const confirm = inspector.getByTestId('rollback-confirm')
  await expect(confirm).toContainText('Roll back to r1?')
  await expect(confirm.getByRole('button')).toHaveText(['Cancel', 'Roll back'])
  await confirm.getByRole('button', { name: 'Cancel' }).click()
  await expect(confirm).toHaveCount(0)
  await expect(page.getByTestId('release-status')).toHaveText('release r2')
  await inspector.getByTestId('release-rollback').click()
  await inspector.getByTestId('rollback-confirm').getByRole('button', { name: 'Roll back' }).click()
  await expect(page.getByTestId('ops-marker-text').last()).toHaveText('Rolled back: release r1 active')
  await expect(page.getByTestId('release-status')).toHaveText('release r1')
  await expect(inspector.locator('[data-testid="release-row"][data-release="r1"]')).toHaveAttribute('data-active', 'true')

  // The old version is back: the skill teaches lookup_order again, as a new version.
  const again = await openSkill(page, 'order_status')
  await expect(again.getByTestId('artifact-version')).toContainText('v3')
  await expect(again).toContainText('lookup_order')
  await expect(again).not.toContainText('find_order')
})

test('a chat marker restores what it saved, after a confirmation', async ({ page }) => {
  const editor = await openSkill(page, 'returns')
  await editor.getByRole('button', { name: 'Edit Skill returns' }).click()
  const textarea = editor.getByTestId('artifact-textarea')
  await textarea.fill((await textarea.inputValue()) + '\n6. Always thank the customer.\n')
  await editor.getByTestId('artifact-save').click()
  const marker = page.getByTestId('ops-marker').last()
  await expect(marker).toContainText('Skill returns v2 saved')
  await marker.getByTestId('ops-marker-restore').click()
  const confirm = page.getByTestId('ops-marker-confirm')
  await expect(confirm).toContainText('Restore Skill returns v1?')
  await confirm.getByRole('button', { name: 'Restore' }).click()
  await expect(page.getByTestId('ops-marker-text').last()).toHaveText('Skill returns v3 saved')
  await expect(editor.getByTestId('artifact-version')).toContainText('v3')
  await expect(editor).not.toContainText('Always thank the customer')
})

test('editing is blocked with a reason while a turn runs, and YAML is checked before Save', async ({ page }) => {
  await node(page, 'policy').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Config' }).click()
  const editor = inspector.getByTestId('artifact-editor')
  await editor.getByTestId('artifact-edit').click()
  await editor.getByTestId('artifact-textarea').fill('rules: [unclosed')
  await expect(editor.getByRole('alert')).toContainText('Not valid YAML')
  await expect(editor.getByTestId('artifact-save')).toBeDisabled()
  await editor.getByRole('button', { name: 'Cancel' }).click()

  // Step mode moves the inspector to each pause; open Policy again while the turn waits.
  await page.getByTestId('step-toggle').click()
  await send(page, 'What are your support hours?')
  await expect(page.getByTestId('step-controls')).toBeVisible()
  await node(page, 'policy').click()
  await inspector.getByRole('tab', { name: 'Config' }).click()
  const edit = editor.getByTestId('artifact-edit')
  await expect(edit).toHaveAttribute('aria-disabled', 'true')
  await expect(edit).toHaveAccessibleDescription(/A turn is running/)
  await page.getByTestId('step-controls').getByRole('button', { name: 'Continue to the end' }).click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(edit).not.toHaveAttribute('aria-disabled')
})

test('the model layer and context builder edit their YAML in a Config tab', async ({ page }) => {
  await node(page, 'model_layer').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Config' }).click()
  await expect(inspector.getByTestId('artifact-editor')).toHaveAttribute('data-kind', 'models')
  await node(page, 'context_builder').click()
  await inspector.getByRole('tab', { name: 'Config' }).click()
  await expect(inspector.getByTestId('artifact-editor')).toHaveAttribute('data-kind', 'context')
  await expect(inspector.getByTestId('config-view')).toContainText('budget')
})

test('Good and Bad record feedback with an optional note, and the seeded rating shows on load', async ({ page }) => {
  const first = page.getByTestId('chat-turn').first()
  const seeded = first.getByTestId('feedback')
  await expect(seeded).toHaveAttribute('data-rating', 'good')
  await expect(first.getByTestId('feedback-good')).toHaveAttribute('aria-pressed', 'true')
  await expect(first.getByTestId('feedback-bad')).toHaveAttribute('aria-pressed', 'false')
  await expect(first.getByTestId('feedback-note-text')).toHaveText('“Short and right.”')
  await expect(first.getByTestId('turn-scores')).toContainText('faithfulness 0.92')

  const second = page.getByTestId('chat-turn').nth(1)
  await second.getByTestId('feedback-bad').click()
  await expect(second.getByTestId('feedback-bad')).toHaveAttribute('aria-pressed', 'true')
  const note = page.getByTestId('feedback-note')
  await expect(note).toContainText('What went wrong?')
  await note.getByRole('textbox').fill('The date is wrong.')
  await note.getByRole('button', { name: 'Send note' }).click()
  await expect(note).toHaveCount(0)
  await expect(second.getByTestId('feedback-note-text')).toHaveText('“The date is wrong.”')
})

test('step-over skips the expert children in step mode and in replay', async ({ page }) => {
  await page.getByTestId('step-toggle').click()
  await send(page, 'What are your support hours?')
  const controls = page.getByTestId('step-controls')
  await expect(controls.getByRole('button')).toHaveText(['Continue', 'Step over', 'Next'])
  for (const label of ['Input guardrails', 'Context builder', 'Memory']) {
    await controls.getByRole('button', { name: 'Next node' }).click()
    await expect(controls).toContainText(`Paused at ${label}`)
  }
  // Next would stop at Procedural (an expert child of Memory); Step over runs on to the Loop.
  await controls.getByTestId('step-over').click()
  await expect(controls).toContainText('Paused at Loop')
  await expect(controls).toHaveAttribute('data-node', 'loop')
  await controls.getByRole('button', { name: 'Continue to the end' }).click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')

  await page.getByTestId('replay-button').last().click()
  const thumb = page.getByRole('slider', { name: 'Replay position' })
  await thumb.focus()
  await page.keyboard.press('Home')
  const position = page.getByTestId('replay-position')
  const steps: string[] = []
  for (let i = 0; i < 5; i++) {
    await page.getByTestId('replay-step-over').click()
    steps.push((await position.textContent()) ?? '')
  }
  expect(steps.map((s) => s.split(' ').slice(1).join(' '))).toEqual([
    'node_enter gateway',
    'node_enter input_guardrails',
    'node_enter context_builder',
    'node_enter memory',
    'node_enter loop',
  ])
})

test('the Trace node queries stored turns and opens a receipt per node', async ({ page }) => {
  await node(page, 'trace').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByRole('tab', { name: 'Turns' })).toHaveAttribute('aria-selected', 'true')
  const turns = inspector.getByTestId('trace-turn')
  await expect(turns).toHaveCount(3)
  await inspector.getByTestId('trace-node').click()
  await page.getByRole('option', { name: 'Tools', exact: true }).click()
  await inspector.getByTestId('trace-search').click()
  await expect(turns).toHaveCount(2)
  await turns.first().click()
  const receipt = inspector.getByTestId('trace-receipt').first()
  await expect(receipt.locator('[data-node="model_layer"]')).toContainText('$0.0000')
  await expect(receipt).toContainText('fake-small')
  await inspector.getByTestId('trace-replay').first().click()
  await expect(page.getByTestId('replay-bar')).toBeVisible()
})
