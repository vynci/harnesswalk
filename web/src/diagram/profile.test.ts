import { describe, expect, it } from 'vitest'
import { diagram } from '@/test/fixtures'
import { initialTurnState } from '@/state/reducer'
import type { FlowEdgeType } from './FlowEdge'
import type { HarnessFlowNode } from './HarnessNode'
import { computeLayout } from './layout'
import { toFlow } from './flow'

// 0.2.0: a profile dims what it switches off; nothing is hidden and the layout does not move.
const layout = computeLayout(diagram, { view: 'expert', collapsed: new Set<string>() })
const switchable = diagram.nodes.filter((n) => n.can_disable).map((n) => n.id)
const off = new Set(switchable.slice(0, 4))

function flow(profileOff?: Set<string>) {
  return toFlow(diagram, layout, initialTurnState(), { disabled: new Set(), profileOff, profileId: profileOff ? 'minimal' : null, onToggle: () => {} })
}
const harness = (nodes: ReturnType<typeof flow>['nodes']) => nodes.filter((n): n is HarnessFlowNode => n.type === 'harness')

describe('profile dimming', () => {
  it('marks the switched-off nodes, keeps every node, and keeps the layout', () => {
    const plain = harness(flow().nodes)
    const dimmed = harness(flow(off).nodes)
    expect(dimmed.map((n) => n.id)).toEqual(plain.map((n) => n.id))
    expect(dimmed.map((n) => n.position)).toEqual(plain.map((n) => n.position))
    for (const n of dimmed) {
      expect(n.data.off === 'profile').toBe(off.has(n.id))
      expect(n.data.profile ?? null).toBe(off.has(n.id) ? 'minimal' : null)
      if (off.has(n.id)) expect(n.ariaLabel).toContain('off in profile minimal')
    }
    expect(plain.every((n) => n.data.off === null)).toBe(true)
  })

  it('dims the edges into and out of a switched-off node, and only those', () => {
    const edges = flow(off).edges as FlowEdgeType[]
    expect(edges.length).toBe((flow().edges as FlowEdgeType[]).length)
    for (const e of edges) expect(e.data!.dim).toBe(off.has(e.source) || off.has(e.target))
    expect(edges.some((e) => e.data!.dim)).toBe(true)
  })

  it('a node switched off for the next turn and by the profile reads as the profile', () => {
    const id = [...off][0]!
    const n = harness(toFlow(diagram, layout, initialTurnState(), { disabled: new Set([id]), profileOff: off, profileId: 'minimal', onToggle: () => {} }).nodes).find((x) => x.id === id)!
    expect(n.data.off).toBe('profile')
  })
})
