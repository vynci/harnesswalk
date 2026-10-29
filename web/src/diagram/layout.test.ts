import { describe, expect, it } from 'vitest'
import { diagram } from '@/test/fixtures'
import { computeLayout, visibleEdges, visibleNodeIds } from './layout'

const simple = { view: 'simple' as const, collapsed: new Set<string>() }
const expert = { view: 'expert' as const, collapsed: new Set<string>() }

function overlaps(a: { x: number; y: number; width: number; height: number }, b: typeof a) {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
}

describe('computeLayout', () => {
  it('is deterministic', () => {
    expect(computeLayout(diagram, simple)).toEqual(computeLayout(diagram, simple))
    expect(computeLayout(diagram, expert)).toEqual(computeLayout(diagram, expert))
  })

  it('draws the 15 simple nodes in four plane boxes in the simple view', () => {
    const l = computeLayout(diagram, simple)
    expect(l.nodes.map((n) => n.id).sort()).toEqual(diagram.nodes.filter((n) => n.view === 'simple').map((n) => n.id).sort())
    expect(l.planes.map((p) => p.id)).toEqual(['control', 'context', 'execution', 'ops'])
    for (const n of l.nodes) {
      const box = l.planes.find((p) => p.id === n.node.group)!
      expect(n.x).toBeGreaterThanOrEqual(box.x)
      expect(n.y).toBeGreaterThanOrEqual(box.y)
      expect(n.x + n.width).toBeLessThanOrEqual(box.x + box.width)
      expect(n.y + n.height).toBeLessThanOrEqual(box.y + box.height)
    }
  })

  it('never overlaps nodes or planes', () => {
    for (const opts of [simple, expert]) {
      const l = computeLayout(diagram, opts)
      const tops = l.nodes.filter((n) => !n.parentId)
      for (let i = 0; i < tops.length; i++) for (let j = i + 1; j < tops.length; j++) expect(overlaps(tops[i]!, tops[j]!), `${tops[i]!.id} ${tops[j]!.id}`).toBe(false)
      for (let i = 0; i < l.planes.length; i++) for (let j = i + 1; j < l.planes.length; j++) expect(overlaps(l.planes[i]!, l.planes[j]!)).toBe(false)
    }
  })

  it('places runtime planes left to right in group order and ops beneath them', () => {
    const l = computeLayout(diagram, simple)
    const [control, context, execution, ops] = l.planes
    expect(control!.x).toBeLessThan(context!.x)
    expect(context!.x).toBeLessThan(execution!.x)
    expect(ops!.y).toBeGreaterThan(Math.max(control!.y + control!.height, execution!.y + execution!.height))
  })

  it('reads each runtime column in walk order', () => {
    const l = computeLayout(diagram, simple)
    const y = (id: string) => l.nodes.find((n) => n.id === id)!.y
    expect(y('gateway')).toBeLessThan(y('input_guardrails'))
    expect(y('context_builder')).toBeLessThan(y('memory'))
    expect(y('loop')).toBeLessThan(y('model_layer'))
  })

  it('nests expanded expert children inside their parent', () => {
    const l = computeLayout(diagram, expert)
    expect(l.nodes).toHaveLength(diagram.nodes.length)
    for (const child of l.nodes.filter((n) => n.parentId)) {
      const p = l.nodes.find((n) => n.id === child.parentId)!
      expect(p.expanded).toBe(true)
      expect(child.x).toBeGreaterThanOrEqual(p.x)
      expect(child.y).toBeGreaterThanOrEqual(p.y)
      expect(child.x + child.width).toBeLessThanOrEqual(p.x + p.width + 0.001)
      expect(child.y + child.height).toBeLessThanOrEqual(p.y + p.height + 0.001)
    }
  })

  it('hides the children of a collapsed parent', () => {
    const l = computeLayout(diagram, { view: 'expert', collapsed: new Set(['memory']) })
    expect(l.nodes.some((n) => n.id === 'procedural')).toBe(false)
    expect(l.nodes.some((n) => n.id === 'router')).toBe(true)
    expect(l.nodes.find((n) => n.id === 'memory')?.expanded).toBe(false)
  })

  it('honours layout hints from diagram.json', () => {
    const hinted = { ...diagram, nodes: diagram.nodes.map((n) => (n.id === 'gateway' ? { ...n, layout: { x: 7, y: 11 } } : n)) }
    const g = computeLayout(hinted, simple).nodes.find((n) => n.id === 'gateway')!
    expect([g.x, g.y]).toEqual([7, 11])
  })

  it('routes an edge by the sides and bend points its diagram.json hint names', () => {
    const l = computeLayout(diagram, simple)
    const e = l.edges.find((x) => x.id === 'gateway_to_policy')!
    expect([e.sourceSide, e.targetSide]).toEqual(['bottom', 'top'])
    // The skip edge runs straight down beside the staggered input guardrails.
    expect(e.points).toHaveLength(2)
    expect(e.points[0]![0]).toBe(e.points[1]![0])
  })

  it('shares one top and bottom across the runtime columns', () => {
    const l = computeLayout(diagram, simple)
    const cols = l.planes.filter((p) => p.id !== 'ops')
    expect(new Set(cols.map((p) => p.y)).size).toBe(1)
    expect(new Set(cols.map((p) => p.y + p.height)).size).toBe(1)
  })
})

describe('visibleEdges', () => {
  it('keeps simple edges and folds refinements into them in the simple view', () => {
    const vis = visibleNodeIds(diagram, simple)
    const edges = visibleEdges(diagram, vis)
    expect(edges.every((e) => e.edge.view === 'simple')).toBe(true)
    const mem = edges.find((e) => e.edge.id === 'memory_to_context_builder')!
    expect(mem.carries).toContain('procedural_to_context_builder')
  })

  it('hides a simple edge once a drawn edge refines it in the expert view', () => {
    const vis = visibleNodeIds(diagram, expert)
    const ids = visibleEdges(diagram, vis).map((e) => e.edge.id)
    expect(ids).toContain('procedural_to_context_builder')
    expect(ids).not.toContain('memory_to_context_builder')
    expect(ids).toContain('router_to_providers')
  })
})
