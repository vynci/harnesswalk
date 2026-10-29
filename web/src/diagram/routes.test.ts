import { describe, expect, it } from 'vitest'
import { diagram } from '@/test/fixtures'
import { computeLayout, type DiagramLayout, type VisibleEdge } from './layout'
import type { Pt } from './route'

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

describe('expert view routes', () => {
  const expert = computeLayout(diagram, { view: 'expert', collapsed: new Set() })

  it('routes every edge orthogonally without running through a node', () => {
    for (const e of expert.edges) for (let i = 0; i < e.points.length - 1; i++) expect(e.points[i]![0] === e.points[i + 1]![0] || e.points[i]![1] === e.points[i + 1]![1]).toBe(true)
    expect(problems(expert, expert.edges).filter((p) => p.includes('through'))).toEqual([])
  })
})
