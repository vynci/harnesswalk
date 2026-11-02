import { expect, test, type Page } from '@playwright/test'

// Phase 5 integration on the mock build: the pack picker and what it switches (prompts, the
// fixture menu, the inspectors' File Tree, the Eval node's golden set), diagnoses, the release
// gate's refusal with Release anyway, a lesson's demonstration on a domain pack, and the resume
// mode in replay.
const URL = '/?mockStepMs=10'
const FEE = 'What is the management fee for Hollowmere Growth Partners III?'

const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)

async function setView(page: Page, view: 'Simple' | 'Expert') {
  const expert = page.getByRole('switch', { name: 'Advanced view' })
  const want = view === 'Expert' ? 'true' : 'false'
  if ((await expert.getAttribute('aria-checked')) === want) return
  await expert.focus()
  await page.keyboard.press('Space')
  await expect(expert).toHaveAttribute('aria-checked', want)
}

async function pickPack(page: Page, id: string) {
  await page.getByTestId('pack-picker').click()
  await page.locator(`[data-testid=pack-option][data-pack="${id}"]`).click()
  await expect(page.getByTestId('pack-picker')).toHaveAttribute('data-pack', id)
}

test.beforeEach(async ({ page }) => {
  await page.goto(URL)
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
})

test('the pack picker starts a session on finance: its prompts, its fixtures and its turns', async ({ page }) => {
  const picker = page.getByTestId('pack-picker')
  await expect(picker).toHaveAttribute('data-pack', '_starter')
  await expect(picker).toContainText('Sample Shop')
  await picker.click()
  const options = page.getByTestId('pack-option')
  await expect(options).toHaveCount(2)
  await expect(options.nth(0)).toHaveAttribute('aria-checked', 'true')
  await expect(options.nth(1)).toContainText('Hollowmere Capital Partners')
  await expect(options.nth(1)).toContainText('Citations and the memory writer')
  await options.nth(1).click()

  await expect(picker).toHaveAttribute('data-pack', 'finance')
  // The trigger fits the whole name: nothing is cut.
  const title = picker.getByTestId('pack-picker-title')
  await expect(title).toHaveText('Hollowmere Capital Partners')
  expect(await title.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
  await expect(page.getByText('New session on Hollowmere Capital Partners.')).toBeVisible()
  await expect(page.getByTestId('chat-turn')).toHaveCount(0)
  await expect(page.getByTestId('empty-pack')).toHaveText('On Hollowmere Capital Partners: citations and the memory writer.')
  await expect(page.getByTestId('try-prompt').first()).toHaveText(FEE)

  // The fixture menu: this pack's own first, then the shared ones; the starter's are hidden.
  await page.getByTestId('fixture-select').click()
  const listbox = page.getByRole('listbox')
  await expect(listbox.getByTestId('fixture-group-pack')).toContainText('This pack')
  await expect(listbox.getByTestId('fixture-group-pack').getByRole('option', { name: 'cite_mgmt_fee_hcp01' })).toBeVisible()
  await expect(listbox.getByTestId('fixture-group-shared').getByRole('option', { name: 'plain_answer' })).toBeVisible()
  await expect(listbox.getByRole('option', { name: 'tool_call', exact: true })).toHaveCount(0)
  await page.keyboard.press('Escape')

  await page.getByTestId('try-prompt').first().click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  const turnId = await page.getByTestId('chat-turn').last().getAttribute('data-turn-id')
  const pack = await page.evaluate((id) => (globalThis as unknown as { __harnesswalkMock: { getTurn: (id: string) => { pack: string } } }).__harnesswalkMock.getTurn(id!).pack, turnId)
  expect(pack).toBe('finance')

  // New session keeps the pack.
  await page.getByTestId('session-menu').click()
  await expect(page.getByTestId('session-pack')).toHaveText('on Hollowmere Capital Partners')
  await page.getByTestId('session-popover').getByRole('button', { name: 'New session' }).click()
  await expect(picker).toHaveAttribute('data-pack', 'finance')
  await expect(page.getByTestId('try-prompt').first()).toHaveText(FEE)
})

test('the inspectors show the pack’s files as a File Tree; a skill opens in the editor, a PDF as its source', async ({ page }) => {
  await pickPack(page, 'finance')
  await setView(page, 'Expert')
  await node(page, 'procedural').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Files' }).click()
  const tree = inspector.getByRole('tree', { name: /Files of Hollowmere Capital Partners/ })
  await expect(tree.getByTestId('file-tree-folder')).toHaveText(['skills'])
  await expect(tree.locator('[data-testid=file-tree-file][data-path="system_prompt.md"]')).toBeVisible()
  // Keyboard: the folder is the one tab stop; ArrowLeft closes it, ArrowRight opens it again.
  const folder = tree.getByTestId('file-tree-folder')
  await folder.focus()
  await expect(folder).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('ArrowLeft')
  await expect(folder).toHaveAttribute('aria-expanded', 'false')
  await page.keyboard.press('ArrowRight')
  await expect(folder).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('ArrowDown')
  await expect(tree.locator('[data-testid=file-tree-file][data-path="skills/account_security.md"]')).toBeFocused()

  await tree.locator('[data-testid=file-tree-file][data-path="skills/fund_documents.md"]').click()
  const file = inspector.getByTestId('pack-file')
  await expect(file).toHaveAttribute('data-path', 'skills/fund_documents.md')
  await expect(file.getByTestId('artifact-editor')).toHaveAttribute('data-name', 'fund_documents')
  await expect(file).toContainText('add the citation marker')

  await node(page, 'knowledge').click()
  await inspector.getByRole('tab', { name: 'Files' }).click()
  await inspector.locator('[data-testid=file-tree-file][data-path="docs/glossary_of_fund_terms.pdf"]').click()
  await expect(inspector.getByTestId('pack-file-pdf-note')).toContainText('sources/glossary_of_fund_terms.md')
  await expect(inspector.getByTestId('pack-file-text')).toHaveAttribute('data-path', 'sources/glossary_of_fund_terms.md')
  await inspector.locator('[data-testid=file-tree-file][data-path="docs/fund_sheet_hcp01.md"]').click()
  await inspector.getByTestId('pack-file-reader').click()
  await expect(page.getByTestId('doc-reader')).toContainText('Hollowmere Growth Partners III')
  await page.keyboard.press('Escape')

  // Back on the starter pack the same tab lists the starter's skills.
  await pickPack(page, '_starter')
  await node(page, 'procedural').click()
  await inspector.getByRole('tab', { name: 'Files' }).click()
  await expect(inspector.locator('[data-testid=file-tree-file][data-path="skills/returns.md"]')).toBeVisible()
  await expect(inspector.locator('[data-testid=file-tree-file][data-path="skills/fund_documents.md"]')).toHaveCount(0)
})

test('Eval runs the session pack’s golden set: checks, after, a skipped mode, and a diagnosed regression', async ({ page }) => {
  await pickPack(page, 'finance')
  await node(page, 'eval').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByTestId('eval-pack')).toHaveAttribute('data-pack', 'finance')
  const golden = inspector.getByTestId('golden-set')
  await expect(golden).toContainText('Golden set of Hollowmere Capital Partners: 4 cases')
  await golden.getByRole('button', { name: /Golden set of/ }).click()
  await expect(golden.locator('[data-testid=golden-case][data-case="mem_statement_format_recall"] [data-testid=golden-after]')).toHaveText(
    'Runs after mem_statement_format_set: it reads what that case remembered, so run alone it has nothing to recall.',
  )
  await expect(golden.locator('[data-testid=golden-case][data-case="account_private"]')).toContainText('private mode only')

  await inspector.getByTestId('eval-run-button').click()
  const detail = inspector.getByTestId('eval-run-detail')
  await expect(detail).toHaveAttribute('data-status', 'done')
  await expect(inspector.getByTestId('eval-headline')).toHaveText(/Ran 4 cases, none failed/)
  await expect(detail.locator('[data-testid=eval-case][data-case="account_private"]')).toContainText('skipped, applies in private mode only, not dev')
  await detail.getByRole('button', { name: /recalled in a new session/ }).click()
  const recall = detail.getByTestId('eval-case-detail')
  await expect(recall.getByTestId('eval-case-after')).toContainText('Runs after mem_statement_format_set')
  await expect(recall.locator('[data-scorer="checks"]')).toContainText('every check holds')

  // Break the skill the cited answer needs, run again: a regression with its diagnosis.
  await setView(page, 'Expert')
  await node(page, 'procedural').click()
  await inspector.getByRole('tab', { name: 'Prompt' }).click()
  const editor = inspector.getByTestId('artifact-editor').and(page.locator('[data-name="fund_documents"]'))
  await editor.getByTestId('artifact-edit').click()
  const textarea = editor.getByTestId('artifact-textarea')
  await textarea.fill((await textarea.inputValue()).replace('add the citation marker', 'mention the source'))
  await editor.getByTestId('artifact-save').click()
  await expect(editor.getByTestId('artifact-saved')).toBeVisible()
  await setView(page, 'Simple')
  await node(page, 'eval').click()
  await inspector.getByTestId('eval-run-button').click()
  await expect(inspector.getByTestId('eval-headline')).toHaveText(/Ran 4 cases, 1 failed/)
  const fee = detail.locator('[data-testid=eval-case][data-case="fee_cited"]')
  await expect(fee).toHaveAttribute('data-regression', 'true')
  await expect(fee.getByTestId('diagnosis-chip')).toHaveAttribute('data-layer', 'prompt_or_skill')
  await fee.click()
  const diagnosis = detail.getByTestId('eval-case-detail').getByTestId('diagnosis')
  await expect(diagnosis.getByTestId('diagnosis-evidence')).toContainText('skill fund_documents changed from v1 to v2 since the last pass')
  await expect(diagnosis.getByTestId('diagnosis-suggestion')).toContainText('Compare skill fund_documents v2 with v1')

  // The release gate refuses it; the failing case replays and shows its diagnosis; Release anyway.
  await node(page, 'release').click()
  await inspector.getByTestId('release-create').click()
  const refused = inspector.getByTestId('gate-failed')
  await expect(refused).toContainText('The eval gate refused the release')
  const kase = refused.locator('[data-testid=gate-case][data-case="fee_cited"]')
  await expect(kase.getByTestId('diagnosis-chip')).toHaveAttribute('data-layer', 'prompt_or_skill')
  await expect(inspector.getByTestId('release-row').first().getByTestId('release-gate')).toHaveAttribute('data-status', 'skipped')
  await refused.getByTestId('release-force').click()
  await expect(inspector.getByTestId('release-forced-note')).toContainText('The audit log records it as release_forced')
  await expect(inspector.getByTestId('release-row').first().getByTestId('release-gate')).toHaveAttribute('data-status', 'forced')
  await expect(inspector.getByTestId('release-activations')).toContainText('Activation log chained over 2 activations')
  await expect(inspector.getByTestId('release-gate-text')).toContainText('the audit log has its release_forced record')
})

test('a turn rated bad can be diagnosed from the chat', async ({ page }) => {
  const turn = page.getByTestId('chat-turn').nth(1)
  await turn.getByTestId('feedback-bad').click()
  await page.getByTestId('feedback-note').getByRole('button', { name: 'Skip' }).click()
  await turn.getByTestId('diagnose-button').click()
  await expect(turn.getByTestId('diagnosis').getByTestId('diagnosis-chip')).toHaveAttribute('data-layer', 'unknown')
  await expect(turn.getByTestId('diagnosis-evidence')).toContainText('the turn was rated bad')
})

test('a lesson’s demonstration on a domain pack starts a session on that pack and says so', async ({ page }) => {
  await page.getByTestId('guide-button').click()
  await page.locator('[data-testid=lesson-row][data-slug="12-memory_writer"]').click()
  const chapter = page.getByTestId('guide-drawer').getByTestId('guide-chapter')
  const also = chapter.getByTestId('lesson-also')
  await expect(also).toHaveAttribute('data-pack', 'finance')
  await expect(also).toContainText('Nothing is written')
  await expect(also).toContainText('This starts a new session on Hollowmere Capital Partners, turns Memory writer off for the next turn')
  await also.getByTestId('lesson-also-button').click()
  await expect(page.getByTestId('pack-picker')).toHaveAttribute('data-pack', 'finance')
  await expect(also).toContainText('Started a new session on Hollowmere Capital Partners. Ready: press send in the chat.')
  await expect(page.getByTestId('fixture-select')).toContainText('mem_reporting_currency_set')
  await expect(page.getByTestId('composer-input')).toHaveValue('Please note that I want all my reports in euro from now on.')
  await page.getByTestId('send-button').click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  // The recorded stream has no memory writer span to skip, so read what the turn was asked to disable.
  const turnId = await page.getByTestId('chat-turn').last().getAttribute('data-turn-id')
  const asked = await page.evaluate((id) => [...(globalThis as unknown as { __harnesswalkMock: { getTurn: (id: string) => { disabled: Set<string>; pack: string } } }).__harnesswalkMock.getTurn(id!).disabled], turnId)
  expect(asked).toEqual(['memory_writer'])
  // The chapter's own Try it is on the starter pack: it switches back.
  await expect(chapter.getByTestId('lesson-try-button')).toHaveText('Disable Memory writer and try it on Sample Shop')
})

test('replay names the control that released each pause', async ({ page }) => {
  await page.getByTestId('step-toggle').click()
  await page.getByTestId('composer-input').fill('What are your support hours?')
  await page.getByTestId('send-button').click()
  const controls = page.getByTestId('step-controls')
  await expect(controls).toHaveAttribute('data-node', 'gateway')
  await controls.getByRole('button', { name: 'Next node' }).click()
  await expect(controls).toContainText('Paused at Input guardrails')
  await controls.getByRole('button', { name: 'Continue to the end' }).click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await page.getByTestId('replay-button').last().click()
  await page.getByRole('slider', { name: 'Replay position' }).focus()
  await page.keyboard.press('Home')
  const modes: string[] = []
  for (let i = 0; i < 80 && modes.length < 2; i++) {
    await page.getByTestId('replay-next').click()
    const badge = page.getByTestId('replay-resume-mode')
    if (await badge.count()) modes.push((await badge.getAttribute('data-mode'))!)
  }
  expect(modes).toEqual(['step', 'continue'])
  await expect(page.getByTestId('replay-resume-mode')).toHaveText('resumed by continue')
})
