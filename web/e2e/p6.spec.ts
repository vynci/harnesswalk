import { expect, test, type Page } from '@playwright/test'

// Phase 6 in mock mode: markdown replies, and the public site (`?mode=public`): the model picker
// with a model out of today's budget, the bring-your-own-key field, the sandbox countdown, the
// limits card, a 429, and the private-only controls disabled with their reason.
const PUBLIC = '/?mockStepMs=10&mode=public'

async function send(page: Page, text: string) {
  await page.getByTestId('composer-input').fill(text)
  await page.getByTestId('send-button').click()
}

const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)

async function openPublic(page: Page, query = '') {
  await page.goto(PUBLIC + query)
  await expect(page.getByTestId('mode-badge')).toContainText('public')
  await expect(node(page, 'gateway')).toBeVisible()
}

test('an assistant reply renders as markdown: heading, list, table, code and a new-tab link', async ({ page }) => {
  await page.goto('/?mockStepMs=10')
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await send(page, 'Reply with a markdown table, please.')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  const reply = page.getByTestId('assistant-message').last()
  await expect(reply.getByRole('heading', { name: 'Order summary' })).toBeVisible()
  const table = reply.getByTestId('markdown-table').getByRole('table')
  await expect(table.getByRole('columnheader')).toHaveText(['Order', 'Status', 'Total'])
  await expect(table.getByRole('row')).toHaveCount(3)
  await expect(table.getByRole('row').nth(1).getByRole('cell')).toHaveText(['A-1001', 'shipped', '$42.00'])
  await expect(reply.getByRole('listitem').first()).toHaveText('Shipped on 2026-10-30')
  await expect(reply.getByTestId('markdown-code')).toHaveAttribute('data-language', 'json')
  const link = reply.getByRole('link', { name: /returns policy/ })
  await expect(link).toHaveAttribute('target', '_blank')
  await expect(link).toHaveAttribute('rel', /noopener/)
})

test('public mode: the picker lists the site models, the default preselected, one disabled with its reason', async ({ page }) => {
  await openPublic(page)
  const picker = page.getByTestId('model-picker')
  await expect(picker).toHaveText('Claude Haiku 5.5')
  await expect(picker).toHaveAttribute('data-model', 'hosted-haiku')
  await picker.click()
  const menu = page.getByTestId('model-menu')
  await expect(menu.getByTestId('model-option')).toHaveCount(3)
  const sonnet = menu.locator('[data-testid=model-option][data-model="hosted-sonnet"]')
  await expect(sonnet).toHaveAttribute('data-available', 'false')
  await expect(sonnet.getByRole('menuitemradio')).toHaveAttribute('data-disabled', '')
  await sonnet.hover()
  await expect(page.getByTestId('model-reason')).toContainText("Today's shared budget for Claude models is spent. It resets at 00:00 UTC. Bring your own key or use the fake model.")
  await expect(menu.getByTestId('budget-line')).toContainText('42% used')
  await menu.locator('[data-testid=model-option][data-model="fake-small"]').click()
  await expect(picker).toHaveText('Fake model (scripted)')
  // A turn on the picked model plays through.
  await send(page, 'What are your support hours?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(page.getByTestId('assistant-message').last()).toContainText('9am to 5pm')
})

test('public mode: a key of your own, held in the tab, unlocks the Claude models; Clear takes it back', async ({ page }) => {
  await openPublic(page, '&budget=spent')
  const picker = page.getByTestId('model-picker')
  // With the shared budget spent only the fake model is left, and it is preselected.
  await expect(picker).toHaveAttribute('data-model', 'fake-small')
  await page.getByTestId('byo-key-button').click()
  const pop = page.getByTestId('byo-key-popover')
  await expect(pop.getByTestId('byo-key-note')).toHaveText('Held in this tab only, sent with each request, never stored.')
  await pop.getByTestId('byo-key-input').fill('sk-ant-e2e-test-0000')
  await pop.getByTestId('byo-key-save').click()
  await expect(page.getByTestId('byo-key-button')).toHaveAttribute('data-set', 'true')
  // Never written to the browser's storage.
  const stored = await page.evaluate(() => {
    const doc = (globalThis as unknown as { document: { cookie: string } }).document
    return JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }) + doc.cookie
  })
  expect(stored).not.toContain('sk-ant-e2e')
  await picker.click()
  await expect(page.locator('[data-testid=model-option][data-model="hosted-sonnet"]')).toHaveAttribute('data-available', 'true')
  await page.locator('[data-testid=model-option][data-model="hosted-sonnet"]').click()
  await expect(picker).toHaveText('Claude Sonnet 5.5')
  await expect(page.getByTestId('model-menu')).toBeHidden()
  await page.getByTestId('byo-key-button').click()
  await expect(pop.getByTestId('byo-key-masked')).toHaveText('key ending 0000')
  await pop.getByTestId('byo-key-clear').click()
  await expect(page.getByTestId('byo-key-button')).toHaveAttribute('data-set', 'false')
  await page.keyboard.press('Escape')
  await expect(picker).toHaveAttribute('data-model', 'fake-small')
})

test('public mode: the header counts down the sandbox session and the limits card shows the caps', async ({ page }) => {
  await openPublic(page)
  const countdown = page.getByTestId('session-countdown')
  await expect(countdown).toContainText(/Sandbox session: (1 h 59 m|2 h 0 m) left, then everything here is deleted/)
  await page.getByTestId('budget-trigger').hover()
  const card = page.getByTestId('limits-card')
  await expect(card.getByTestId('limit-remaining')).toHaveText('27 / 30')
  await expect(card.getByTestId('limit-steps')).toHaveText('8')
  await expect(card.getByTestId('limit-tokens')).toHaveText('16.0k')
})

test('public mode: five minutes before the end a toast warns', async ({ page }) => {
  await openPublic(page, '&mockTtl=305')
  await expect(page.getByText('Sandbox session: 5 minutes left.')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('session-countdown')).toHaveAttribute('data-expires-at', /Z$/)
})

test('public mode: an expired session resets the chat; the next message starts a fresh one', async ({ page }) => {
  await openPublic(page, '&mockTtl=4')
  await send(page, 'What are your support hours?')
  await expect(page.getByTestId('chat-turn')).toHaveCount(1)
  await expect(page.getByText('The sandbox session ended and everything in it was deleted.')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('chat-turn')).toHaveCount(0)
  await send(page, 'What are your support hours?')
  await expect(page.getByTestId('chat-turn')).toHaveCount(1)
})

test('public mode: a 429 shows a toast with the retry time', async ({ page }) => {
  await openPublic(page, '&mockRemaining=0')
  await send(page, 'What are your support hours?')
  await expect(page.getByText('Too many requests from this address this hour. Try again in 12 min.')).toBeVisible()
  await expect(page.getByTestId('chat-turn')).toHaveCount(0)
})

test('public mode: private-only controls stay visible, disabled, with their reason', async ({ page }) => {
  await openPublic(page)
  const attach = page.getByTestId('attach-button')
  await expect(attach).toHaveAttribute('aria-disabled', 'true')
  await attach.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Uploads need a private workspace.')

  // Turning a node off.
  await node(page, 'input_guardrails').click()
  const inspector = page.getByTestId('inspector')
  const off = inspector.getByTestId('disable-node-off')
  await expect(off).toBeVisible()
  await off.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Turning nodes off needs a private workspace. Coming later.')

  // Editing an artifact.
  await node(page, 'policy').click()
  await inspector.getByRole('tab', { name: 'Config' }).click()
  const edit = inspector.getByTestId('artifact-edit')
  await expect(edit).toHaveAttribute('aria-disabled', 'true')
  await edit.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Editing needs a private workspace. Coming later.')

  // Eval runs: the panel and its list stay, Run is disabled.
  await node(page, 'eval').click()
  const run = inspector.getByTestId('eval-run-button')
  await expect(run).toHaveAttribute('aria-disabled', 'true')
  await run.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Eval runs need a private workspace. Coming later.')

  // Releases: the list is read-only, creating one is disabled.
  await node(page, 'release').click()
  const create = inspector.getByTestId('release-create')
  await expect(create).toHaveAttribute('aria-disabled', 'true')
  await create.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Releases and rollbacks need a private workspace. Coming later.')
})
