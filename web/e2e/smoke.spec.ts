import { expect, test, type Page } from '@playwright/test'

// Mock mode: the build serves the spec/examples streams from an in-browser mock, quickly.
const URL = '/?mockStepMs=10'

async function send(page: Page, text: string) {
  await page.getByTestId('composer-input').fill(text)
  await page.getByTestId('send-button').click()
}

const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)

test.beforeEach(async ({ page }) => {
  await page.goto(URL)
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
})

test('a turn plays through and the expected nodes end done', async ({ page }) => {
  await send(page, 'What are your support hours?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  for (const id of ['gateway', 'input_guardrails', 'context_builder', 'memory', 'loop', 'model_layer', 'output_guardrails', 'trace']) {
    await expect(node(page, id), id).toHaveAttribute('data-state', 'done')
  }
  for (const id of ['tools', 'policy', 'approval', 'audit']) await expect(node(page, id), id).toHaveAttribute('data-state', 'idle')
  await expect(page.getByTestId('badge-procedural')).toHaveAttribute('data-lit', 'true')
  await expect(page.getByTestId('badge-knowledge')).toHaveAttribute('data-lit', 'false')
  await expect(node(page, 'memory_writer')).toHaveAttribute('data-implemented', 'false')
  await expect(page.getByTestId('assistant-message').last()).toHaveText('Our support team is available 9am to 5pm Eastern, Monday to Friday.')
})

test('the approval card appears on the approval example and approving finishes the turn', async ({ page }) => {
  await send(page, 'Please cancel order A-1002.')
  const card = page.getByTestId('approval-card').last()
  await expect(card).toContainText('Run cancel_order?')
  // The Confirmation pattern: Deny first, then Approve.
  await expect(card.getByRole('button')).toHaveText(['Deny', 'Approve'])
  await expect(card.getByTestId('approval-tool')).toHaveText('cancel_order')
  await expect(card.getByTestId('approval-action-class')).toHaveText('destructive')
  await expect(card.getByTestId('approval-countdown')).toHaveText(/^1[45]:\d\d$/)
  await expect(node(page, 'approval')).toHaveAttribute('data-state', 'waiting')
  await card.getByRole('button', { name: 'Approve cancel_order' }).click()
  // Once decided the confirmation goes, and the tool line says how it went.
  await expect(page.getByTestId('approval-card')).toHaveCount(0)
  await expect(page.getByTestId('tool-line').last()).toContainText('approved by')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(node(page, 'audit')).toHaveAttribute('data-state', 'done')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Order A-1002 is cancelled')
})

test('denying an approval skips the tool and says so', async ({ page }) => {
  await send(page, 'Please cancel order A-1002.')
  const card = page.getByTestId('approval-card').last()
  await card.getByRole('button', { name: 'Deny cancel_order' }).click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(page.getByTestId('approval-card')).toHaveCount(0)
  await expect(page.getByTestId('tool-line').last()).toHaveAttribute('data-call-status', 'denied')
  await expect(page.getByTestId('tool-line').last()).toContainText('denied by')
  await expect(page.getByTestId('assistant-message').last()).toContainText('denied')
})

test('replay scrubbing changes node states', async ({ page }) => {
  await page.getByTestId('replay-button').first().click()
  const bar = page.getByTestId('replay-bar')
  await expect(bar).toBeVisible()
  await expect(node(page, 'trace')).toHaveAttribute('data-state', 'done')
  await expect(page.getByTestId('replay-position')).toContainText('turn_end')

  const thumb = page.getByRole('slider', { name: 'Replay position' })
  await thumb.focus()
  await page.keyboard.press('Home')
  await expect(page.getByTestId('replay-position')).toContainText('1/36 turn_start')
  await expect(node(page, 'gateway')).toHaveAttribute('data-state', 'idle')
  await expect(node(page, 'trace')).toHaveAttribute('data-state', 'idle')

  await page.getByTestId('replay-next').click()
  await expect(node(page, 'gateway')).toHaveAttribute('data-state', 'active')
  await page.getByTestId('replay-next').click()
  await expect(node(page, 'gateway')).toHaveAttribute('data-state', 'done')
  await page.getByTestId('replay-prev').click()
  await expect(node(page, 'gateway')).toHaveAttribute('data-state', 'active')

  await page.getByTestId('replay-exit').click()
  await expect(bar).toBeHidden()
})

test('step mode pauses at every node and Next moves one hop', async ({ page }) => {
  // Step mode sits on the hint line under the composer, not in the header.
  const step = page.getByRole('switch', { name: /Step mode/ })
  await expect(page.getByRole('banner').getByRole('switch', { name: /Step mode/ })).toHaveCount(0)
  await expect(step).toBeVisible()
  const hint = await page.locator('#composer-keys').boundingBox()
  const box = await step.boundingBox()
  expect(Math.abs(hint!.y + hint!.height / 2 - (box!.y + box!.height / 2))).toBeLessThan(8)
  expect(box!.x).toBeGreaterThan(hint!.x + hint!.width)
  await step.click()
  await send(page, 'What are your support hours?')
  const controls = page.getByTestId('step-controls')
  await expect(controls).toContainText('Gateway')
  await expect(node(page, 'gateway')).toHaveAttribute('data-state', 'waiting')
  await controls.getByRole('button', { name: 'Next node' }).click()
  await expect(controls).toContainText('Input guardrails')
  await expect(node(page, 'gateway')).toHaveAttribute('data-state', 'done')
  await controls.getByRole('button', { name: 'Continue to the end' }).click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
})

test('the step card stacks its buttons under the title in a narrow chat', async ({ page }) => {
  await page.getByTestId('step-toggle').click()
  await send(page, 'What are your support hours?')
  const controls = page.getByTestId('step-controls')
  await expect(controls).toContainText('Gateway')
  const title = controls.getByText(/^Paused at /)
  const next = controls.getByRole('button', { name: 'Next node' })
  const layout = async () => {
    const [card, t, n] = await Promise.all([controls.boundingBox(), title.boundingBox(), next.boundingBox()])
    return { width: card!.width, titleLines: Math.round(t!.height / 20), stacked: n!.y >= t!.y + t!.height }
  }
  // Narrow (the old layout wrapped "Paused at Gateway" word by word here): the buttons go under
  // the title, which stays on one line.
  await page.setViewportSize({ width: 1500, height: 800 })
  await expect.poll(async () => (await layout()).width).toBeLessThan(560)
  expect(await layout()).toMatchObject({ titleLines: 1, stacked: true })
  // Wide: title and buttons share the row.
  await page.setViewportSize({ width: 1900, height: 800 })
  await expect.poll(async () => (await layout()).width).toBeGreaterThan(560)
  expect(await layout()).toMatchObject({ titleLines: 1, stacked: false })
})

test('Stop cancels a paused turn', async ({ page }) => {
  await page.getByTestId('step-toggle').click()
  await send(page, 'What are your support hours?')
  await expect(page.getByTestId('step-controls')).toBeVisible()
  await page.getByTestId('stop-button').click()
  await expect(page.getByTestId('system-notice').last()).toContainText('cancelled')
  await expect(page.getByTestId('turn-status')).toContainText('exit: cancelled')
})

test('expert view expands children and the inspector shows lesson and code', async ({ page }) => {
  await expect(node(page, 'procedural')).toHaveCount(0)
  await page.getByRole('switch', { name: 'Expert view' }).click()
  await expect(node(page, 'procedural')).toBeVisible()
  await page.getByRole('button', { name: 'Collapse Memory' }).click()
  await expect(node(page, 'procedural')).toHaveCount(0)

  await node(page, 'policy').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Policy')
  await expect(inspector.getByTestId('inspector-lesson')).toContainText('allow, deny or needs approval')
  await inspector.getByRole('tab', { name: 'Config' }).click()
  await expect(inspector).toContainText('config/policy.yaml')
  await inspector.getByRole('tab', { name: 'Code' }).click()
  await expect(inspector.getByTestId('code-view')).toContainText('def ')
  await page.keyboard.press('Escape')
  await expect(inspector).toBeHidden()
})

test('a focused node opens its inspector with Enter', async ({ page }) => {
  await page.getByRole('group', { name: /^Loop,.*Press Enter to inspect/ }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('inspector-title')).toHaveText('Loop')
  await expect(page.getByRole('button', { name: 'Close inspector' })).toBeFocused()
})

test('the canvas controls: the legend dialog explains the colours and telemetry edges are always drawn', async ({ page }) => {
  const controls = page.getByRole('group', { name: 'Diagram controls' })
  await expect(controls.getByRole('switch', { name: 'Expert view' })).toBeVisible()
  await expect(page.getByRole('banner').getByRole('switch')).toHaveCount(0)
  await expect(node(page, 'policy').getByTestId('kind-chip')).toHaveAttribute('data-kind', 'config')

  // The legend is a Dialog: closed at first, opened by its button, Esc closes it and focus returns.
  await expect(page.getByRole('dialog')).toHaveCount(0)
  const open = controls.getByRole('button', { name: 'Legend' })
  await open.click()
  const legend = page.getByRole('dialog', { name: 'Legend' })
  await expect(legend).toBeVisible()
  for (const row of ['State', 'Node', 'Edge']) await expect(legend.getByRole('term').filter({ hasText: row })).toBeVisible()
  await expect(legend).toContainText('waiting')
  await expect(legend).toContainText('queue')
  await expect(legend).toContainText('telemetry')
  // Focus stays inside the dialog while it is open.
  for (let i = 0; i < 3; i++) {
    await page.keyboard.press('Tab')
    expect(await legend.evaluate((el) => el.contains(el.ownerDocument.activeElement))).toBe(true)
  }
  await page.keyboard.press('Escape')
  await expect(legend).toBeHidden()
  await expect(open).toBeFocused()
  // Nothing is remembered: a reload starts with the dialog closed.
  await page.reload()
  await expect(page.getByTestId('node-gateway')).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)

  // Telemetry edges are always drawn: every ops edge, no collapsed bus, and no switch for it.
  await expect(page.getByTestId('edge-eval_to_release')).toBeAttached()
  await expect(page.getByTestId('edge-loop_to_trace')).toBeAttached()
  await expect(page.getByTestId('edge-bus__execution__trace')).toHaveCount(0)
  await expect(page.getByRole('switch', { name: 'Telemetry' })).toHaveCount(0)
  // The controls sit at the top right of the canvas, clear of the status bar at the top left.
  const box = (await controls.boundingBox())!
  const canvas = (await page.locator('.react-flow').first().boundingBox())!
  expect(box.y - canvas.y).toBeLessThan(40)
  expect(canvas.x + canvas.width - (box.x + box.width)).toBeLessThan(40)
})

test('a tool call shows inline in the chat and opens to its details', async ({ page }) => {
  await send(page, 'Where is order A-1001?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  const calls = page.getByRole('group', { name: 'Tool calls' }).last()
  const line = calls.getByTestId('tool-line')
  await expect(line).toHaveAttribute('data-call-status', 'done')
  await expect(line).toContainText('Ran lookup_order')
  const trigger = calls.getByRole('button', { name: /Ran lookup_order/ })
  await expect(trigger).toHaveAttribute('aria-expanded', 'false')
  await trigger.click()
  await expect(trigger).toHaveAttribute('aria-expanded', 'true')
  // The line keeps an outcome and a duration; policy and sandbox are in its content.
  const details = calls.getByTestId('tool-details')
  await expect(details).toContainText('allow, rule dev.read.allow')
  await expect(details).toContainText('read')
})

test('an approval shows as a waiting tool line until it is decided', async ({ page }) => {
  await send(page, 'Please cancel order A-1002.')
  const line = page.getByTestId('tool-line').last()
  await expect(line).toHaveAttribute('data-call-status', 'waiting')
  await expect(line).toContainText('Waiting for approval to run cancel_order')
  // Not "pending" (not started): the line stays readable and the Confirmation below it asks.
  await expect(line).not.toHaveAttribute('data-status')
  await expect(page.getByTestId('approval-card').last()).toBeVisible()
  await page.getByTestId('approval-card').last().getByRole('button', { name: 'Approve cancel_order' }).click()
  await expect(line).toHaveAttribute('data-call-status', 'done')
  await expect(line).toContainText('approved by')
})

test('the inspector events are a trace whose lines open to the event JSON', async ({ page }) => {
  await send(page, 'Where is order A-1001?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await node(page, 'model_layer').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: /^Events/ }).click()
  const events = inspector.getByRole('list', { name: 'Node events' })
  await expect(events.getByRole('listitem').first()).toContainText('node_enter')
  // A finished turn has no running or pending lines.
  await expect(events.locator('[data-slot=trace-line][data-status]')).toHaveCount(0)
  const exit = events.getByRole('button', { name: /^node_exit #\d+/ }).first()
  await exit.click()
  await expect(events).toContainText('"finish_reason"')
})

test('the theme button and the Expert view switch, and the session menu starts a new session', async ({ page }) => {
  // No saved choice: the light OS preference wins, so the button offers dark.
  await expect(page.locator('html')).not.toHaveClass(/\bdark\b/)
  await expect(page.getByRole('button', { name: /theme/ })).toHaveCount(1)
  const toDark = page.getByRole('button', { name: 'Switch to dark theme' })
  await toDark.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Switch to dark theme')
  await toDark.click()
  await expect(page.locator('html')).toHaveClass(/\bdark\b/)
  expect(await page.evaluate(() => localStorage.getItem('harnesswalk.theme'))).toBe('"dark"')
  await page.getByRole('button', { name: 'Switch to light theme' }).click()
  await expect(page.locator('html')).not.toHaveClass(/\bdark\b/)
  await expect(page.getByRole('button', { name: 'Switch to dark theme' })).toBeVisible()

  const expert = page.getByRole('switch', { name: 'Expert view' })
  await expect(expert).toHaveAttribute('aria-checked', 'false')
  await expert.click()
  await expect(expert).toHaveAttribute('aria-checked', 'true')
  await expect(node(page, 'procedural')).toBeVisible()
  await expert.focus()
  await page.keyboard.press('Space')
  await expect(expert).toHaveAttribute('aria-checked', 'false')
  await expect(node(page, 'procedural')).toHaveCount(0)

  await expect(page.getByTestId('chat-turn').first()).toBeVisible()
  await page.getByTestId('session-menu').click()
  await page.getByTestId('session-popover').getByRole('button', { name: 'New session' }).click()
  await expect(page.getByTestId('chat-turn')).toHaveCount(0)
  await expect(page.getByTestId('try-prompt').first()).toBeVisible()
})

test('with no saved choice the theme follows prefers-color-scheme, and a saved choice wins', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await page.reload()
  await expect(page.locator('html')).toHaveClass(/\bdark\b/)
  await page.getByRole('button', { name: 'Switch to light theme' }).click()
  await expect(page.locator('html')).not.toHaveClass(/\bdark\b/)
  await page.reload()
  await expect(page.getByRole('button', { name: 'Switch to dark theme' })).toBeVisible()
  await expect(page.locator('html')).not.toHaveClass(/\bdark\b/)
})

test('the composer: a plus button for attachments later, and the arrow-up send turns into Stop', async ({ page }) => {
  const attach = page.getByTestId('attach-button')
  await expect(attach).toHaveAttribute('aria-disabled', 'true')
  await attach.hover()
  await expect(page.getByRole('tooltip')).toHaveText('Attach, coming later')
  const send = page.getByTestId('send-button')
  await expect(send).toBeDisabled()
  await page.getByTestId('composer-input').fill('What are your support hours?')
  await expect(send).toBeEnabled()
  await page.getByTestId('step-toggle').click()
  await page.getByTestId('composer-input').press('Enter')
  await expect(page.getByTestId('step-controls')).toBeVisible()
  await expect(page.getByTestId('send-button')).toHaveCount(0)
  await expect(page.getByTestId('stop-button')).toHaveAccessibleName('Stop the running turn')
  await page.getByTestId('step-controls').getByRole('button', { name: 'Continue to the end' }).click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(page.getByTestId('send-button')).toBeVisible()
  await expect(page.getByTestId('composer-input')).toHaveValue('')
})

test('an empty chat suggests prompts, and a suggestion sends', async ({ page }) => {
  await page.getByTestId('session-menu').click()
  await page.getByTestId('session-popover').getByRole('button', { name: 'New session' }).click()
  await expect(page.getByText('Walk the harness')).toBeVisible()
  await page.getByRole('group', { name: 'Try these' }).getByRole('button', { name: 'Where is order A-1001?' }).click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(page.getByTestId('try-prompt')).toHaveCount(0)
  await expect(page.getByTestId('message-actions').last().getByTestId('replay-button')).toBeVisible()
})
