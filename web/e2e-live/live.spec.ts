import { expect, test, type Page } from '@playwright/test'

// Needs a dev-mode API with the fake provider and the conformance fixtures (see the config).

const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)

async function pickFixture(page: Page, name: string) {
  await page.getByTestId('fixture-select').click()
  await page.getByRole('option', { name, exact: true }).click()
}

async function send(page: Page, text: string) {
  await page.getByTestId('composer-input').fill(text)
  await page.getByTestId('send-button').click()
}

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  // Each test starts its own session.
  await page.getByRole('button', { name: 'Start a new session' }).click()
})

test('a plain answer walks the canonical path', async ({ page }) => {
  await pickFixture(page, 'plain_answer')
  await send(page, 'What are your support hours?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  for (const id of ['gateway', 'input_guardrails', 'context_builder', 'memory', 'loop', 'model_layer', 'output_guardrails', 'trace']) {
    await expect(node(page, id), id).toHaveAttribute('data-state', 'done')
  }
  await expect(page.getByTestId('assistant-message').last()).not.toBeEmpty()
  await expect(page.getByText('invalid events')).toHaveCount(0)
})

test('an approval pauses the turn and approving finishes it', async ({ page }) => {
  await pickFixture(page, 'approval_approve')
  await send(page, 'Please cancel order A-1002.')
  const card = page.getByTestId('approval-card').last()
  await expect(card).toContainText('Approval needed')
  await card.getByRole('button', { name: /^Approve/ }).click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(card.getByTestId('approval-decision')).toContainText('approve')
})

test('step mode, Next, then Stop', async ({ page }) => {
  await pickFixture(page, 'plain_answer')
  await page.getByTestId('step-toggle').click()
  await send(page, 'What are your support hours?')
  const controls = page.getByTestId('step-controls')
  await expect(controls).toContainText('Gateway')
  await controls.getByRole('button', { name: 'Next node' }).click()
  await expect(controls).toContainText('Input guardrails')
  await page.getByTestId('stop-button').click()
  await expect(page.getByTestId('turn-status')).toContainText('exit: cancelled')
})

test('history reloads and a past turn replays', async ({ page }) => {
  await pickFixture(page, 'tool_call')
  await send(page, 'Where is order A-1001?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await page.reload()
  await expect(page.getByTestId('chat-turn')).toHaveCount(1)
  await expect(page.getByTestId('user-message')).toHaveText('Where is order A-1001?')
  await page.getByTestId('replay-button').click()
  await page.getByRole('slider', { name: 'Replay position' }).focus()
  await page.keyboard.press('Home')
  await expect(node(page, 'gateway')).toHaveAttribute('data-state', 'idle')
  await page.keyboard.press('End')
  await expect(node(page, 'tools')).toHaveAttribute('data-state', 'done')
})

test('the inspector loads node details and the code peek', async ({ page }) => {
  await node(page, 'loop').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Loop')
  await inspector.getByRole('tab', { name: 'Code' }).click()
  await expect(inspector.getByTestId('code-view')).toContainText('def ')
})
