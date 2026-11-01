// Orthogonal edge routes. Pure functions, no React, deterministic: the same boxes and hints
// always give the same polylines.
//
// Two ways to route an edge:
// - hinted: diagram.json names the sides and the bend points (the simple view). The ports sit
//   where the nearest bend point projects onto the named side, so a lane is written once.
// - automatic: an A* search over a sparse orthogonal grid built from the node boxes, with costs
//   for length, bends, crossing an earlier edge and, steeply, running along one. Used for any
//   edge without hints, which in practice is the expert view.
import type { Side } from '@/api/types'

export type Pt = [number, number]

export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

/** How far a port's first segment runs straight out of the node before it may bend. */
export const STUB = 14
/** Ports keep this far from a node's corners. */
const CORNER = 10

const SIDES: Side[] = ['top', 'right', 'bottom', 'left']

export function isHorizontalSide(side: Side): boolean {
  return side === 'left' || side === 'right'
}

export function outward(side: Side): Pt {
  switch (side) {
    case 'top':
      return [0, -1]
    case 'bottom':
      return [0, 1]
    case 'left':
      return [-1, 0]
    default:
      return [1, 0]
  }
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v))
}

/** The point on `side` of `r` at coordinate `along` (x for top and bottom, y for left and right). */
export function sidePoint(r: Rect, side: Side, along: number): Pt {
  const margin = Math.min(CORNER, (isHorizontalSide(side) ? r.height : r.width) / 4)
  switch (side) {
    case 'top':
      return [clamp(along, r.x + margin, r.x + r.width - margin), r.y]
    case 'bottom':
      return [clamp(along, r.x + margin, r.x + r.width - margin), r.y + r.height]
    case 'left':
      return [r.x, clamp(along, r.y + margin, r.y + r.height - margin)]
    default:
      return [r.x + r.width, clamp(along, r.y + margin, r.y + r.height - margin)]
  }
}

/** A port on `side`, where `toward` projects onto it; the side's centre when it projects off it. */
export function projectPort(r: Rect, side: Side, toward: Pt): Pt {
  const horizontal = isHorizontalSide(side)
  const along = horizontal ? toward[1] : toward[0]
  const lo = horizontal ? r.y : r.x
  const len = horizontal ? r.height : r.width
  const inside = along > lo + 2 && along < lo + len - 2
  return sidePoint(r, side, inside ? along : lo + len / 2)
}

/** Drop repeated points and the middle point of any straight run. */
export function simplify(points: Pt[]): Pt[] {
  const out: Pt[] = []
  for (const p of points) {
    const last = out[out.length - 1]
    if (last && last[0] === p[0] && last[1] === p[1]) continue
    out.push([p[0], p[1]])
    while (out.length >= 3) {
      const [a, b, c] = out.slice(-3) as [Pt, Pt, Pt]
      const collinear = (a[0] === b[0] && b[0] === c[0]) || (a[1] === b[1] && b[1] === c[1])
      if (!collinear) break
      out.splice(out.length - 2, 1)
    }
  }
  return out
}

/**
 * Join `points` with axis-aligned segments. The first segment leaves along the source side's
 * axis and the last one enters along the target side's axis; any other diagonal gets one elbow.
 */
export function orthogonal(points: Pt[], sourceSide: Side, targetSide: Side): Pt[] {
  const out: Pt[] = [points[0]!]
  for (let i = 1; i < points.length; i++) {
    const a = out[out.length - 1]!
    const b = points[i]!
    if (a[0] !== b[0] && a[1] !== b[1]) {
      const last = i === points.length - 1
      let horizontalFirst: boolean
      if (last) horizontalFirst = !isHorizontalSide(targetSide)
      else if (i === 1) horizontalFirst = isHorizontalSide(sourceSide)
      else horizontalFirst = true
      out.push(horizontalFirst ? [b[0], a[1]] : [a[0], b[1]])
    }
    out.push(b)
  }
  return simplify(out)
}

export interface RouteHint {
  source: Side
  target: Side
  waypoints: Pt[]
}

/** The route the hints describe: source port, bend points, target port. */
export function hintedRoute(src: Rect, tgt: Rect, hint: RouteHint): Pt[] {
  const first = hint.waypoints[0] ?? [tgt.x + tgt.width / 2, tgt.y + tgt.height / 2]
  const p0 = projectPort(src, hint.source, first)
  const lastWp = hint.waypoints[hint.waypoints.length - 1] ?? p0
  const pn = projectPort(tgt, hint.target, lastWp)
  return orthogonal([p0, ...hint.waypoints, pn], hint.source, hint.target)
}

export function insideRect(p: Pt, r: Rect): boolean {
  return p[0] > r.x && p[0] < r.x + r.width && p[1] > r.y && p[1] < r.y + r.height
}

/** The route from the point where it leaves `r` onwards (a bus drawn from a plane's border). */
export function clipRouteFrom(points: Pt[], r: Rect): Pt[] {
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i]!
    const b = points[i + 1]!
    if (insideRect(a, r) && !insideRect(b, r)) {
      let p: Pt
      if (a[0] === b[0]) p = [a[0], b[1] > a[1] ? r.y + r.height : r.y]
      else p = [b[0] > a[0] ? r.x + r.width : r.x, a[1]]
      return simplify([p, ...points.slice(i + 1)])
    }
  }
  return points
}

// ---------- Automatic routing ----------

export interface Segment {
  /** Owner, so an edge never counts its own segments against itself. */
  id: string
  a: Pt
  b: Pt
}

const BEND = 26
const OVERLAP = 5000
const NEAR = 400
/** Parallel lanes of different edges keep at least this far apart. */
export const LANE_GAP = 16
const CROSS = 40
const AWAY = 240
const PORT_GAP = 20
const AVOID = 2.5
/** Running along a rail (a group or plane border the edge may cross): a fixed cost plus this per unit. */
const RAIL = 300
const RAIL_PER = 4

export interface Obstacle extends Rect {
  /** Clearance the router keeps from this box. */
  pad: number
}

export interface AutoRouteRequest {
  id: string
  sourceId: string
  targetId: string
  src: Rect
  tgt: Rect
  /** Allowed sides; all four when absent. */
  sourceSides?: Side[]
  targetSides?: Side[]
  obstacles: Obstacle[]
  /** How far each end runs straight out first (shorter for boxes nested in a parent). */
  sourceStub?: number
  targetStub?: number
  /** Ports on the left or right side keep within this many units of the top (an open group's title band). */
  sourceBand?: number
  targetBand?: number
  /** Areas the edge may cross but should not run along: each unit of length inside costs `AVOID` more. */
  avoid?: Rect[]
  /**
   * Borders the edge may cross but must not run along closer than `pad`: the open group it
   * leaves or enters, and the planes. The grid gets lanes at `pad` on both sides of each border.
   */
  rails?: Obstacle[]
  /** A box the whole route stays inside (an edge between two children of one group). */
  within?: Rect
}

/** Remembers routed segments and taken port slots across the edges of one layout. */
export class RouteBook {
  readonly segments: Segment[] = []
  private readonly ports = new Map<string, number[]>()
  private readonly hBuckets = new Map<number, Segment[]>()
  private readonly vBuckets = new Map<number, Segment[]>()

  add(id: string, points: Pt[]): void {
    for (let i = 0; i < points.length - 1; i++) {
      const s: Segment = { id, a: points[i]!, b: points[i + 1]! }
      this.segments.push(s)
      const horizontal = s.a[1] === s.b[1]
      const key = Math.round((horizontal ? s.a[1] : s.a[0]) / LANE_GAP)
      const buckets = horizontal ? this.hBuckets : this.vBuckets
      const list = buckets.get(key) ?? []
      list.push(s)
      buckets.set(key, list)
    }
  }

  /**
   * Coordinates one lane gap either side of the routed segments that overlap [lo, hi] on the
   * other axis: where a new edge can run parallel to an earlier one at the right distance.
   */
  laneLines(vertical: boolean, lo: number, hi: number): number[] {
    const out: number[] = []
    for (const s of this.segments) {
      const isVertical = s.a[0] === s.b[0]
      if (isVertical !== vertical) continue
      const c = vertical ? s.a[0] : s.a[1]
      const slo = vertical ? Math.min(s.a[1], s.b[1]) : Math.min(s.a[0], s.b[0])
      const shi = vertical ? Math.max(s.a[1], s.b[1]) : Math.max(s.a[0], s.b[0])
      if (shi < lo || slo > hi) continue
      out.push(c - LANE_GAP, c + LANE_GAP)
    }
    return out
  }

  /** Record the port an edge took, so later edges on that side keep their distance. */
  takePort(nodeId: string, side: Side, along: number): void {
    const key = `${nodeId}:${side}`
    this.ports.set(key, [...(this.ports.get(key) ?? []), along])
  }

  /**
   * Where a new port on `side` of `r` goes: facing `other` (the middle of their shared span when
   * the two boxes overlap along that side, so the edge can run straight), then nudged in steps
   * of PORT_GAP until it keeps clear of the ports already taken there.
   */
  portAlong(nodeId: string, side: Side, r: Rect, other: Rect): number {
    const horizontal = isHorizontalSide(side)
    const lo = (horizontal ? r.y : r.x) + CORNER
    const hi = (horizontal ? r.y + r.height : r.x + r.width) - CORNER
    const olo = horizontal ? other.y : other.x
    const ohi = horizontal ? other.y + other.height : other.x + other.width
    const shared = [Math.max(lo, olo + CORNER), Math.min(hi, ohi - CORNER)]
    const want = shared[1]! - shared[0]! >= 0 ? (shared[0]! + shared[1]!) / 2 : clamp((olo + ohi) / 2, lo, hi)
    const taken = this.ports.get(`${nodeId}:${side}`) ?? []
    for (let k = 0; k < 24; k++) {
      const step = Math.ceil(k / 2) * PORT_GAP * (k % 2 === 1 ? 1 : -1)
      const c = Math.round(want + step)
      if (c < lo || c > hi) continue
      if (taken.every((t) => Math.abs(t - c) >= PORT_GAP - 0.5)) return c
    }
    // A crowded side: the spot furthest from the ports already taken, nearest `want` on a tie.
    let best = Math.round(want)
    let bestGap = -1
    for (let c = Math.ceil(lo); c <= hi; c++) {
      const gap = Math.min(...taken.map((t) => Math.abs(t - c)))
      if (gap > bestGap + 0.5 || (Math.abs(gap - bestGap) <= 0.5 && Math.abs(c - want) < Math.abs(best - want))) {
        best = c
        bestGap = gap
      }
    }
    return best
  }

  /** Extra cost of a step from p to q (axis-aligned) given what is already routed. */
  stepPenalty(p: Pt, q: Pt): number {
    const horizontal = p[1] === q[1]
    const c = horizontal ? p[1] : p[0]
    const lo = horizontal ? Math.min(p[0], q[0]) : Math.min(p[1], q[1])
    const hi = horizontal ? Math.max(p[0], q[0]) : Math.max(p[1], q[1])
    const buckets = horizontal ? this.hBuckets : this.vBuckets
    let cost = 0
    const key = Math.round(c / LANE_GAP)
    for (let k = key - 1; k <= key + 1; k++) {
      for (const s of buckets.get(k) ?? []) {
        const sc = horizontal ? s.a[1] : s.a[0]
        const slo = horizontal ? Math.min(s.a[0], s.b[0]) : Math.min(s.a[1], s.b[1])
        const shi = horizontal ? Math.max(s.a[0], s.b[0]) : Math.max(s.a[1], s.b[1])
        const overlap = Math.min(hi, shi) - Math.max(lo, slo)
        if (overlap <= 0) continue
        const gap = Math.abs(sc - c)
        if (gap < 0.5) cost += OVERLAP
        else if (gap < LANE_GAP - 0.5) cost += NEAR + overlap * 4
      }
    }
    // Crossing a perpendicular segment at q.
    const perp = horizontal ? this.vBuckets : this.hBuckets
    const qc = horizontal ? q[0] : q[1]
    const qk = Math.round(qc / LANE_GAP)
    for (let k = qk - 1; k <= qk + 1; k++) {
      for (const s of perp.get(k) ?? []) {
        const sc = horizontal ? s.a[0] : s.a[1]
        if (Math.abs(sc - qc) > 0.5) continue
        const along = horizontal ? q[1] : q[0]
        const slo = horizontal ? Math.min(s.a[1], s.b[1]) : Math.min(s.a[0], s.b[0])
        const shi = horizontal ? Math.max(s.a[1], s.b[1]) : Math.max(s.a[0], s.b[0])
        if (along > slo && along < shi) cost += CROSS
      }
    }
    return cost
  }
}

class Heap {
  private readonly f: number[] = []
  private readonly v: number[] = []
  get size(): number {
    return this.v.length
  }
  push(f: number, v: number): void {
    this.f.push(f)
    this.v.push(v)
    let i = this.v.length - 1
    while (i > 0) {
      const p = (i - 1) >> 1
      if (this.f[p]! < this.f[i]! || (this.f[p] === this.f[i] && this.v[p]! <= this.v[i]!)) break
      this.swap(i, p)
      i = p
    }
  }
  pop(): number {
    const top = this.v[0]!
    const lastF = this.f.pop()!
    const lastV = this.v.pop()!
    if (this.v.length > 0) {
      this.f[0] = lastF
      this.v[0] = lastV
      let i = 0
      for (;;) {
        const l = 2 * i + 1
        const r = l + 1
        let m = i
        if (l < this.v.length && this.less(l, m)) m = l
        if (r < this.v.length && this.less(r, m)) m = r
        if (m === i) break
        this.swap(i, m)
        i = m
      }
    }
    return top
  }
  private less(a: number, b: number): boolean {
    return this.f[a]! < this.f[b]! || (this.f[a] === this.f[b] && this.v[a]! < this.v[b]!)
  }
  private swap(a: number, b: number): void {
    ;[this.f[a], this.f[b]] = [this.f[b]!, this.f[a]!]
    ;[this.v[a], this.v[b]] = [this.v[b]!, this.v[a]!]
  }
}

function uniqueSorted(values: number[]): number[] {
  const s = [...new Set(values.map((v) => Math.round(v * 2) / 2))].sort((a, b) => a - b)
  return s
}

function facing(from: Rect, side: Side, to: Rect): boolean {
  const [ox, oy] = outward(side)
  const dx = to.x + to.width / 2 - (from.x + from.width / 2)
  const dy = to.y + to.height / 2 - (from.y + from.height / 2)
  return ox * dx + oy * dy > 0
}

interface End {
  side: Side
  port: Pt
  stub: Pt
  penalty: number
}

function banded(r: Rect, band: number | undefined, side: Side): Rect {
  return band !== undefined && isHorizontalSide(side) ? { ...r, height: Math.min(band, r.height) } : r
}

function ends(book: RouteBook, nodeId: string, box: Rect, other: Rect, sides: Side[], stub: number, band?: number, otherBand?: number): End[] {
  return sides.map((side) => {
    const r = banded(box, band, side)
    const port = sidePoint(r, side, book.portAlong(nodeId, side, r, banded(other, otherBand, side)))
    const [ox, oy] = outward(side)
    return {
      side,
      port,
      stub: [port[0] + ox * stub, port[1] + oy * stub] as Pt,
      penalty: facing(box, side, other) ? 0 : AWAY,
    }
  })
}

/** Route one edge around `obstacles`, avoiding what `book` already holds, and record it. */
export function autoRoute(req: AutoRouteRequest, book: RouteBook): { points: Pt[]; sourceSide: Side; targetSide: Side } {
  const starts = ends(book, req.sourceId, req.src, req.tgt, req.sourceSides ?? SIDES, req.sourceStub ?? STUB, req.sourceBand, req.targetBand)
  const goals = ends(book, req.targetId, req.tgt, req.src, req.targetSides ?? SIDES, req.targetStub ?? STUB, req.targetBand, req.sourceBand)

  const xsRaw: number[] = []
  const ysRaw: number[] = []
  for (const o of req.obstacles) {
    xsRaw.push(o.x - o.pad, o.x + o.width + o.pad, o.x - 2.4 * o.pad, o.x + o.width + 2.4 * o.pad)
    ysRaw.push(o.y - o.pad, o.y + o.height + o.pad, o.y - 2.4 * o.pad, o.y + o.height + 2.4 * o.pad)
  }
  for (const r of req.rails ?? []) {
    for (const k of [-2, -1, 1, 2]) {
      xsRaw.push(r.x + k * r.pad, r.x + r.width + k * r.pad)
      ysRaw.push(r.y + k * r.pad, r.y + r.height + k * r.pad)
    }
  }
  for (const e of [...starts, ...goals]) {
    xsRaw.push(e.stub[0])
    ysRaw.push(e.stub[1])
  }
  const all = [...starts, ...goals].map((e) => e.stub)
  let minX = Math.min(...all.map((p) => p[0]), ...req.obstacles.map((o) => o.x)) - 60
  let maxX = Math.max(...all.map((p) => p[0]), ...req.obstacles.map((o) => o.x + o.width)) + 60
  let minY = Math.min(...all.map((p) => p[1]), ...req.obstacles.map((o) => o.y)) - 60
  let maxY = Math.max(...all.map((p) => p[1]), ...req.obstacles.map((o) => o.y + o.height)) + 60
  if (req.within) {
    minX = Math.max(minX, req.within.x)
    maxX = Math.min(maxX, req.within.x + req.within.width)
    minY = Math.max(minY, req.within.y)
    maxY = Math.min(maxY, req.within.y + req.within.height)
  }
  xsRaw.push(minX, maxX, ...book.laneLines(true, minY, maxY))
  ysRaw.push(minY, maxY, ...book.laneLines(false, minX, maxX))
  const addMidpoints = (v: number[]) => {
    const out = [...v]
    for (let i = 0; i < v.length - 1; i++) if (v[i + 1]! - v[i]! > 20) out.push((v[i]! + v[i + 1]!) / 2)
    return uniqueSorted(out)
  }
  const xs = addMidpoints(uniqueSorted(xsRaw).filter((x) => x >= minX && x <= maxX))
  const ys = addMidpoints(uniqueSorted(ysRaw).filter((y) => y >= minY && y <= maxY))
  const nx = xs.length
  const ny = ys.length
  const xi = new Map(xs.map((x, i) => [x, i]))
  const yi = new Map(ys.map((y, i) => [y, i]))

  const strictlyInside = (x: number, y: number) =>
    req.obstacles.some((o) => x > o.x - o.pad && x < o.x + o.width + o.pad && y > o.y - o.pad && y < o.y + o.height + o.pad)
  const blocked = new Uint8Array(nx * ny)
  for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) blocked[j * nx + i] = strictlyInside(xs[i]!, ys[j]!) ? 1 : 0
  // A step that runs along a rail, closer than its pad, on the inside or the outside.
  const railCost = (x: number, y: number, qx: number, qy: number): number => {
    let cost = 0
    const horizontal = y === qy
    const c = horizontal ? y : x
    const lo = horizontal ? Math.min(x, qx) : Math.min(y, qy)
    const hi = horizontal ? Math.max(x, qx) : Math.max(y, qy)
    for (const r of req.rails ?? []) {
      const [blo, bhi] = horizontal ? [r.x, r.x + r.width] : [r.y, r.y + r.height]
      if (Math.min(hi, bhi) - Math.max(lo, blo) <= 0) continue
      const lines = horizontal ? [r.y, r.y + r.height] : [r.x, r.x + r.width]
      if (lines.some((l) => Math.abs(l - c) < r.pad - 0.5)) cost += RAIL + (hi - lo) * RAIL_PER
    }
    return cost
  }

  const STATES = nx * ny * 2
  const g = new Float64Array(STATES).fill(Infinity)
  const prev = new Int32Array(STATES).fill(-1)
  const heap = new Heap()
  const key = (x: number, y: number) => {
    const i = xi.get(Math.round(x * 2) / 2)
    const j = yi.get(Math.round(y * 2) / 2)
    return i === undefined || j === undefined ? -1 : j * nx + i
  }
  const goalAt = new Map<number, End[]>()
  for (const e of goals) {
    const k = key(e.stub[0], e.stub[1])
    if (k >= 0) goalAt.set(k, [...(goalAt.get(k) ?? []), e])
  }
  const h = (x: number, y: number) => Math.min(...goals.map((e) => Math.abs(e.stub[0] - x) + Math.abs(e.stub[1] - y)))
  const startOf = new Map<number, End>()
  for (const s of starts) {
    const k = key(s.stub[0], s.stub[1])
    if (k < 0) continue
    const dir = isHorizontalSide(s.side) ? 0 : 1
    const st = k * 2 + dir
    const cost = s.penalty + book.stepPenalty(s.port, s.stub)
    if (cost < g[st]!) {
      g[st] = cost
      startOf.set(st, s)
      heap.push(cost + h(s.stub[0], s.stub[1]), st)
    }
  }

  let best = Infinity
  let bestState = -1
  let bestGoal: End | null = null
  while (heap.size > 0) {
    const st = heap.pop()
    const cell = st >> 1
    const dir = st & 1
    const gc = g[st]!
    if (gc >= best) break
    const i = cell % nx
    const j = (cell - i) / nx
    const x = xs[i]!
    const y = ys[j]!
    const gl = goalAt.get(cell)
    if (gl) {
      for (const e of gl) {
        const arrive = isHorizontalSide(e.side) ? 0 : 1
        const total = gc + (arrive === dir ? 0 : BEND) + e.penalty + book.stepPenalty(e.stub, e.port)
        if (total < best) {
          best = total
          bestState = st
          bestGoal = e
        }
      }
    }
    const moves: [number, number, number][] = [
      [i + 1, j, 0],
      [i - 1, j, 0],
      [i, j + 1, 1],
      [i, j - 1, 1],
    ]
    for (const [ni, nj, nd] of moves) {
      if (ni < 0 || nj < 0 || ni >= nx || nj >= ny) continue
      const ncell = nj * nx + ni
      if (blocked[ncell]) continue
      const qx = xs[ni]!
      const qy = ys[nj]!
      if (strictlyInside((x + qx) / 2, (y + qy) / 2)) continue
      const len = Math.abs(qx - x) + Math.abs(qy - y)
      const mid: Pt = [(x + qx) / 2, (y + qy) / 2]
      const soft = req.avoid?.some((r) => insideRect(mid, r)) ? len * AVOID : 0
      const step = len + soft + (nd === dir ? 0 : BEND) + book.stepPenalty([x, y], [qx, qy]) + railCost(x, y, qx, qy)
      const ns = ncell * 2 + nd
      const ng = gc + step
      if (ng < g[ns]!) {
        g[ns] = ng
        prev[ns] = st
        heap.push(ng + h(qx, qy), ns)
      }
    }
  }

  let points: Pt[]
  let sourceSide: Side
  let targetSide: Side
  if (bestState < 0 || !bestGoal) {
    // No way round: fall back to a plain elbow between the facing sides.
    const s = starts.find((e) => e.penalty === 0) ?? starts[0]!
    const t = goals.find((e) => e.penalty === 0) ?? goals[0]!
    points = orthogonal([s.port, s.stub, t.stub, t.port], s.side, t.side)
    sourceSide = s.side
    targetSide = t.side
  } else {
    const cells: Pt[] = []
    let st = bestState
    let first = st
    while (st >= 0) {
      const cell = st >> 1
      const i = cell % nx
      cells.push([xs[i]!, ys[(cell - i) / nx]!])
      first = st
      st = prev[st]!
    }
    cells.reverse()
    const s = startOf.get(first)!
    points = simplify([s.port, ...cells, bestGoal.stub, bestGoal.port])
    sourceSide = s.side
    targetSide = bestGoal.side
  }
  const along = (p: Pt, side: Side) => (isHorizontalSide(side) ? p[1] : p[0])
  book.takePort(req.sourceId, sourceSide, along(points[0]!, sourceSide))
  book.takePort(req.targetId, targetSide, along(points[points.length - 1]!, targetSide))
  book.add(req.id, points)
  return { points, sourceSide, targetSide }
}

// ---------- Drawing helpers ----------

export interface LabelSpot {
  x: number
  y: number
  side: 'above' | 'below' | 'left' | 'right'
}

/** The midpoint of the longest segment, with the label set beside the line rather than on it. */
export function labelSpot(points: Pt[], prefer?: LabelSpot['side']): LabelSpot {
  let best = 0
  let len = -1
  for (let i = 0; i < points.length - 1; i++) {
    const l = Math.abs(points[i + 1]![0] - points[i]![0]) + Math.abs(points[i + 1]![1] - points[i]![1])
    if (l > len) {
      len = l
      best = i
    }
  }
  const a = points[best]!
  const b = points[best + 1] ?? a
  const vertical = a[0] === b[0]
  const side = prefer && (vertical ? prefer === 'left' || prefer === 'right' : prefer === 'above' || prefer === 'below') ? prefer : vertical ? 'right' : 'above'
  return { x: (a[0] + b[0]) / 2, y: (a[1] + b[1]) / 2, side }
}

/** An SVG path through `points` with rounded corners. */
export function roundedPath(points: Pt[], radius = 9): string {
  if (points.length === 0) return ''
  let d = `M ${points[0]![0]} ${points[0]![1]}`
  for (let i = 1; i < points.length - 1; i++) {
    const p = points[i - 1]!
    const c = points[i]!
    const n = points[i + 1]!
    const l1 = Math.abs(c[0] - p[0]) + Math.abs(c[1] - p[1])
    const l2 = Math.abs(n[0] - c[0]) + Math.abs(n[1] - c[1])
    const r = Math.min(radius, l1 / 2, l2 / 2)
    const d1: Pt = [Math.sign(c[0] - p[0]), Math.sign(c[1] - p[1])]
    const d2: Pt = [Math.sign(n[0] - c[0]), Math.sign(n[1] - c[1])]
    d += ` L ${c[0] - d1[0] * r} ${c[1] - d1[1] * r} Q ${c[0]} ${c[1]} ${c[0] + d2[0] * r} ${c[1] + d2[1] * r}`
  }
  const last = points[points.length - 1]!
  d += ` L ${last[0]} ${last[1]}`
  return d
}

/** The route shortened by `by` at its end (room for an arrowhead), and the arrow's direction. */
export function trimEnd(points: Pt[], by: number): { points: Pt[]; tip: Pt; dir: Pt } {
  const tip = points[points.length - 1]!
  const before = points[points.length - 2] ?? tip
  const len = Math.abs(tip[0] - before[0]) + Math.abs(tip[1] - before[1])
  const dir: Pt = len === 0 ? [0, 1] : [Math.sign(tip[0] - before[0]), Math.sign(tip[1] - before[1])]
  const cut = Math.min(by, len)
  const end: Pt = [tip[0] - dir[0] * cut, tip[1] - dir[1] * cut]
  return { points: [...points.slice(0, -1), end], tip, dir }
}
