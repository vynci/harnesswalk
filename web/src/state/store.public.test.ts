import { afterEach, describe, expect, it } from 'vitest'
import { ApiError, PROVIDER_KEY_HEADER } from '@/api/client'
import { MockServer, type MockPublicOptions } from '@/mock/server'
import { noticeDetail, selectBudgetBanner, turnRefusal, useApp, waitText } from './store'

// P6 public mode against the in-browser mock: the turn names the picked model, a key of the
// reader's own rides along as a header and nowhere else, and the refusals come back as words.
const initial = useApp.getState()

type Sent = { url: string; headers: Record<string, string>; body: Record<string, unknown> }

async function boot(pub: MockPublicOptions = {}) {
  const server = new MockServer({ stepMs: 0, seed: false, public: pub })
  const transport = server.transport()
  const sent: Sent[] = []
  const fetch = transport.fetch
  transport.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    if (String(input).endsWith('/v1/turns') && init?.method === 'POST') {
      sent.push({ url: String(input), headers: { ...(init.headers as Record<string, string>) }, body: JSON.parse(String(init.body)) })
    }
    return fetch(input, init)
  }) as typeof globalThis.fetch
  await useApp.getState().boot(transport, { mock: true })
  return { server, sent }
}

async function until(check: () => boolean, ms = 3000) {
  const end = Date.now() + ms
  while (!check()) {
    if (Date.now() > end) throw new Error('timed out')
    await new Promise((r) => setTimeout(r, 10))
  }
}

afterEach(() => useApp.setState(initial, true))

describe('store: public mode', () => {
  const done = () => until(() => useApp.getState().liveTurnId !== null && useApp.getState().turns[useApp.getState().liveTurnId!]?.state?.phase === 'done')

  it('Auto is the default: the turn names no model; a pick is named; no key header without a key', async () => {
    const { sent } = await boot()
    expect(useApp.getState().capabilities?.mode).toBe('public')
    expect(useApp.getState().pickedModel).toBeNull()
    await useApp.getState().send('What are your support hours?')
    expect(sent[0]!.body).not.toHaveProperty('model')
    expect(Object.keys(sent[0]!.headers).map((k) => k.toLowerCase())).not.toContain(PROVIDER_KEY_HEADER.toLowerCase())
    await done()
    useApp.getState().pickModel('fake-small')
    await useApp.getState().send('And on weekends?')
    expect(sent[1]!.body.model).toBe('fake-small')
  })

  it('an unavailable pick is Auto again; in public mode a key is never sent and unlocks nothing', async () => {
    const { sent } = await boot()
    useApp.getState().pickModel('hosted-sonnet')
    await useApp.getState().send('Hello?')
    expect(sent[0]!.body).not.toHaveProperty('model')
    await done()
    // The key button is disabled in public mode; a key in the store (an old tab) stays home.
    useApp.getState().setByoKey('  sk-ant-test-key-1234 ')
    await useApp.getState().send('Hello again?')
    expect(sent[1]!.body).not.toHaveProperty('model')
    expect(Object.keys(sent[1]!.headers).map((k) => k.toLowerCase())).not.toContain(PROVIDER_KEY_HEADER.toLowerCase())
    expect(JSON.stringify(sent[1])).not.toContain('sk-ant')
    useApp.getState().setByoKey(null)
    expect(useApp.getState().byoKey).toBeNull()
  })

  it('with the budget spent Auto names no model, the API runs the fake model, and the banner shows', async () => {
    const { sent } = await boot({ budget: 'spent' })
    expect(selectBudgetBanner(useApp.getState())).toMatch(/T00:00:00Z$/)
    await useApp.getState().send('Hello?')
    expect(sent[0]!.body).not.toHaveProperty('model')
    // Closing the banner holds for this budget day in this tab.
    useApp.getState().dismissBudgetBanner()
    expect(selectBudgetBanner(useApp.getState())).toBeNull()
  })

  it('when the budget runs out a Claude pick moves to the fake model with a toast, and stays there after', async () => {
    const { server } = await boot()
    useApp.getState().pickModel('hosted-haiku')
    const opts = (server as unknown as { opts: { public: MockPublicOptions } }).opts.public
    opts.budget = 'spent'
    await useApp.getState().refreshCapabilities()
    expect(useApp.getState().pickedModel).toBe('fake-small')
    expect(useApp.getState().notices.at(-1)?.text).toMatch(/^Today's shared Claude budget is used up\. You're on the scripted fake model until 00:00 UTC( \(.+ your time\))?\.$/)
    expect(selectBudgetBanner(useApp.getState())).not.toBeNull()
    // Once only: a second read changes nothing.
    const count = useApp.getState().notices.length
    await useApp.getState().refreshCapabilities()
    expect(useApp.getState().notices).toHaveLength(count)
    // The budget is back: the banner goes, the pick stays as the reader left it.
    opts.budget = 'ok'
    await useApp.getState().refreshCapabilities()
    expect(selectBudgetBanner(useApp.getState())).toBeNull()
    expect(useApp.getState().pickedModel).toBe('fake-small')
    // Auto is never moved.
    useApp.getState().pickModel(null)
    opts.budget = 'spent'
    await useApp.getState().refreshCapabilities()
    expect(useApp.getState().pickedModel).toBeNull()
  })

  it('a fixture goes with a turn on the scripted model only', async () => {
    const { sent, server } = await boot({ budget: 'spent' })
    expect(useApp.getState().capabilities?.public?.default_fixture).toBe('public_demo')
    useApp.getState().setFixture('tool_call')
    await useApp.getState().send('Where is order A-1001?') // Auto runs on the fake model now
    expect(sent[0]!.body.fixture).toBe('tool_call')
    expect(sent[0]!.body).not.toHaveProperty('model')
    await done()
    // The budget is back: Auto is Claude Haiku again, so the fixture stays home.
    ;(server as unknown as { opts: { public: MockPublicOptions } }).opts.public.budget = 'ok'
    await useApp.getState().refreshCapabilities()
    await useApp.getState().send('Hello?')
    expect(sent[1]!.body).not.toHaveProperty('fixture')
    await done()
    useApp.getState().pickModel('fake-small')
    await useApp.getState().send('Hello again?')
    expect(sent[2]!.body).toMatchObject({ model: 'fake-small', fixture: 'tool_call' })
  })

  it('a 429 says when to try again', async () => {
    await boot({ remaining: 0 })
    await useApp.getState().send('Hello?')
    expect(useApp.getState().notices.at(-1)?.text).toBe('Too many requests from this address this hour. Try again in 12 min.')
    expect(useApp.getState().order).toHaveLength(0)
  })

  it('an API error toast carries the request id in its details', async () => {
    await boot({ remaining: 0 })
    await useApp.getState().send('Hello?')
    expect(useApp.getState().notices.at(-1)?.detail).toMatch(/^Request ID req_mock\d{4}$/)
    expect(noticeDetail(new ApiError(500, 'internal', 'boom', undefined, null, 'req_0123abcd'))).toBe('Request ID req_0123abcd')
    expect(noticeDetail(new Error('boom'))).toBeUndefined()
    expect(noticeDetail(new ApiError(500, 'internal', 'boom'))).toBeUndefined()
  })

  it('a turn that fell through to the fake model keeps the notice for the reply, and the banner shows', async () => {
    await boot({ budget: 'midturn' })
    expect(selectBudgetBanner(useApp.getState())).toBeNull()
    await useApp.getState().send('What are your support hours?')
    await until(() => useApp.getState().turns[useApp.getState().liveTurnId!]?.state?.phase === 'done')
    const state = useApp.getState().turns[useApp.getState().liveTurnId!]!.state!
    expect(state.notices).toEqual([{ reason: 'budget_exhausted', message: expect.stringContaining('ran on the fake model') }])
    expect(useApp.getState().budgetNoticeAt).not.toBeNull()
    expect(selectBudgetBanner(useApp.getState())).not.toBeNull()
    // The fake model itself is never budget-gated: no notice.
    useApp.getState().pickModel('fake-small')
    await useApp.getState().send('And on weekends?')
    await until(() => useApp.getState().order.length === 2 && useApp.getState().turns[useApp.getState().liveTurnId!]?.state?.phase === 'done')
    expect(useApp.getState().turns[useApp.getState().liveTurnId!]!.state!.notices).toEqual([])
  })

  it('a session the API no longer knows (404 session_not_found) starts a fresh one and sends the message there', async () => {
    const { server, sent } = await boot({ ttlS: 7200 })
    await useApp.getState().send('Hello?')
    await until(() => useApp.getState().turns[useApp.getState().liveTurnId!]?.state?.phase === 'done')
    const before = useApp.getState().sessionId!
    // The sandbox ends on the server before this tab noticed (a sleeping laptop, say).
    ;(server as unknown as { sandbox: { startedAt: number } }).sandbox.startedAt -= 7300 * 1000
    await useApp.getState().send('Still there?')
    expect(sent.map((r) => r.body.session_id)).toEqual([before, before, expect.stringMatching(/^sess_/)])
    expect(sent[2]!.body.session_id).not.toBe(before)
    const s = useApp.getState()
    expect(s.sessionId).toBe(sent[2]!.body.session_id)
    expect(s.order).toHaveLength(1)
    expect(s.turns[s.order[0]!]!.userText).toBe('Still there?')
    expect(s.notices.map((n) => n.text)).toContain('That session has ended and its data was deleted, so this message starts a fresh one.')
    expect(s.notices.some((n) => n.level === 'error')).toBe(false)
  })

  it('words the refusals', () => {
    expect(turnRefusal(new ApiError(409, 'budget_exhausted', 'spent', { error: { code: 'budget_exhausted', resets_at: '2026-11-02T00:00:00Z' } }))).toMatch(
      /^Today's shared Claude budget is used up\. Back at 00:00 UTC( \(.+ your time\))?\. Auto and the fake model still work\.$/,
    )
    // Public mode never suggests a key of the reader's own.
    const pub = { mode: 'public', features: { byo_key: true } } as never
    expect(turnRefusal(new ApiError(422, 'provider_not_configured', 'No key is configured for hosted-sonnet. Bring your own key.'), pub)).toBe('This model is not set up on this server.')
    expect(turnRefusal(new ApiError(429, 'rate_limited', 'slow down', undefined, 40))).toContain('Try again in 40 s.')
    expect(turnRefusal(new ApiError(429, 'rate_limited', 'busy', { error: { code: 'rate_limited', limit: 'running_turns' } }, 10))).toBe(
      'Too many Claude turns are running right now. Try again in 10 s, or use the fake model.',
    )
    expect(waitText(null)).toBe('a little while')
    expect(waitText(3600)).toBe('1 h')
  })

  it('a session past its end resets the chat and warns once before', async () => {
    await boot({ ttlS: 7200 })
    await useApp.getState().send('Hello?')
    await until(() => useApp.getState().order.length === 1)
    const expires = useApp.getState().capabilities!.public!.session_expires_at!
    useApp.getState().warnSandbox(expires)
    useApp.getState().warnSandbox(expires)
    expect(useApp.getState().notices.filter((n) => n.text.includes('5 minutes left'))).toHaveLength(1)
    useApp.getState().expireSandbox(expires)
    expect(useApp.getState().order).toHaveLength(0)
    expect(useApp.getState().sessionId).toBeNull()
    expect(useApp.getState().notices.at(-1)?.text).toContain('sandbox session ended')
  })
})
