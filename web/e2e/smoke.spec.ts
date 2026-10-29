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
  await expect(card).toContainText('Approval needed')
  await expect(card.getByTestId('approval-tool')).toHaveText('cancel_order')
  await expect(card.getByTestId('approval-action-class')).toHaveText('destructive')
  await expect(card.getByTestId('approval-countdown')).toHaveText(/^1[45]:\d\d$/)
  await expect(node(page, 'approval')).toHaveAttribute('data-state', 'waiting')
  await card.getByRole('button', { name: 'Approve cancel_order' }).click()
  await expect(card.getByTestId('approval-decision')).toContainText('approve')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await expect(node(page, 'audit')).toHaveAttribute('data-state', 'done')
  await expect(page.getByTestId('assistant-message').last()).toContainText('Order A-1002 is cancelled')
})

test('denying an approval skips the tool and says so', async ({ page }) => {
  await send(page, 'Please cancel order A-1002.')
  const card = page.getByTestId('approval-card').last()
  await card.getByRole('button', { name: 'Deny cancel_order' }).click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
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
  await page.getByTestId('step-toggle').click()
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
  await page.getByTestId('view-expert').click()
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
  await page.locator('.react-flow__node-harness[data-id="loop"]').focus()
  await page.keyboard.press('Enter')
  await expect(page.getByTestId('inspector-title')).toHaveText('Loop')
  await expect(page.getByRole('button', { name: 'Close inspector' })).toBeFocused()
})
