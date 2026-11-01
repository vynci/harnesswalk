// Geometry checks on drawn routes, shared by the layout tests: clearance from nodes an edge
// does not connect, spacing between parallel lanes, distinct ports per side, a straight run-in
// for the arrowhead, and group-internal edges kept inside their group.
import type { DiagramLayout, LaidOutNode, VisibleEdge } from '@/diagram/layout'
import type { Pt } from '@/diagram/route'

interface Box {
  x: number
  y: number
  width: number
  height: number
}

export interface Seg {
  edge: string
  a: Pt
  b: Pt
}

export function segmentsOf(edges: VisibleEdge[]): Seg[] {
  const out: Seg[] = []
  for (const e of edges) for (let i = 0; i < e.points.length - 1; i++) out.push({ edge: e.id, a: e.points[i]!, b: e.points[i + 1]! })
  return out
}

const isHorizontal = (s: Seg) => s.a[1] === s.b[1]
const span = (s: Seg): [number, number] => (isHorizontal(s) ? [Math.min(s.a[0], s.b[0]), Math.max(s.a[0], s.b[0])] : [Math.min(s.a[1], s.b[1]), Math.max(s.a[1], s.b[1])])
const coord = (s: Seg) => (isHorizontal(s) ? s.a[1] : s.a[0])

/** Euclidean distance between an axis-aligned segment and a box (0 when it touches or enters). */
export function segBoxDistance(s: Seg, r: Box): number {
  const x1 = Math.min(s.a[0], s.b[0])
  const x2 = Math.max(s.a[0], s.b[0])
  const y1 = Math.min(s.a[1], s.b[1])
  const y2 = Math.max(s.a[1], s.b[1])
  const dx = Math.max(r.x - x2, x1 - (r.x + r.width), 0)
  const dy = Math.max(r.y - y2, y1 - (r.y + r.height), 0)
  return Math.hypot(dx, dy)
}

/** A segment running parallel to one of the box's borders, closer than `gap`, over a shared span. */
function alongBorder(s: Seg, r: Box, gap: number): boolean {
  const [lo, hi] = span(s)
  const c = coord(s)
  const lines = isHorizontal(s) ? [r.y, r.y + r.height] : [r.x, r.x + r.width]
  const [blo, bhi] = isHorizontal(s) ? [r.x, r.x + r.width] : [r.y, r.y + r.height]
  if (Math.min(hi, bhi) - Math.max(lo, blo) <= 0) return false
  return lines.some((l) => Math.abs(l - c) < gap)
}

export interface RouteRules {
  /** Clearance from any node the edge does not connect. */
  clear: number
  /** Distance between parallel lanes of different edges. */
  lane: number
  /** Distance between two ports on one side of a node. */
  port: number
  /** Length of the straight last segment the arrowhead sits on. */
  runIn: number
  /** Margin from a plane border a route keeps when running along it. */
  plane: number
}

export function routeProblems(layout: DiagramLayout, drawn: VisibleEdge[], rules: RouteRules): string[] {
  const out: string[] = []
  const byId = new Map(layout.nodes.map((n) => [n.id, n]))
  const chain = (id: string) => {
    const s: LaidOutNode[] = []
    for (let n = byId.get(id); n; n = n.parentId ? byId.get(n.parentId) : undefined) s.push(n)
    return s
  }
  for (const e of drawn) {
    const ends = new Set([...chain(e.source), ...chain(e.target)].map((n) => n.id))
    const segs = segmentsOf([e])
    for (const n of layout.nodes) {
      if (ends.has(n.id)) continue
      for (const s of segs) {
        const d = segBoxDistance(s, n)
        if (d < rules.clear) {
          out.push(`${e.id} within ${d.toFixed(0)} of ${n.id}`)
          break
        }
      }
    }
    // An open group the edge leaves or enters: crossing its border is fine, running along it is not.
    for (const id of ends) {
      if (id === e.source || id === e.target) continue
      const n = byId.get(id)!
      if (segs.some((s) => alongBorder(s, n, rules.clear))) out.push(`${e.id} along the border of ${id}`)
    }
    for (const p of layout.planes) if (segs.some((s) => alongBorder(s, p, rules.plane))) out.push(`${e.id} along the ${p.id} plane border`)
    const last = segs[segs.length - 1]!
    const runIn = Math.abs(last.b[0] - last.a[0]) + Math.abs(last.b[1] - last.a[1])
    if (runIn < rules.runIn) out.push(`${e.id} run-in ${runIn}`)
    // A group-internal edge stays inside its group, clear of the group's border.
    const src = byId.get(e.source)!
    const tgt = byId.get(e.target)!
    const group = src.parentId && src.parentId === tgt.parentId ? byId.get(src.parentId) : src.id === tgt.parentId ? src : undefined
    if (group) {
      const inset = 6
      const inside = e.points.every((p) => p[0] >= group.x + inset && p[0] <= group.x + group.width - inset && p[1] >= group.y + inset && p[1] <= group.y + group.height - inset)
      if (!inside) out.push(`${e.id} leaves ${group.id}`)
    }
  }
  const segs = segmentsOf(drawn)
  for (let i = 0; i < segs.length; i++) {
    for (let j = i + 1; j < segs.length; j++) {
      const s = segs[i]!
      const t = segs[j]!
      if (s.edge === t.edge || isHorizontal(s) !== isHorizontal(t)) continue
      const gap = Math.abs(coord(s) - coord(t))
      if (gap >= rules.lane) continue
      const [slo, shi] = span(s)
      const [tlo, thi] = span(t)
      if (Math.min(shi, thi) - Math.max(slo, tlo) > 0) out.push(`lanes ${s.edge} / ${t.edge} ${gap} apart`)
    }
  }
  const ports = new Map<string, { edge: string; at: number }[]>()
  const along = (p: Pt, side: string) => (side === 'left' || side === 'right' ? p[1] : p[0])
  for (const e of drawn) {
    for (const [node, side, p] of [
      [e.source, e.sourceSide, e.points[0]!],
      [e.target, e.targetSide, e.points[e.points.length - 1]!],
    ] as const) {
      const key = `${node}:${side}`
      ports.set(key, [...(ports.get(key) ?? []), { edge: e.id, at: along(p, side) }])
    }
  }
  for (const [key, list] of ports) {
    for (let i = 0; i < list.length; i++)
      for (let j = i + 1; j < list.length; j++)
        if (Math.abs(list[i]!.at - list[j]!.at) < rules.port) out.push(`ports ${key} ${list[i]!.edge} / ${list[j]!.edge}`)
  }
  return [...new Set(out)]
}
