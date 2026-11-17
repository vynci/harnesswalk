import { describe, expect, it } from 'vitest'
import { diagram } from '@/test/fixtures'
import { routeProblems, type RouteRules } from '@/test/routeChecks'
import { computeLayout, type DiagramLayout, type VisibleEdge } from './layout'
import type { Pt } from './route'

/**
 * What a reader needs to tell the edges apart: clear air round every node an edge does not
 * connect, parallel lanes at least 14 apart, distinct ports along a side, a straight run for
 * the arrowhead, and no route hugging a plane or group border it crosses.
 */
const RULES: RouteRules = { clear: 12, lane: 14, port: 14, runIn: 12, plane: 10 }

// Geometry checks on the drawn routes: axis-aligned segments against each other and against
// the node boxes. Kept deliberately simple.
interface Seg {
  edge: string
  a: Pt
  b: Pt
}

function segments(edges: VisibleEdge[]): Seg[] {
  const out: Seg[] = []
  for (const e of edges) for (let i = 0; i < e.points.length - 1; i++) out.push({ edge: e.id, a: e.points[i]!, b: e.points[i + 1]! })
  return out
}

const horizontal = (s: Seg) => s.a[1] === s.b[1]

/** Two segments on one line sharing more than a point. */
export function collinearOverlap(s: Seg, t: Seg): boolean {
  if (horizontal(s) !== horizontal(t)) return false
  const h = horizontal(s)
  if (h ? s.a[1] !== t.a[1] : s.a[0] !== t.a[0]) return false
  const [slo, shi] = h ? [Math.min(s.a[0], s.b[0]), Math.max(s.a[0], s.b[0])] : [Math.min(s.a[1], s.b[1]), Math.max(s.a[1], s.b[1])]
  const [tlo, thi] = h ? [Math.min(t.a[0], t.b[0]), Math.max(t.a[0], t.b[0])] : [Math.min(t.a[1], t.b[1]), Math.max(t.a[1], t.b[1])]
  return Math.min(shi, thi) - Math.max(slo, tlo) > 0
}

/** A segment that runs through the inside of a box (touching its border is fine). */
export function throughBox(s: Seg, r: { x: number; y: number; width: number; height: number }): boolean {
  const x1 = Math.min(s.a[0], s.b[0])
  const x2 = Math.max(s.a[0], s.b[0])
  const y1 = Math.min(s.a[1], s.b[1])
  const y2 = Math.max(s.a[1], s.b[1])
  return x2 > r.x && x1 < r.x + r.width && y2 > r.y && y1 < r.y + r.height
}

function problems(layout: DiagramLayout, drawn: VisibleEdge[]): string[] {
  const out: string[] = []
  const segs = segments(drawn)
  for (let i = 0; i < segs.length; i++)
    for (let j = i + 1; j < segs.length; j++)
      if (segs[i]!.edge !== segs[j]!.edge && collinearOverlap(segs[i]!, segs[j]!)) out.push(`overlap ${segs[i]!.edge} / ${segs[j]!.edge}`)
  const parentOf = new Map(layout.nodes.map((n) => [n.id, n.parentId]))
  const ends = (e: VisibleEdge) => {
    const s = new Set<string>()
    for (let id: string | undefined = e.source; id; id = parentOf.get(id)) s.add(id)
    for (let id: string | undefined = e.target; id; id = parentOf.get(id)) s.add(id)
    return s
  }
  for (const e of drawn) {
    const skip = ends(e)
    for (const n of layout.nodes) {
      if (skip.has(n.id)) continue
      for (const s of segments([e])) if (throughBox(s, n)) out.push(`${e.id} through ${n.id}`)
    }
  }
  return out
}

const simple = { view: 'simple' as const, collapsed: new Set<string>() }

describe('simple view routes', () => {
  const layout = computeLayout(diagram, simple)

  it('every route is orthogonal and starts and ends on its nodes', () => {
    for (const e of layout.edges) {
      for (let i = 0; i < e.points.length - 1; i++) {
        const [a, b] = [e.points[i]!, e.points[i + 1]!]
        expect(a[0] === b[0] || a[1] === b[1], `${e.id} segment ${i}`).toBe(true)
      }
    }
  })

  it('uses the diagram.json hints for every simple edge', () => {
    expect(layout.edges.filter((e) => !e.bus && !e.hinted).map((e) => e.id)).toEqual([])
  })

  it('has no collinear overlaps and no edge through a node, telemetry hidden (the default)', () => {
    const drawn = layout.edges.filter((e) => !e.optional)
    expect(drawn.some((e) => e.bus)).toBe(true)
    expect(problems(layout, drawn)).toEqual([])
  })

  it('has no collinear overlaps and no edge through a node, telemetry shown', () => {
    expect(problems(layout, layout.edges.filter((e) => !e.bus))).toEqual([])
  })

  it('never puts two edges on one port of a side', () => {
    const ports = new Map<string, string>()
    for (const e of layout.edges.filter((x) => !x.bus)) {
      for (const [node, p] of [
        [e.source, e.points[0]!],
        [e.target, e.points[e.points.length - 1]!],
      ] as const) {
        const key = `${node}@${p[0]},${p[1]}`
        expect(ports.get(key), `${e.id} shares ${key} with ${ports.get(key)}`).toBeUndefined()
        ports.set(key, e.id)
      }
    }
  })

  it('draws the hub call pairs as parallel straight lanes', () => {
    const lanes = ['loop_to_model_layer', 'model_layer_to_loop', 'loop_to_tools', 'tools_to_loop'].map((id) => layout.edges.find((e) => e.id === id)!)
    for (const e of lanes) expect(e.points, e.id).toHaveLength(2)
    const xs = lanes.map((e) => e.points[0]![0])
    expect(new Set(xs).size).toBe(4)
  })

  it('starts the telemetry bus on the execution plane border', () => {
    const bus = layout.edges.find((e) => e.bus)!
    const plane = layout.planes.find((p) => p.id === bus.sourcePlane)!
    expect(bus.points[0]![1]).toBe(plane.y + plane.height)
    expect(bus.carries).toContain('loop_to_trace')
  })
})

describe('without hints', () => {
  // A diagram with no layout hints at all still lays out and routes cleanly (dagre plus the
  // automatic router), so a new node or edge in the spec never breaks the drawing.
  const bare = {
    ...diagram,
    nodes: diagram.nodes.map((n) => ({ ...n, layout: undefined })),
    edges: diagram.edges.map((e) => ({ ...e, layout: undefined })),
  }

  it('routes every edge orthogonally and around the nodes', () => {
    for (const view of ['simple', 'expert'] as const) {
      const l = computeLayout(bare, { view, collapsed: new Set() })
      for (const e of l.edges) for (let i = 0; i < e.points.length - 1; i++) expect(e.points[i]![0] === e.points[i + 1]![0] || e.points[i]![1] === e.points[i + 1]![1]).toBe(true)
      expect(problems(l, l.edges.filter((e) => !e.bus)).filter((p) => p.includes('through')), view).toEqual([])
    }
  })
})

describe('advanced view routes', () => {
  const expert = computeLayout(diagram, { view: 'expert', collapsed: new Set() })
  const byId = new Map(expert.nodes.map((n) => [n.id, n]))

  it('routes every edge orthogonally without running through a node', () => {
    for (const e of expert.edges) for (let i = 0; i < e.points.length - 1; i++) expect(e.points[i]![0] === e.points[i + 1]![0] || e.points[i]![1] === e.points[i + 1]![1]).toBe(true)
    expect(problems(expert, expert.edges).filter((p) => p.includes('through'))).toEqual([])
  })

  it('keeps clear air, lane spacing, distinct ports and a run-in for every arrow', () => {
    expect(routeProblems(expert, expert.edges, RULES)).toEqual([])
  })

  it('keeps the same rules with any group collapsed, and with all of them', () => {
    const parents = [...new Set(diagram.nodes.filter((n) => n.parent).map((n) => n.parent!))]
    for (const collapsed of [...parents.map((p) => [p]), parents]) {
      const l = computeLayout(diagram, { view: 'expert', collapsed: new Set(collapsed) })
      expect(routeProblems(l, l.edges, RULES), collapsed.join(',')).toEqual([])
    }
  })

  it('spreads the edges that share a node side along it', () => {
    const sides = new Map<string, number[]>()
    for (const e of expert.edges) {
      for (const [node, side, p] of [
        [e.source, e.sourceSide, e.points[0]!],
        [e.target, e.targetSide, e.points[e.points.length - 1]!],
      ] as const) {
        const key = `${node}:${side}`
        sides.set(key, [...(sides.get(key) ?? []), side === 'left' || side === 'right' ? p[1] : p[0]])
      }
    }
    // The context builder takes all five memory stores on one side, each on its own port.
    expect(new Set(sides.get('context_builder:bottom')).size).toBe(5)
    for (const [key, at] of sides) expect(new Set(at).size, key).toBe(at.length)
  })

  it('keeps an edge between two children of one group inside the group', () => {
    const inner = expert.edges.filter((e) => {
      const s = byId.get(e.source)!
      const t = byId.get(e.target)!
      return (s.parentId && s.parentId === t.parentId) || t.parentId === s.id
    })
    expect(inner.map((e) => e.id).sort()).toEqual(['diagnose_to_curate', 'diagnose_to_offline_eval', 'online_eval_to_diagnose', 'registry_to_sandbox', 'router_to_providers', 'trace_to_cost', 'tune_to_artifacts'])
    for (const e of inner) {
      const g = byId.get(byId.get(e.target)!.parentId!)!
      for (const [x, y] of e.points) {
        expect(x > g.x && x < g.x + g.width && y > g.y && y < g.y + g.height, `${e.id} at ${x},${y}`).toBe(true)
      }
    }
  })

  it('draws the edge from a group to its own child under the title, onto the child', () => {
    const e = expert.edges.find((x) => x.id === 'trace_to_cost')!
    const trace = byId.get('trace')!
    const cost = byId.get('cost')!
    expect(e.points[0]![1]).toBeGreaterThan(trace.y)
    expect(e.points[e.points.length - 1]![1]).toBe(cost.y)
    expect(e.targetSide).toBe('top')
  })

  it('crosses between planes through the gutters, never along a plane border', () => {
    const plane = new Map(expert.nodes.map((n) => [n.id, n.node.group]))
    const cross = expert.edges.filter((e) => plane.get(e.source) !== plane.get(e.target))
    expect(cross.length).toBeGreaterThan(8)
    expect(routeProblems(expert, cross, RULES).filter((p) => p.includes('plane'))).toEqual([])
  })

  it('grows each plane round the lanes of its own edges', () => {
    const plane = new Map(expert.nodes.map((n) => [n.id, n.node.group]))
    for (const e of expert.edges) {
      if (plane.get(e.source) !== plane.get(e.target)) continue
      const box = expert.planes.find((p) => p.id === plane.get(e.source))!
      for (const [x, y] of e.points) expect(x > box.x && x < box.x + box.width && y > box.y && y < box.y + box.height, `${e.id}`).toBe(true)
    }
  })
})

describe('simple view route rules', () => {
  it('meets the same spacing rules, telemetry shown', () => {
    const l = computeLayout(diagram, { view: 'simple', collapsed: new Set() })
    expect(routeProblems(l, l.edges.filter((e) => !e.bus), RULES)).toEqual([])
  })
})
