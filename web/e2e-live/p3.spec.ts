import { expect, test } from '@playwright/test'
import { closeTurnReport, Harness, lastTurnEvents, node, openTurnReport, pickFixture, send, status, stepThrough, turnDone } from './helpers'

// Phase 3 against the real harness: the context plane (citations, memory write and read back,
// the review gate) and the control plane (PII redaction, budgets, the audit chain, sandbox
// lines, approvals that survive a restart). Fake provider, as everywhere in the suite.

const PORT = Number(process.env.HARNESSWALK_E2E_PORT ?? 8765)

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(node(page, 'gateway')).toBeVisible()
})

test.afterEach(async ({ page }) => {
  await expect(page.getByText(/invalid events/)).toHaveCount(0)
})

test('citations: the Trailhead question lights all five stores and its chips open the document', async ({ page, request }) => {
  await pickFixture(page, 'trailhead_return')
  await send(page, 'Can I return order A-1003, the Trailhead backpack?')
  await turnDone(page)
  for (const store of ['procedural', 'session', 'semantic', 'episodic', 'knowledge']) {
    await expect(page.getByTestId(`badge-${store}`), store).toHaveAttribute('data-lit', 'true')
  }
  const reply = page.getByTestId('assistant-message').last()
  const chips = reply.getByTestId('citation-chip')
  await expect(chips).toHaveCount(2)
  await expect(chips.first()).toHaveAttribute('data-doc', 'returns_policy')
  // The chips are turn_end.summary.citations, nothing else.
  const end = (await lastTurnEvents(page, request)).find((e) => e.type === 'turn_end')!
  const cited = end.summary!.citations as { n: number; doc: string; chunk: number }[]
  expect(cited.map((c) => c.n)).toEqual([6, 5])
  // Sources start closed (the AI Elements pattern) and open to one line per cited chunk.
  await expect(page.getByTestId('sources').getByTestId('source-line')).toHaveCount(0)
  await page.getByTestId('sources').getByRole('button', { name: /Cited 2 passages/ }).click()
  await expect(page.getByTestId('sources').getByTestId('source-line')).toHaveCount(2)
  await expect(page.getByTestId('guard-citations')).toContainText('citations 2')

  await chips.first().click()
  const reader = page.getByTestId('doc-reader')
  await expect(reader).toContainText('Returns policy')
  await expect(reader.locator('[data-cited="true"]')).toHaveAttribute('data-position', String(cited[0]!.chunk))
  await expect(reader.locator('[data-cited="true"]')).toContainText('30 days')
  await page.keyboard.press('Escape')
  await expect(reader).toBeHidden()

  // The context builder's budget per slice, retrieval stores included.
  await node(page, 'context_builder').click()
  const inspector = page.getByTestId('inspector')
  const budget = inspector.getByTestId('context-budget')
  for (const slice of ['semantic', 'episodic', 'knowledge']) {
    await expect(budget.locator(`[data-slice="${slice}"]`)).toBeVisible()
  }
  expect(Number(await budget.locator('[data-slice="knowledge"]').getAttribute('data-tokens'))).toBeGreaterThan(0)
  await inspector.getByRole('tab', { name: 'Context' }).click()
  await expect(inspector.getByTestId('context-citations').locator('li')).toHaveCount(6)
})

test('the knowledge inspector lists the pack documents and opens one', async ({ page }) => {
  await page.getByRole('switch', { name: 'Expert view' }).setChecked(true)
  await node(page, 'knowledge').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Records' }).click()
  await expect(inspector.getByTestId('doc-row')).toHaveCount(3)
  await inspector.locator('[data-doc="shipping_faq"]').getByRole('button', { name: /Open/ }).click()
  await expect(page.getByTestId('doc-reader')).toContainText('Shipping FAQ')
  await expect(page.getByTestId('doc-scope')).toHaveText('pack')
  await page.keyboard.press('Escape')
  await page.keyboard.press('Escape')
  // Semantic memory: the pack's seed facts carry the pack scope tag.
  await node(page, 'semantic').click()
  await inspector.getByRole('tab', { name: 'Records' }).click()
  await expect(inspector.getByTestId('scope-tag').first()).toHaveText('pack')
})

// Facts belong to the tenant, not the session: each memory test gets a tenant of its own, so a
// fact one test wrote is not a duplicate in the next.
test.describe('memory, as its own tenant', () => {
  test.use({ extraHTTPHeaders: { 'X-Harnesswalk-Tenant': 'e2e-memory' } })

  test('memory: a remembered fact is written after the reply and read back next turn', async ({ page, request }) => {
    await pickFixture(page, 'remember_fact')
    await send(page, 'Please remember that my preferred name is Sam.')
    await turnDone(page)
    await expect(page.getByTestId('memory-line')).toHaveAttribute('data-written', 'true')
    await expect(page.getByTestId('memory-line')).toContainText('preferred name: Sam')
    // The point events landed on the writer node while it ran.
    await expect(node(page, 'memory_writer')).toHaveAttribute('data-points', /memory_proposed:2 memory_written:2/)
    await expect(page.getByTestId('stat-memory_writer')).toContainText('2 written')
    await node(page, 'memory_writer').click()
    const inspector = page.getByTestId('inspector')
    await inspector.getByRole('tab', { name: 'Writes' }).click()
    const fact = inspector.locator('[data-testid="memory-write"][data-store="semantic"]').first()
    await expect(fact).toHaveAttribute('data-status', 'accepted')
    await expect(fact.getByTestId('memory-diff').locator('[data-op="add"]')).toContainText('Sam')
    await page.keyboard.press('Escape')

    await send(page, 'What is my preferred name?')
    await expect(page.getByTestId('chat-turn')).toHaveCount(2)
    await turnDone(page)
    await expect(page.getByTestId('assistant-message').last()).toContainText('Sam')
    const semantic = (await lastTurnEvents(page, request)).find((e) => e.type === 'node_exit' && e.node_id === 'semantic')!
    expect(semantic.summary!.hits).toBeGreaterThanOrEqual(1)
    await expect(page.getByTestId('badge-semantic')).toHaveAttribute('data-lit', 'true')
  })

  test('a fact the tenant already holds is not written twice', async ({ page }) => {
    await pickFixture(page, 'remember_fact')
    await send(page, 'Please remember that my preferred name is Sam.')
    await turnDone(page)
    await expect(page.getByTestId('memory-line')).toHaveAttribute('data-rejected', 'duplicate')
    await expect(page.getByTestId('memory-line')).toContainText('Not remembered in semantic')
  })
})

test('pii: the model only sees a placeholder, and dev mode can reveal the restored reply', async ({ page }) => {
  await pickFixture(page, 'pii_redacted')
  await send(page, 'Please email the receipt for order A-1001 to ana.lopez@example.com.')
  await turnDone(page)
  await expect(page.getByTestId('guard-redaction')).toHaveAttribute('data-kind', 'email')
  await expect(page.getByTestId('guard-injection')).toContainText('injection 0.00')
  await expect(page.getByTestId('model-saw')).toContainText('[EMAIL_1]')
  await expect(page.getByTestId('model-saw')).not.toContainText('ana.lopez')
  const reply = page.getByTestId('assistant-message').last()
  await expect(reply).toHaveAttribute('data-masked', 'true')
  await expect(reply.getByTestId('placeholder')).toHaveText('[EMAIL_1]')
  await expect(page.getByTestId('guard-restored')).toContainText('restored 1')
  await page.getByTestId('reveal-toggle').getByRole('switch').click()
  await expect(reply).toContainText('ana.lopez@example.com')
  await expect(reply).toHaveAttribute('data-masked', 'false')
  await expect(page.getByTestId('stat-input_guardrails')).toContainText('redact')
})

test.describe('a tenant with a tool-call budget of two per turn', () => {
  test.use({ extraHTTPHeaders: { 'X-Harnesswalk-Tenant': 'e2e-budget' } })

  test('the third call is denied on the budget and the turn ends on a limit', async ({ page }) => {
    await pickFixture(page, 'three_lookups')
    await send(page, 'Where are orders A-1001, A-1002 and A-1003?')
    await turnDone(page)
    await expect(status(page)).toContainText('exit: limit')
    const notice = page.getByTestId('system-notice').last()
    await expect(notice).toHaveAttribute('data-reason', 'budget_tool_calls')
    await expect(notice).toContainText('tool-call budget')
    await expect(notice).toContainText('2 of 2')
    const lines = page.getByTestId('tool-line')
    await expect(lines).toHaveCount(3)
    await expect(lines.nth(2)).toHaveAttribute('data-call-status', 'denied')
    await expect(lines.nth(2)).toContainText('tool calls per turn 2/2')
    await lines.nth(0).click()
    await expect(page.getByTestId('tool-trace').getByTestId('sandbox-chip').first()).toHaveAttribute('data-level', 'subprocess')
    const budgetRow = (await openTurnReport(page)).locator('[data-testid="budget-row"][data-label="tool calls per turn"]')
    await expect(budgetRow).toContainText('2 / 2')
    await closeTurnReport(page)
    await expect(page.getByTestId('stat-policy')).toContainText('budget 2/2')
    await expect(page.getByTestId('stat-policy').locator('[data-why]')).toHaveAttribute('title', /budget is 2 per turn/)
  })
})

test('audit: an approved call is recorded in the hash chain, and Verify checks it', async ({ page }) => {
  await pickFixture(page, 'approval_approve')
  await send(page, 'Please cancel order A-1002.')
  await page.getByTestId('approval-card').last().getByRole('button', { name: 'Approve cancel_order' }).click()
  await turnDone(page)
  await node(page, 'audit').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: 'Records' }).click()
  const records = inspector.getByTestId('audit-record')
  expect(await records.count()).toBeGreaterThanOrEqual(3)
  await expect(inspector.locator('[data-testid="audit-record"][data-linked="false"]')).toHaveCount(0)
  await expect(inspector.getByTestId('audit-export')).toHaveAttribute('href', '/v1/audit')
  await inspector.getByTestId('audit-verify-button').click()
  await expect(inspector.getByTestId('audit-verify')).toHaveAttribute('data-ok', 'true')
  await expect(inspector.getByText('verified the whole chain on the server just now')).toBeVisible()
})

test.describe('step mode, as its own tenant', () => {
  test.use({ extraHTTPHeaders: { 'X-Harnesswalk-Tenant': 'e2e-step' } })
  test('step mode pauses at the retrieval stores and at the memory writer', async ({ page }) => {
    test.setTimeout(120_000)
    await pickFixture(page, 'remember_fact')
    await page.getByRole('switch', { name: /Step mode/ }).click()
    await send(page, 'Please remember that my preferred name is Sam.')
    const visited = await stepThrough(page)
    for (const id of ['semantic', 'episodic', 'knowledge', 'memory_writer']) expect(visited, id).toContain(id)
    // The writer runs after the reply, so it pauses after the reply's gateway span.
    expect(visited.lastIndexOf('memory_writer')).toBeGreaterThan(visited.lastIndexOf('gateway'))
    await expect(page.getByTestId('memory-line')).toHaveAttribute('data-written', 'true')
  })
})

test('dev turn options: an output schema the answer breaks gets the turn blocked', async ({ page }) => {
  await pickFixture(page, 'plain_answer')
  await page.getByRole('button', { name: 'Turn options (dev mode)' }).click()
  await page.getByTestId('output-schema-input').fill('{"type": "object", "required": ["answer"]}')
  await send(page, 'What are your support hours?')
  await turnDone(page)
  await expect(page.getByTestId('guard-output-action')).toContainText('schema')
  await expect(page.getByTestId('stat-output_guardrails')).toContainText('block')
})

test.describe('a harness of its own', () => {
  test.describe.configure({ timeout: 180_000 })

  test('restart: a turn waiting on approval comes back paused, a running one closes restarted', async ({ browser }) => {
    const h = new Harness(PORT + 1)
    await h.start()
    try {
      const approvalPage = await browser.newPage()
      await approvalPage.goto(h.url)
      await pickFixture(approvalPage, 'approval_resume_after_restart')
      await send(approvalPage, 'Please cancel order A-1002.')
      const card = approvalPage.getByTestId('approval-card').last()
      await expect(card).toContainText('Run cancel_order?')

      const steppedPage = await browser.newPage()
      await steppedPage.goto(h.url)
      await pickFixture(steppedPage, 'plain_answer')
      await steppedPage.getByRole('switch', { name: /Step mode/ }).click()
      await send(steppedPage, 'What are your support hours?')
      await expect(steppedPage.getByTestId('step-controls')).toHaveAttribute('data-node', 'gateway')

      await h.restart() // SIGKILL, then a fresh process on the same data dir

      // The approval page reconnects on its own: the turn is still paused, the card still asks.
      await expect(approvalPage.getByTestId('reattached-notice')).toBeVisible({ timeout: 40_000 })
      await expect(status(approvalPage)).toHaveAttribute('data-phase', 'paused')
      await card.getByRole('button', { name: 'Approve cancel_order' }).click()
      await turnDone(approvalPage)
      await expect(approvalPage.getByTestId('assistant-message').last()).toContainText('cancelled')

      // The stepped turn was closed by recovery: a notice says so.
      await expect(steppedPage.getByTestId('system-notice').last()).toHaveAttribute('data-reason', 'restarted', { timeout: 40_000 })
      await expect(steppedPage.getByTestId('system-notice').last()).toContainText('restarted')

      await Promise.all([approvalPage.close(), steppedPage.close()])
    } finally {
      h.stop()
    }
  })

  test('review gate: a proposal waits in the writer inspector until Accept writes it', async ({ browser }) => {
    const h = new Harness(PORT + 2, 'web/e2e-live/profile-review.yaml')
    await h.start()
    try {
      const page = await browser.newPage()
      await page.goto(h.url)
      await pickFixture(page, 'remember_fact')
      await send(page, 'Please remember that my preferred name is Sam.')
      await turnDone(page)
      await expect(page.getByTestId('memory-line')).toHaveAttribute('data-written', 'false')
      await expect(page.getByTestId('memory-line')).toContainText('waiting at the review gate')
      await node(page, 'memory_writer').click()
      const inspector = page.getByTestId('inspector')
      await inspector.getByRole('tab', { name: 'Writes' }).click()
      const pending = inspector.locator('[data-testid="pending-proposal"][data-store="semantic"]')
      await expect(pending).toHaveCount(1)
      await pending.getByRole('button', { name: /^Accept/ }).click()
      await expect(pending).toHaveCount(0)
      await expect(inspector.locator('[data-testid="memory-write"][data-store="semantic"][data-status="accepted"]')).toHaveCount(1)
      await page.keyboard.press('Escape')
      await send(page, 'What is my preferred name?')
      await expect(page.getByTestId('chat-turn')).toHaveCount(2)
      await turnDone(page)
      await expect(page.getByTestId('badge-semantic')).toHaveAttribute('data-lit', 'true')
      await page.close()
    } finally {
      h.stop()
    }
  })
})
