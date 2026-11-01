import { afterEach, describe, expect, it } from 'vitest'
import { ApiError, PROVIDER_KEY_HEADER } from '@/api/client'
import { MockServer, type MockPublicOptions } from '@/mock/server'
import { noticeDetail, turnRefusal, useApp, waitText } from './store'

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
  it('sends the default model, then the picked one, and no key header without a key', async () => {
    const { sent } = await boot()
    expect(useApp.getState().capabilities?.mode).toBe('public')
    await useApp.getState().send('What are your support hours?')
    expect(sent[0]!.body.model).toBe('hosted-haiku')
    expect(Object.keys(sent[0]!.headers).map((k) => k.toLowerCase())).not.toContain(PROVIDER_KEY_HEADER.toLowerCase())
    await until(() => useApp.getState().liveTurnId !== null && useApp.getState().turns[useApp.getState().liveTurnId!]?.state?.phase === 'done')
    useApp.getState().setPublicModel('fake-small')
    await useApp.getState().send('And on weekends?')
    expect(sent[1]!.body.model).toBe('fake-small')
  })

  it('an unavailable pick falls back to the default; a key unlocks it and goes out as the header only', async () => {
    const { sent } = await boot()
    useApp.getState().setPublicModel('hosted-sonnet')
    await useApp.getState().send('Hello?')
    expect(sent[0]!.body.model).toBe('hosted-haiku')
    await until(() => useApp.getState().turns[useApp.getState().liveTurnId!]?.state?.phase === 'done')
    useApp.getState().setByoKey('  sk-ant-test-key-1234 ')
    expect(useApp.getState().byoKey).toBe('sk-ant-test-key-1234')
    await useApp.getState().send('Hello again?')
    expect(sent[1]!.body.model).toBe('hosted-sonnet')
    expect(sent[1]!.headers[PROVIDER_KEY_HEADER]).toBe('sk-ant-test-key-1234')
    expect(JSON.stringify(sent[1]!.body)).not.toContain('sk-ant')
    useApp.getState().setByoKey(null)
    expect(useApp.getState().byoKey).toBeNull()
  })

  it('with the budget spent the turn runs on the fake model', async () => {
    const { sent } = await boot({ budget: 'spent' })
    await useApp.getState().send('Hello?')
    expect(sent[0]!.body.model).toBe('fake-small')
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

  it('a turn that fell through to the fake model keeps the notice for the reply', async () => {
    await boot({ budget: 'midturn' })
    await useApp.getState().send('What are your support hours?')
    await until(() => useApp.getState().turns[useApp.getState().liveTurnId!]?.state?.phase === 'done')
    const state = useApp.getState().turns[useApp.getState().liveTurnId!]!.state!
    expect(state.notices).toEqual([{ reason: 'budget_exhausted', message: expect.stringContaining('ran on the fake model') }])
    // The fake model itself is never budget-gated: no notice.
    useApp.getState().setPublicModel('fake-small')
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
    expect(turnRefusal(new ApiError(409, 'budget_exhausted', 'spent', { error: { code: 'budget_exhausted', resets_at: '2026-11-02T00:00:00Z' } }))).toBe(
      "Today's shared budget for Claude models is spent. It resets at 00:00 UTC. Bring your own key or use the fake model.",
    )
    expect(turnRefusal(new ApiError(429, 'rate_limited', 'slow down', undefined, 40))).toContain('Try again in 40 s.')
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
