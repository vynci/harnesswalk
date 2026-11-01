import { expect, test, type Page } from '@playwright/test'

// The Payload tab (tracer.md, "Payloads"): what each hop actually carried, served offline by the
// mock from real fake-provider records of the same three turns.
const URL = '/?mockStepMs=10'

async function send(page: Page, text: string) {
  await page.getByTestId('composer-input').fill(text)
  await page.getByTestId('send-button').click()
}

const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)

test.beforeEach(async ({ page }) => {
  await page.goto(URL)
  await expect(node(page, 'gateway')).toBeVisible()
})

test('the context builder shows the assembled context as sections with token counts', async ({ page }) => {
  await send(page, 'Where is order A-1001?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await node(page, 'context_builder').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Context builder')
  // Right after Overview.
  const tabs = inspector.getByRole('tab')
  await expect(tabs.nth(0)).toHaveText('Overview')
  await expect(tabs.nth(1)).toHaveText('Payload')
  await tabs.nth(1).click()
  const sections = inspector.getByTestId('payload-section')
  await expect(sections.first()).toBeVisible()
  expect(await sections.count()).toBeGreaterThanOrEqual(4)
  await expect(sections.first().getByTestId('section-role')).toHaveText('system')
  await expect(sections.first()).toContainText('System prompt')
  await expect(sections.last()).toHaveAttribute('data-kind', 'user')
  await expect(sections.last().getByTestId('section-text')).toHaveText('Where is order A-1001?')
  await expect(sections.first().getByTestId('section-tokens')).toHaveText(/^\d+ tokens · [\d.]+%$/)
  await expect(inspector.getByTestId('payload-totals')).toContainText(/\d+ sections in \d+ messages · [\d,]+ tokens of a 16,000 budget/)
  await expect(inspector.getByTestId('payload-copy-json')).toBeVisible()
  // The system prompt runs past three lines: collapsed, then whole.
  const first = sections.first()
  const collapsed = (await first.getByTestId('section-text').textContent())!.length
  await first.getByTestId('section-expand').click()
  await expect(first.getByTestId('section-expand')).toHaveAttribute('aria-expanded', 'true')
  await expect(first.getByTestId('section-text')).toHaveAttribute('data-open', 'true')
  expect((await first.getByTestId('section-text').textContent())!.length).toBeGreaterThan(collapsed)
})

test('the model layer shows the request sections and the reply with its tool call', async ({ page }) => {
  await send(page, 'Where is order A-1001?')
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  await node(page, 'model_layer').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Payload' }).click()
  // The latest call: the answer, after the lookup_order result came back.
  await expect(inspector.getByTestId('payload-reply')).toContainText('Order A-1001 shipped on 24 October')
  const request = inspector.getByTestId('payload-request')
  await expect(request.getByTestId('payload-section').last()).toHaveAttribute('data-kind', 'tool')
  await expect(request.getByTestId('section-kind').last()).toHaveText('tool result')
  await expect(inspector.getByTestId('payload-request')).toContainText('lookup_order result')
})

test('the assembled context edge label opens the context builder payload', async ({ page }) => {
  await page.getByTestId('step-toggle').click()
  await send(page, 'Where is order A-1001?')
  const controls = page.getByTestId('step-controls')
  // Next until the walk stops at the loop: the context has just been handed over.
  for (let i = 0; i < 12; i++) {
    await expect(controls).toBeVisible()
    if ((await controls.getAttribute('data-node')) === 'loop') break
    const seq = await controls.getAttribute('data-seq')
    await controls.getByRole('button', { name: 'Next node' }).click()
    await expect(controls).not.toHaveAttribute('data-seq', seq ?? '')
  }
  await expect(controls).toHaveAttribute('data-node', 'loop')
  const label = page.getByTestId('edge-label-context_builder_to_loop')
  await expect(label).toHaveText('assembled context')
  await label.click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Context builder')
  await expect(inspector.getByRole('tab', { name: 'Payload' })).toHaveAttribute('aria-selected', 'true')
  await expect(inspector.getByTestId('payload-section').first()).toBeVisible()
  await controls.getByRole('button', { name: 'Continue to the end' }).click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
})

test('step mode opens the paused node on its payload: what is about to be sent', async ({ page }) => {
  await page.getByTestId('step-toggle').click()
  await send(page, 'Where is order A-1001?')
  const controls = page.getByTestId('step-controls')
  await expect(controls).toHaveAttribute('data-node', 'gateway')
  await node(page, 'gateway').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByRole('tab', { name: 'Payload' })).toHaveAttribute('aria-selected', 'true')
  await expect(inspector.getByTestId('payload-at')).toContainText('recorded before it ran')
  await expect(inspector.getByTestId('payload-received')).toContainText('Where is order A-1001?')
  // The inspector follows the walk and stays on the payload.
  await controls.getByRole('button', { name: 'Next node' }).click()
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Input guardrails')
  await expect(inspector.getByRole('tab', { name: 'Payload' })).toHaveAttribute('aria-selected', 'true')
  await expect(inspector.getByTestId('payload-input')).toContainText('Where is order A-1001?')
  await controls.getByRole('button', { name: 'Continue to the end' }).click()
  await expect(page.getByTestId('turn-status')).toHaveAttribute('data-phase', 'done')
  // Once the node finished, the full record: every check with its verdict.
  await node(page, 'input_guardrails').click()
  await expect(inspector.getByTestId('payload-check').first()).toBeVisible()
  await expect(inspector.getByTestId('verdict-chip').first()).toHaveText('pass')
})
