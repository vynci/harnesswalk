import { describe, expect, it } from 'vitest'
import { diagram, edgeIndex, examples } from '@/test/fixtures'
import { reduceEvents } from '@/state/reducer'
import { memoryBadges, nodeStat } from './stats'

describe('memoryBadges', () => {
  it('has one badge per memory store, in diagram order', () => {
    const badges = memoryBadges(diagram, { nodes: {} })
    expect(badges.map((b) => b.id)).toEqual(['procedural', 'session', 'semantic', 'episodic', 'knowledge'])
    expect(badges.every((b) => b.state === 'idle')).toBe(true)
  })

  it('lights each store from its own expert-child events', () => {
    const s = reduceEvents(examples.plain_answer, edgeIndex)
    const byId = Object.fromEntries(memoryBadges(diagram, s).map((b) => [b.id, b.state]))
    expect(byId).toEqual({ procedural: 'done', session: 'done', semantic: 'idle', episodic: 'idle', knowledge: 'idle' })
  })

  it('lights stores one at a time while memory is open', () => {
    const events = examples.plain_answer
    const idx = events.findIndex((e) => e.type === 'node_enter' && e.node_id === 'procedural')
    const s = reduceEvents(events.slice(0, idx + 1), edgeIndex)
    const byId = Object.fromEntries(memoryBadges(diagram, s).map((b) => [b.id, b.state]))
    expect(byId.procedural).toBe('active')
    expect(byId.session).toBe('idle')
  })

  it('falls back to stores_queried when the harness emits no expert children', () => {
    const children = new Set(['procedural', 'session', 'semantic', 'episodic', 'knowledge'])
    const s = reduceEvents(examples.plain_answer.filter((e) => !(e.node_id && children.has(e.node_id))), edgeIndex)
    const byId = Object.fromEntries(memoryBadges(diagram, s).map((b) => [b.id, b.state]))
    expect(byId).toMatchObject({ procedural: 'done', session: 'done', semantic: 'idle' })
  })
})

describe('nodeStat', () => {
  const s = reduceEvents(examples.approval_pause, edgeIndex)
  it('summarises the latest exit in one short line', () => {
    expect(nodeStat('policy', s.nodes.policy)).toBe('needs_approval · cancel_order')
    expect(nodeStat('context_builder', s.nodes.context_builder)).toBe('619 / 16.0k tok')
    expect(nodeStat('model_layer', s.nodes.model_layer)).toBe('fake-small · 184 ms')
    expect(nodeStat('approval', s.nodes.approval)).toBe('approve · 41.3 s')
    expect(nodeStat('trace', s.nodes.trace)).toBe('66 events · sqlite')
    expect(nodeStat('audit', undefined)).toBeUndefined()
  })
})
