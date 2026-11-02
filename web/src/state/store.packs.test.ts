import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { MockServer } from '@/mock/server'
import { useApp } from './store'

// The store's P5 pack handling against the in-browser mock: the session's pack at boot, a pack
// picked from the header, New session keeping it, every pack-scoped call naming it, the
// session's pack across a reload, and diagnose on a turn.
const initial = useApp.getState()

function memoryStorage() {
  const data = new Map<string, string>()
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
    clear: () => data.clear(),
    key: (i: number) => [...data.keys()][i] ?? null,
    get length() {
      return data.size
    },
  }
}

async function boot(server = new MockServer({ stepMs: 0 }), sessionId?: string) {
  await useApp.getState().boot(server.transport(), { mock: true, ...(sessionId ? { sessionId } : {}), seedMessages: server.seededMessages() })
  return server
}

async function until(check: () => boolean, ms = 3000) {
  const end = Date.now() + ms
  while (!check()) {
    if (Date.now() > end) throw new Error('timed out')
    await new Promise((r) => setTimeout(r, 10))
  }
}

beforeEach(() => {
  ;(globalThis as { localStorage?: unknown }).localStorage = memoryStorage()
})
afterEach(() => {
  useApp.setState(initial, true)
  delete (globalThis as { localStorage?: unknown }).localStorage
})

describe('store: packs', () => {
  it('boots on the session’s pack and lists the packs', async () => {
    await boot(undefined, 'sess_demo')
    const s = useApp.getState()
    expect(s.packs?.map((p) => p.id)).toEqual(['_starter', 'finance', 'healthcare', 'logistics', 'ecommerce', 'sports'])
    expect(s.pack).toBe('_starter')
    expect(s.api?.pack()).toBe('_starter')
  })

  it('a picked pack starts a new session on it; the inspectors and evals name it; a reload keeps it', async () => {
    const server = await boot(undefined, 'sess_demo')
    useApp.getState().setFixture('tool_call')
    expect(await useApp.getState().choosePack('finance')).toBe(true)
    let s = useApp.getState()
    expect(s.pack).toBe('finance')
    expect(s.order).toEqual([])
    expect(s.sessionId).toMatch(/^sess_/)
    expect(s.sessionId).not.toBe('sess_demo')
    // The starter pack's fixture is not one of finance's, so the menu goes back to the default.
    expect(s.fixture).toBeNull()
    expect(s.notices.at(-1)?.text).toBe('New session on Hollowmere Capital Partners.')
    expect((await s.api!.artifacts()).pack).toBe('finance')
    await until(() => useApp.getState().capabilities?.release?.active_release_id === 'r2')

    await useApp.getState().send('What is the management fee?')
    const turnId = useApp.getState().order[0]!
    await until(() => useApp.getState().turns[turnId]?.state?.phase === 'done')
    expect(server.getTurn(turnId)?.pack).toBe('finance')
    await until(() => useApp.getState().turns[turnId]?.summary?.pack === 'finance')

    // A reload: the stored session and its pack come back.
    const sessionId = useApp.getState().sessionId!
    useApp.setState(initial, true)
    await boot(server)
    s = useApp.getState()
    expect(s.sessionId).toBe(sessionId)
    expect(s.pack).toBe('finance')
    expect(s.order).toEqual([turnId])
  })

  it('New session keeps the chosen pack: the first message starts the session on it', async () => {
    const server = await boot(undefined, 'sess_demo')
    await useApp.getState().choosePack('finance')
    useApp.getState().newSession()
    expect(useApp.getState().pack).toBe('finance')
    expect(useApp.getState().sessionId).toBeNull()
    await useApp.getState().send('Hello')
    const turnId = useApp.getState().order[0]!
    expect(server.getTurn(turnId)?.pack).toBe('finance')
  })

  it('a refused gate comes back as the failure, and Release anyway is forced', async () => {
    await boot(undefined, 'sess_demo')
    const api = useApp.getState().api!
    const skill = await api.artifact('skill', 'order_status')
    await api.saveArtifact('skill', 'order_status', skill.body.replaceAll('lookup_order', 'find_order'))
    const refused = await useApp.getState().createRelease('try')
    expect(refused?.gateFailed?.gate.status).toBe('failed')
    expect(useApp.getState().notices.at(-1)?.text).toMatch(/^The eval gate refused the release: 3 golden cases failed/)
    const forced = await useApp.getState().createRelease('try', { force: true })
    expect(forced?.release?.gate).toMatchObject({ forced: true })
    expect(useApp.getState().notices.at(-1)?.text).toMatch(/created without the gate\. The audit log records it as release_forced\.$/)
  })

  it('diagnoses a turn rated bad and keeps the diagnosis on the turn', async () => {
    await boot(undefined, 'sess_demo')
    expect(await useApp.getState().sendFeedback('turn_tool_call', 'bad', 'wrong')).toBe(true)
    const d = await useApp.getState().diagnose({ turn_id: 'turn_tool_call' })
    expect(d?.layer).toBe('unknown')
    expect(useApp.getState().turns.turn_tool_call?.diagnosis?.diagnosis_id).toBe(d?.diagnosis_id)
    expect(await useApp.getState().diagnose({ turn_id: 'turn_plain_answer' })).toBeNull()
    expect(useApp.getState().notices.at(-1)?.text).toMatch(/^Nothing to diagnose/)
  })
})
