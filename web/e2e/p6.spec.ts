import { expect, test, type Page } from '@playwright/test'
import { closeTurnReport, openTurnReport } from './helpers'

// Phase 6 in mock mode: markdown replies, and the public site (`?mode=public`): the model picker
// (Auto first, a model out of today's budget, the Fixture select on the fake model), the key
// button disabled until accounts, the budget banner, the sandbox countdown, the limits card, a
// 429, and the private-only controls disabled with their reason.
const PUBLIC = '/?mockStepMs=10&mode=public'

async function send(page: Page, text: string) {
  await page.getByTestId('composer-input').fill(text)
  await page.getByTestId('send-button').click()
}

const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)

/** Close the model menu: the first Escape may only close a reason tooltip inside it. */
async function closeMenu(page: Page) {
  const menu = page.getByTestId('model-menu')
  await expect(async () => {
    await page.keyboard.press('Escape')
    await expect(menu).toBeHidden({ timeout: 500 })
  }).toPass()
}

type Sent = Record<string, unknown>
const sentTurns = (page: Page) => page.evaluate(() => (globalThis as unknown as { __harnesswalkMock: { turnRequests: Sent[] } }).__harnesswalkMock.turnRequests)

/** Send a message and return the POST /v1/turns body the UI sent to the in-browser mock. */
async function sendAndCapture(page: Page, text: string): Promise<Sent> {
  const before = (await sentTurns(page)).length
  await send(page, text)
  await expect.poll(async () => (await sentTurns(page)).length).toBe(before + 1)
  return (await sentTurns(page)).at(-1)!
}

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

test('dev mode shares the picker: Auto first, the fixture select beside it on the fake model', async ({ page }) => {
  await page.goto('/?mockStepMs=10')
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  const picker = page.getByTestId('model-picker')
  await expect(picker).toHaveText('Auto')
  await picker.click()
  const menu = page.getByTestId('model-menu')
  await expect(menu.getByTestId('auto-hint')).toHaveText('Currently fake-small')
  const ollama = menu.locator('[data-testid=model-option][data-model="llama3.1:8b"]')
  await expect(ollama).toHaveAttribute('data-available', 'false')
  await ollama.hover()
  await expect(page.getByTestId('model-reason')).toHaveText("This model's provider is not configured on this server.")
  await expect(menu.getByTestId('budget-line')).toHaveCount(0)
  await closeMenu(page)
  // The fixture control lives in the header, left of the pack picker, while the fake model runs.
  const select = page.getByRole('banner').getByTestId('fixture-select')
  await expect(select).toHaveCount(1)
  await expect(page.getByTestId('fixture-select')).toHaveCount(1)
  await expect(select).toContainText('by message')
  await select.click()
  await page.getByRole('option', { name: 'tool_call', exact: true }).click()
  const body = await sendAndCapture(page, 'What are your support hours?')
  expect(body.fixture).toBe('tool_call')
  expect(body).not.toHaveProperty('model')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Order A-1001 shipped on 24 October')
  // The key button is not offered in dev mode.
  await expect(page.getByTestId('byo-key-button')).toHaveCount(0)
})

test('public mode: Auto is first and preselected, says what it runs on, and names no model', async ({ page }) => {
  await openPublic(page)
  const picker = page.getByTestId('model-picker')
  await expect(picker).toHaveText('Auto')
  await expect(picker).toHaveAttribute('data-selected', 'auto')
  await expect(picker).toHaveAttribute('data-model', 'hosted-haiku')
  await picker.click()
  const menu = page.getByTestId('model-menu')
  const options = menu.getByTestId('model-option')
  await expect(options).toHaveCount(4)
  await expect(options.first()).toHaveAttribute('data-model', 'auto')
  await expect(options.first()).toHaveAttribute('aria-checked', 'true')
  await expect(menu.getByTestId('auto-hint')).toHaveText('Currently Claude Haiku 5.5')
  const sonnet = menu.locator('[data-testid=model-option][data-model="hosted-sonnet"]')
  await expect(sonnet).toHaveAttribute('data-available', 'false')
  await expect(sonnet.getByRole('menuitemradio')).toHaveAttribute('data-disabled', '')
  await expect(sonnet).toContainText(/Back at 00:00 UTC/)
  await sonnet.hover()
  await expect(page.getByTestId('model-reason')).toContainText("Today's shared Claude budget is used up. Back at 00:00 UTC")
  await expect(page.getByTestId('model-reason')).not.toContainText(/key/i)
  await expect(menu.getByTestId('budget-line')).toContainText('42% used')
  await closeMenu(page)
  // Auto on Claude: no fixture select. A turn names no model.
  await expect(page.getByTestId('fixture-select')).toHaveCount(0)
  expect(await sendAndCapture(page, 'What are your support hours?')).not.toHaveProperty('model')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(page.getByTestId('assistant-message').last()).toContainText('9am to 5pm')
  // An explicit pick is named.
  await picker.click()
  await menu.locator('[data-testid=model-option][data-model="fake-small"]').click()
  await expect(picker).toHaveText('Fake model (scripted)')
  expect((await sendAndCapture(page, 'And on weekends?')).model).toBe('fake-small')
})

test('public mode: on the fake model a Fixture select picks the scripted answer', async ({ page }) => {
  await openPublic(page)
  await page.getByTestId('model-picker').click()
  await page.locator('[data-testid=model-option][data-model="fake-small"]').click()
  const select = page.getByTestId('fixture-select')
  await expect(select).toBeVisible()
  await expect(select).toHaveText('public_demo')
  await select.click()
  const listbox = page.getByRole('listbox')
  await expect(listbox.getByTestId('fixture-group-pack').getByRole('option', { name: 'tool_call', exact: true })).toBeVisible()
  await expect(listbox.getByTestId('fixture-group-shared').getByRole('option', { name: 'public_demo', exact: true })).toBeVisible()
  // Another pack's fixtures are hidden; descriptions sit under the names.
  await expect(listbox.getByRole('option', { name: 'cite_mgmt_fee_hcp01', exact: true })).toHaveCount(0)
  await expect(listbox.getByRole('option', { name: 'tool_call', exact: true })).toContainText('One read-only tool call, allowed by policy, then an answer.')
  await listbox.getByRole('option', { name: 'tool_call', exact: true }).click()
  await expect(select).toContainText('tool_call')
  expect(await sendAndCapture(page, 'What are your support hours?')).toMatchObject({ model: 'fake-small', fixture: 'tool_call' })
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Order A-1001 shipped on 24 October')
  // Back on Auto (Claude Haiku): the select goes and the fixture stays home.
  await page.getByTestId('model-picker').click()
  await page.locator('[data-testid=model-option][data-model="auto"]').click()
  await expect(select).toHaveCount(0)
  expect(await sendAndCapture(page, 'And on weekends?')).not.toHaveProperty('fixture')
})

test('public mode: the key button is visible and disabled until accounts arrive', async ({ page }) => {
  await openPublic(page, '&budget=spent')
  const key = page.getByTestId('byo-key-button')
  await expect(key).toBeVisible()
  await expect(key).toHaveAttribute('aria-disabled', 'true')
  await key.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Bring your own key arrives with accounts. Coming soon.')
  await key.dispatchEvent('click')
  await expect(page.getByTestId('byo-key-popover')).toHaveCount(0)
  await expect(page.getByTestId('byo-key-input')).toHaveCount(0)
  // No "bring a key" wording anywhere in the picker either.
  await page.getByTestId('model-picker').click()
  await expect(page.getByTestId('model-menu')).not.toContainText(/key/i)
})

test.describe('public mode with the budget spent', () => {
  test.use({ timezoneId: 'Asia/Manila' })

  test('the Claude models say when they are back, Auto runs on the fake model, and a banner says so', async ({ page }) => {
    await openPublic(page, '&budget=spent')
    const picker = page.getByTestId('model-picker')
    await expect(picker).toHaveText('Auto')
    await expect(picker).toHaveAttribute('data-model', 'fake-small')
    const banner = page.getByTestId('budget-banner')
    await expect(banner).toHaveText("Today's shared Claude budget is used up. You're on the scripted fake model until 00:00 UTC (8:00 AM your time).")
    await picker.click()
    const menu = page.getByTestId('model-menu')
    await expect(menu.getByTestId('auto-hint')).toHaveText('Currently Fake model (scripted)')
    for (const alias of ['hosted-haiku', 'hosted-sonnet']) {
      const item = menu.locator(`[data-testid=model-option][data-model="${alias}"]`)
      await expect(item).toHaveAttribute('data-available', 'false')
      await expect(item.getByTestId('model-reason-short')).toHaveText('Back at 00:00 UTC (8:00 AM your time)')
    }
    await expect(menu.getByTestId('budget-line')).toHaveText("Today's shared Claude budget is used up. Back at 00:00 UTC (8:00 AM your time).")
    await closeMenu(page)
    // Auto resolves to the fake model, so the fixture select is there with the site's default.
    await expect(page.getByTestId('fixture-select')).toHaveText('public_demo')
    // Closing the banner holds for this tab's session, a reload included.
    await page.getByTestId('budget-banner-dismiss').click()
    await expect(banner).toHaveCount(0)
    await page.reload()
    await expect(page.getByTestId('mode-badge')).toContainText('public')
    await expect(page.getByTestId('model-picker')).toBeVisible()
    await expect(banner).toHaveCount(0)
  })
})

test('public mode: the session chip counts down and its popover holds the session, its totals and the limits', async ({ page }) => {
  await openPublic(page)
  const chip = page.getByTestId('session-menu')
  await expect(chip.getByTestId('mode-badge')).toHaveText('public · mock')
  const countdown = chip.getByTestId('session-countdown')
  await expect(countdown).toHaveText(/^(1 h 59 m|2 h 0 m)$/)
  await expect(countdown).toHaveAttribute('data-warn', 'false')
  await chip.click()
  const pop = page.getByTestId('session-popover')
  await expect(pop.getByTestId('session-ends')).toHaveText(/^This sandbox session ends in (1 h 59 m|2 h 0 m), at .+\. Everything in it is deleted then\.$/)
  await expect(pop.getByRole('button', { name: 'New session' })).toBeVisible()
  await expect(pop.getByTestId('session-copy')).toBeDisabled()
  // Session totals hold the requests left this hour; the limits card the per-turn limits.
  const totals = pop.getByTestId('session-totals')
  await expect(totals.getByTestId('total-turns')).toHaveText('0')
  await expect(totals.getByTestId('limit-remaining')).toHaveText('27 / 30')
  const card = pop.getByTestId('limits-card')
  await expect(card.getByTestId('limit-steps')).toHaveText('8')
  await expect(card.getByTestId('limit-tokens')).toHaveText('16.0k')
  await expect(pop.getByTestId('budget-card')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(pop).toBeHidden()

  // Before a turn the status bar's readout opens the sandbox's limits only.
  const readout = page.getByTestId('budget-trigger')
  await expect(readout).toHaveText(/^27 requests left$/)
  const idle = await openTurnReport(page)
  await expect(idle.getByTestId('limits-card').getByTestId('limit-remaining')).toHaveText('27 / 30')
  await expect(idle.getByTestId('budget-card')).toHaveCount(0)
  await closeTurnReport(page)

  // After a turn the readout opens the turn's report; the chip stays about the session.
  await send(page, 'What are your support hours?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(readout).toHaveText(/^\d+% budget$/)
  const report = await openTurnReport(page)
  await expect(report.getByTestId('budget-card')).toContainText('This turn')
  await expect(report.locator('[data-testid="budget-row"][data-label="Tokens"]')).toBeVisible()
  await expect(report.getByTestId('turn-exit')).toHaveText('answered')
  await expect(report.getByTestId('turn-tool-calls')).toHaveText('0')
  await expect(report.getByTestId('limits-card')).toHaveCount(0)
  await closeTurnReport(page)
  await chip.click()
  await expect(pop.getByTestId('session-copy')).toBeEnabled()
  await expect(pop.getByTestId('total-turns')).toHaveText('1')
  await expect(pop.getByTestId('limits-card')).toBeVisible()
  await expect(pop.getByTestId('budget-card')).toHaveCount(0)
})

test('dev mode: the chip shows the mode only, and its popover the session without a countdown', async ({ page }) => {
  await page.goto('/?mockStepMs=10')
  const chip = page.getByTestId('session-menu')
  await expect(chip.getByTestId('mode-badge')).toHaveText('dev · mock')
  await expect(chip.getByTestId('session-countdown')).toHaveCount(0)
  await chip.click()
  const pop = page.getByTestId('session-popover')
  await expect(pop.getByTestId('session-mode')).toContainText('Dev mode')
  await expect(pop.getByTestId('session-ends')).toHaveCount(0)
  await expect(pop.getByTestId('limits-card')).toHaveCount(0)
  await expect(pop.getByRole('button', { name: 'New session' })).toBeVisible()
  await expect(pop.getByTestId('limit-remaining')).toHaveCount(0)
})

test('public mode: under ten minutes the chip turns to the warning colour', async ({ page }) => {
  await openPublic(page, '&mockTtl=540')
  const countdown = page.getByTestId('session-countdown')
  await expect(countdown).toHaveText(/^[89] m$/)
  await expect(countdown).toHaveAttribute('data-warn', 'true')
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
  // The toast's details name the request, for a bug report against the server's logs.
  await expect(page.getByTestId('notice-detail')).toHaveText(/^Request ID req_mock\d{4}$/)
  await expect(page.getByTestId('chat-turn')).toHaveCount(0)
})

test('public mode: a Claude turn that ran out of budget mid-turn says so under the reply, and the banner and picker follow', async ({ page }) => {
  await openPublic(page, '&budget=midturn')
  await expect(page.getByTestId('budget-banner')).toHaveCount(0)
  // The reader picked Claude Haiku explicitly.
  const picker = page.getByTestId('model-picker')
  await picker.click()
  await page.locator('[data-testid=model-option][data-model="hosted-haiku"]').click()
  await expect(picker).toHaveText('Claude Haiku 5.5')
  await send(page, 'What are your support hours?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  const note = page.getByTestId('chat-turn').last().getByTestId('reply-notice')
  await expect(note).toHaveCount(1)
  await expect(note).toHaveAttribute('data-reason', 'budget_exhausted')
  await expect(note).toHaveText("Today's shared budget for Claude models is spent, so the rest of this turn ran on the fake model. It resets at 00:00 UTC.")
  // The banner appears, and the Claude pick moves to the fake model with a toast.
  await expect(page.getByTestId('budget-banner')).toContainText("Today's shared Claude budget is used up. You're on the scripted fake model until 00:00 UTC")
  await expect(page.getByText(/^Today's shared Claude budget is used up\. You're on the scripted fake model until 00:00 UTC/).first()).toBeVisible()
  await expect(picker).toHaveText('Fake model (scripted)')
  // A turn on the fake model has nothing to note.
  await send(page, 'And on weekends?')
  await expect(page.getByTestId('chat-turn')).toHaveCount(2)
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(page.getByTestId('chat-turn').last().getByTestId('reply-notice')).toHaveCount(0)
})

test('public mode: when the budget comes back the banner goes and the pick stays', async ({ page }) => {
  await openPublic(page, '&budget=spent')
  await expect(page.getByTestId('budget-banner')).toBeVisible()
  await page.getByTestId('model-picker').click()
  await page.locator('[data-testid=model-option][data-model="fake-small"]').click()
  await page.evaluate(() => {
    const mock = (globalThis as unknown as { __harnesswalkMock: { opts: { public: { budget: string } } } }).__harnesswalkMock
    mock.opts.public.budget = 'ok'
  })
  // A turn ends, so the capabilities are read again.
  await send(page, 'What are your support hours?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(page.getByTestId('budget-banner')).toHaveCount(0)
  await expect(page.getByTestId('model-picker')).toHaveText('Fake model (scripted)')
})

test('public mode: a provider marked down is disabled in the picker with its own reason', async ({ page }) => {
  await openPublic(page, '&unhealthy=1')
  await page.getByTestId('model-picker').click()
  const sonnet = page.getByTestId('model-menu').locator('[data-testid=model-option][data-model="hosted-sonnet"]')
  await expect(sonnet).toHaveAttribute('data-available', 'false')
  await expect(sonnet).toHaveAttribute('data-reason', 'provider_unhealthy')
  await expect(sonnet).toContainText('Not answering')
  await expect(sonnet).toHaveAttribute('data-reason', 'provider_unhealthy')
  await sonnet.hover()
  await expect(page.getByTestId('model-reason')).toHaveText("This model's provider is not answering right now. Try again in a minute or use another model.")
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
