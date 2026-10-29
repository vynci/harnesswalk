import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

// The real harness: `harnesswalk serve --provider fake` in dev mode serves this build (see
// playwright.live.config.ts). Each test opens its own browser context, so its own session.
// Fixtures are picked per turn from the header menu, which lists what the API offers.

const node = (page: Page, id: string) => page.getByTestId(`node-${id}`)
const status = (page: Page) => page.getByTestId('turn-status')

async function pickFixture(page: Page, name: string) {
  await page.getByTestId('fixture-select').click()
  await page.getByRole('option', { name, exact: true }).click()
}

async function send(page: Page, text: string) {
  await page.getByTestId('composer-input').fill(text)
  await page.getByTestId('send-button').click()
}

async function expectStates(page: Page, states: Record<string, string>) {
  for (const [id, state] of Object.entries(states)) {
    await expect(node(page, id), id).toHaveAttribute('data-state', state)
  }
}

/** The turn the chat shows last, read back from the API (the source of truth). */
async function lastTurn(page: Page, request: APIRequestContext) {
  const turnId = await page.getByTestId('chat-turn').last().getAttribute('data-turn-id')
  expect(turnId).toBeTruthy()
  const r = await request.get(`/v1/turns/${turnId}`)
  expect(r.ok()).toBe(true)
  return (await r.json()) as {
    turn_id: string
    session_id: string
    status: string
    exit: string | null
    reply: string | null
    event_count: number
    cost: { input_tokens: number; output_tokens: number; usd: number }
  }
}

/** Press Next at every breakpoint until the turn ends (or `stopAt` is the paused node). */
async function stepThrough(page: Page, stopAt?: string): Promise<string[]> {
  const controls = page.getByTestId('step-controls')
  const visited: string[] = []
  let lastSeq = -1
  for (let i = 0; i < 80; i++) {
    // Wait for a new pause (a higher seq than the one just released) or the end of the turn.
    await expect
      .poll(async () => {
        if ((await status(page).getAttribute('data-phase')) === 'done') return 'done'
        if (!(await controls.isVisible())) return 'moving'
        return Number(await controls.getAttribute('data-seq')) > lastSeq ? 'paused' : 'moving'
      })
      .not.toBe('moving')
    if ((await status(page).getAttribute('data-phase')) === 'done') return visited
    const nodeId = (await controls.getAttribute('data-node'))!
    lastSeq = Number(await controls.getAttribute('data-seq'))
    visited.push(nodeId)
    // A node the simple view does not draw (an expert child) is shown through its parent.
    if (await node(page, nodeId).count()) await expect(node(page, nodeId)).toHaveAttribute('data-state', 'waiting')
    else await expect(page.locator('[data-current="true"]')).toHaveCount(1)
    if (nodeId === stopAt) return visited
    await controls.getByRole('button', { name: 'Next node' }).click()
  }
  throw new Error(`the turn did not end after 80 steps: ${visited.join(', ')}`)
}

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('mode-badge')).toContainText('dev')
  await expect(page.getByTestId('mode-badge')).not.toContainText('mock')
  await expect(node(page, 'gateway')).toBeVisible()
})

test.afterEach(async ({ page }) => {
  // Every event the real API sent validated against spec/events.schema.json.
  await expect(page.getByText(/invalid events/)).toHaveCount(0)
})

test('tool_call: a read-only tool runs under policy and the reply streams in', async ({ page, request }) => {
  await pickFixture(page, 'tool_call')
  await send(page, 'Where is order A-1001?')
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
  await expect(status(page)).toContainText('exit: answered')
  await expectStates(page, {
    gateway: 'done',
    input_guardrails: 'done',
    context_builder: 'done',
    memory: 'done',
    loop: 'done',
    model_layer: 'done',
    tools: 'done',
    policy: 'done',
    output_guardrails: 'done',
    trace: 'done',
    approval: 'idle',
  })
  await expect(page.getByTestId('stat-policy')).toContainText('allow')
  await expect(page.getByTestId('badge-procedural')).toHaveAttribute('data-lit', 'true')
  // P3: the seed fact about A-1001 makes semantic memory answer too.
  await expect(page.getByTestId('badge-semantic')).toHaveAttribute('data-state', 'done')
  await expect(page.getByTestId('badge-semantic')).toHaveAttribute('data-lit', 'true')
  // The tool line opens to where the call ran: the subprocess sandbox, network blocked.
  await page.getByTestId('tool-line').last().click()
  const chip = page.getByTestId('tool-trace').last().getByTestId('sandbox-chip')
  await expect(chip).toHaveAttribute('data-level', 'subprocess')
  await expect(chip).toHaveAttribute('data-network', 'blocked')
  const turn = await lastTurn(page, request)
  expect(turn.exit).toBe('answered')
  await expect(page.getByTestId('assistant-message').last()).toHaveText(turn.reply!)
  expect(turn.reply).toContain('A-1001')
  // The receipt shown is turn_end.cost, and every event arrived once.
  const tokens = turn.cost.input_tokens + turn.cost.output_tokens
  expect(tokens).toBeGreaterThan(0)
  await expect(status(page)).toContainText(`${tokens} tok`)
  await expect(status(page)).toContainText(`${turn.event_count} events`)
  // The reply also came as message_delta events inside the gateway's reply span.
  await node(page, 'gateway').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('tab', { name: /^Events/ }).click()
  await expect(inspector.getByTestId('node-events').getByText('message_delta').first()).toBeVisible()
})

test('approval_approve: the card pauses the turn and Approve runs the tool', async ({ page, request }) => {
  await pickFixture(page, 'approval_approve')
  await send(page, 'Please cancel order A-1002.')
  const card = page.getByTestId('approval-card').last()
  await expect(card).toContainText('Run cancel_order?')
  await expect(card.getByTestId('approval-tool')).toHaveText('cancel_order')
  await expect(card.getByTestId('approval-action-class')).toHaveText('destructive')
  await expect(card.getByTestId('approval-countdown')).toHaveText(/^\d+:\d\d$/)
  await expect(status(page)).toHaveAttribute('data-phase', 'paused')
  await expectStates(page, { approval: 'waiting', policy: 'done', tools: 'active' })
  await card.getByRole('button', { name: 'Approve cancel_order' }).click()
  // Decided: the confirmation goes and the tool line carries the decision.
  await expect(page.getByTestId('approval-card')).toHaveCount(0)
  await expect(page.getByTestId('tool-line').last()).toContainText('approved by')
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
  await expectStates(page, { approval: 'done', audit: 'done', tools: 'done', trace: 'done' })
  await expect(page.getByTestId('assistant-message').last()).toContainText('cancelled')
  expect((await lastTurn(page, request)).exit).toBe('answered')
})

test('approval_deny: Deny skips the tool and the reply says so', async ({ page, request }) => {
  await pickFixture(page, 'approval_deny')
  await send(page, 'Please cancel order A-1002.')
  const card = page.getByTestId('approval-card').last()
  await card.getByRole('button', { name: 'Deny cancel_order' }).click()
  await expect(page.getByTestId('approval-card')).toHaveCount(0)
  await expect(page.getByTestId('tool-line').last()).toHaveAttribute('data-call-status', 'denied')
  await expect(page.getByTestId('tool-line').last()).toContainText('denied by')
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
  await expectStates(page, { approval: 'done', audit: 'done', output_guardrails: 'done' })
  await expect(page.getByTestId('assistant-message').last()).toContainText('did not cancel')
  expect((await lastTurn(page, request)).exit).toBe('answered')
})

test('step_mode: Next walks every node, one hop at a time, to the end', async ({ page, request }) => {
  await pickFixture(page, 'step_mode')
  await page.getByRole('switch', { name: /Step mode/ }).click()
  await send(page, 'What are your support hours?')
  const visited = await stepThrough(page)
  // The simple-view walk, in order (expert children pause too, in between).
  const simple = visited.filter((id) => ['gateway', 'input_guardrails', 'context_builder', 'memory', 'loop', 'model_layer', 'output_guardrails', 'trace'].includes(id))
  expect(simple).toEqual(['gateway', 'input_guardrails', 'context_builder', 'memory', 'loop', 'model_layer', 'output_guardrails', 'gateway', 'trace'])
  await expectStates(page, { gateway: 'done', loop: 'done', output_guardrails: 'done', trace: 'done', tools: 'idle' })
  const turn = await lastTurn(page, request)
  expect(turn.exit).toBe('answered')
  await expect(page.getByTestId('assistant-message').last()).toHaveText(turn.reply!)
})

test('cancel_running: Stop while paused before the tool ends the turn cancelled', async ({ page, request }) => {
  await pickFixture(page, 'cancel_running')
  await page.getByRole('switch', { name: /Step mode/ }).click()
  await send(page, 'Where is order A-1001?')
  await stepThrough(page, 'tools')
  await page.getByTestId('stop-button').click()
  await expect(status(page)).toContainText('exit: cancelled')
  await expect(page.getByTestId('system-notice').last()).toContainText('cancelled')
  await expect(page.getByTestId('stop-button')).toHaveCount(0)
  await expectStates(page, { tools: 'error', loop: 'error', model_layer: 'done', policy: 'idle', output_guardrails: 'idle' })
  const turn = await lastTurn(page, request)
  expect(turn.status).toBe('done')
  expect(turn.exit).toBe('cancelled')
  // Stopping a finished turn is a 409 with the final state, which the UI ignores quietly.
  const again = await request.post(`/v1/turns/${turn.turn_id}/cancel`, { data: {} })
  expect(again.status()).toBe(409)
})

test('guardrail_block: an oversized message never reaches the model', async ({ page, request }) => {
  await pickFixture(page, 'guardrail_block')
  const caps = await (await request.get('/v1/capabilities')).json()
  await send(page, 'Please help. '.repeat(700)) // over input_guardrails.size, under the transport cap
  expect(9100).toBeLessThan(caps.limits.max_message_chars)
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
  await expect(status(page)).toContainText('exit: blocked')
  await expect(page.getByTestId('system-notice').last()).toContainText('Blocked by a guardrail')
  await expectStates(page, { gateway: 'done', input_guardrails: 'done', trace: 'done', context_builder: 'idle', loop: 'idle', model_layer: 'idle' })
  await expect(page.getByTestId('stat-input_guardrails')).toContainText('block')
  expect((await lastTurn(page, request)).exit).toBe('blocked')
})

test('the inspector loads the loop and its code peek from the API', async ({ page }) => {
  await node(page, 'loop').click()
  const inspector = page.getByTestId('inspector')
  await expect(inspector.getByTestId('inspector-title')).toHaveText('Loop')
  await expect(inspector.getByTestId('inspector-lesson')).not.toBeEmpty()
  await inspector.getByRole('tab', { name: 'Code' }).click()
  await expect(inspector).toContainText('harnesswalk/execution/loop.py')
  await expect(inspector.getByTestId('code-view')).toContainText('class Loop')
  await expect(inspector.getByRole('navigation', { name: 'Symbols' }).getByRole('button').filter({ hasText: /^Loop:\d+$/ })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(inspector).toBeHidden()
})

test('a dropped stream reconnects from its cursor without losing or repeating events', async ({ page, request }) => {
  // The first connection gets only what is stored so far, then the server side "drops": the
  // body ends. The client must reconnect with ?after=<last seq> and carry on from there.
  const urls: string[] = []
  await page.route('**/v1/turns/*/events*', async (route) => {
    const url = route.request().url()
    urls.push(url)
    if (urls.length > 1) return route.continue()
    const cut = await route.fetch({ url: `${url}${url.includes('?') ? '&' : '?'}follow=false` })
    await route.fulfill({ response: cut, body: await cut.text() })
  })
  await pickFixture(page, 'plain_answer')
  await page.getByRole('switch', { name: /Step mode/ }).click()
  await send(page, 'What are your support hours?')
  const controls = page.getByTestId('step-controls')
  await expect(controls).toHaveAttribute('data-node', 'gateway')
  await controls.getByRole('button', { name: 'Continue to the end' }).click()
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
  expect(urls.length).toBeGreaterThan(1)
  expect(urls[1]).toMatch(/\/events\?after=\d+$/)
  const turn = await lastTurn(page, request)
  await expect(status(page)).toContainText(`${turn.event_count} events`)
  await expect(page.getByTestId('assistant-message').last()).toHaveText(turn.reply!)
})

test('a reload mid-turn picks the paused turn up again', async ({ page }) => {
  await pickFixture(page, 'plain_answer')
  await page.getByRole('switch', { name: /Step mode/ }).click()
  await send(page, 'What are your support hours?')
  await expect(page.getByTestId('step-controls')).toHaveAttribute('data-node', 'gateway')
  await page.reload()
  const controls = page.getByTestId('step-controls')
  await expect(controls).toHaveAttribute('data-node', 'gateway')
  await expect(page.getByTestId('user-message')).toHaveText('What are your support hours?')
  await controls.getByRole('button', { name: 'Continue to the end' }).click()
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
})

test('history reloads, a past turn replays, and New session starts clean', async ({ page }) => {
  await pickFixture(page, 'tool_call')
  await send(page, 'Where is order A-1001?')
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
  await page.reload()
  await expect(page.getByTestId('chat-turn')).toHaveCount(1)
  await expect(page.getByTestId('user-message')).toHaveText('Where is order A-1001?')
  await page.getByTestId('replay-button').click()
  const slider = page.getByRole('slider', { name: 'Replay position' })
  await slider.focus()
  await page.keyboard.press('Home')
  await expect(page.getByTestId('replay-position')).toContainText('turn_start')
  await expect(node(page, 'gateway')).toHaveAttribute('data-state', 'idle')
  await page.keyboard.press('End')
  await expect(page.getByTestId('replay-position')).toContainText('turn_end')
  await expect(node(page, 'tools')).toHaveAttribute('data-state', 'done')
  await page.getByTestId('replay-exit').click()

  // A new session is a new session on the server too, not the one in the cookie.
  await page.getByRole('button', { name: 'Session actions' }).click()
  await page.getByRole('menuitem', { name: 'New session' }).click()
  await pickFixture(page, 'plain_answer')
  await send(page, 'What are your support hours?')
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
  await page.reload()
  await expect(page.getByTestId('chat-turn')).toHaveCount(1)
  await expect(page.getByTestId('user-message')).toHaveText('What are your support hours?')
})

test('a node switched off in the inspector is marked off next turn, then skipped', async ({ page }) => {
  await node(page, 'output_guardrails').click()
  const inspector = page.getByTestId('inspector')
  await inspector.getByRole('switch', { name: /Disable Output guardrails/ }).click()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('off-output_guardrails')).toHaveText('off next turn')
  await pickFixture(page, 'plain_answer')
  await send(page, 'What are your support hours?')
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
  await expect(node(page, 'output_guardrails')).toHaveAttribute('data-state', 'skipped')
})

test('a saved theme applies before the app script runs, so there is no flash', async ({ page }) => {
  await page.evaluate(() => localStorage.setItem('harnesswalk.theme', JSON.stringify('dark')))
  await page.route('**/assets/*.js', (route) => route.abort()) // the inline script alone
  await page.reload()
  await expect(page.locator('html')).toHaveClass(/\bdark\b/)
  await page.unroute('**/assets/*.js')
  await page.evaluate(() => localStorage.removeItem('harnesswalk.theme'))
})

test('a second turn on a one-turn fixture explains that fake replies are canned', async ({ page }) => {
  await pickFixture(page, 'plain_answer')
  await send(page, 'What are your support hours?')
  await expect(status(page)).toHaveAttribute('data-phase', 'done')
  await send(page, 'And on weekends?')
  await expect(page.getByTestId('chat-turn')).toHaveCount(2)
  await expect(status(page)).toContainText('exit: error')
  await expect(page.getByTestId('system-notice').last()).toContainText('canned replies')
  await expect(node(page, 'model_layer')).toHaveAttribute('data-state', 'error')
  await expect(page.getByTestId('stat-model_layer')).toContainText('fixture_exhausted')
})
