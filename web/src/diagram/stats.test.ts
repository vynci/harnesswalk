import { describe, expect, it } from 'vitest'
import { diagram, edgeIndex, examples } from '@/test/fixtures'
import { reduceEvents } from '@/state/reducer'
import { memoryBadges, nodeStat } from './stats'

describe('memoryBadges', () => {
  it('has one badge per memory store, in diagram order', () => {
    const badges = memoryBadges(diagram, { nodes: {} }, 'memory')
    expect(badges.map((b) => b.id)).toEqual(['procedural', 'session', 'semantic', 'episodic', 'knowledge'])
    expect(badges.every((b) => b.state === 'idle')).toBe(true)
  })

  it('lights each store from its own expert-child events', () => {
    const s = reduceEvents(examples.plain_answer, edgeIndex)
    const byId = Object.fromEntries(memoryBadges(diagram, s, 'memory').map((b) => [b.id, b.state]))
    expect(byId).toEqual({ procedural: 'done', session: 'done', semantic: 'idle', episodic: 'idle', knowledge: 'idle' })
  })

  it('lights stores one at a time while memory is open', () => {
    const events = examples.plain_answer
    const idx = events.findIndex((e) => e.type === 'node_enter' && e.node_id === 'procedural')
    const s = reduceEvents(events.slice(0, idx + 1), edgeIndex)
    const byId = Object.fromEntries(memoryBadges(diagram, s, 'memory').map((b) => [b.id, b.state]))
    expect(byId.procedural).toBe('active')
    expect(byId.session).toBe('idle')
  })

  it('falls back to stores_queried when the harness emits no expert children', () => {
    const children = new Set(['procedural', 'session', 'semantic', 'episodic', 'knowledge'])
    const s = reduceEvents(examples.plain_answer.filter((e) => !(e.node_id && children.has(e.node_id))), edgeIndex)
    const byId = Object.fromEntries(memoryBadges(diagram, s, 'memory').map((b) => [b.id, b.state]))
    expect(byId).toMatchObject({ procedural: 'done', session: 'done', semantic: 'idle' })
  })
})

describe('nodeStat', () => {
  const s = reduceEvents(examples.approval_pause, edgeIndex)
  it('summarises the latest exit in one short line', () => {
    expect(nodeStat(s.nodes.policy)).toBe('needs_approval · cancel_order')
    expect(nodeStat(s.nodes.context_builder)).toBe('619 / 16.0k tok')
    expect(nodeStat(s.nodes.model_layer)).toBe('fake-small · 184 ms')
    expect(nodeStat(s.nodes.approval)).toBe('approve · 41.3 s')
    expect(nodeStat(s.nodes.trace)).toBe('66 events · sqlite')
    expect(nodeStat(undefined)).toBeUndefined()
  })

  it('reads summary keys, not node ids, so a new node needs no UI change', () => {
    const run = (lastSummary: Record<string, unknown>) => ({ state: 'done' as const, enters: 1, totalDurationMs: 5, lastDurationMs: 5, lastSummary })
    expect(nodeStat(run({ decision: 'allow', tool: 'lookup_order', rule: 'dev.read.allow' }))).toBe('allow · lookup_order')
    expect(nodeStat(run({ rule: 'default', model: 'fake-small' }))).toBe('default · fake-small')
    // 0.2.0: a call the canary split routed names its arm (router.md, "0.2.0: the canary rule").
    expect(nodeStat(run({ rule: 'canary', model: 'tuned-approvals-fake', alternatives: ['fake-student'], arm: 'candidate', bucket: 0.4649 }))).toBe('canary · candidate arm')
    expect(nodeStat(run({ provider: 'fake', model: 'fake-small', attempt: 1 }))).toBe('fake · fake-small')
    expect(nodeStat(run({ tool: 'lookup_order', executed: true, outcome: 'ok' }))).toBe('lookup_order · ok')
    expect(nodeStat(run({ phase: 'reply', reply_chars: 70 }))).toBe('reply · 70 chars')
    expect(nodeStat(run({ cases: 4, passed: 3 }))).toBe('3/4 passed')
    expect(nodeStat(run({ something_new: true }))).toBe('5 ms')
    expect(nodeStat({ state: 'active', enters: 1, totalDurationMs: 0, enterSummary: { max_steps: 8, budget_tokens: 16000 } })).toBe('max 8 steps')
  })
})
