// Deterministic layout of the served diagram. Pure functions, no React: the same diagram,
// view and collapsed set always produce the same coordinates and edge routes.
//
// Rules:
// 1. A node with a `layout` hint in diagram.json is placed exactly there (`layout.expert` in the
//    expert view, where parents grow to hold their children).
// 2. Otherwise the runtime planes (control, context, execution) are columns left to right in
//    `groups[].order`, and the ops plane, which sits outside the runtime, is a row beneath them.
//    Inside a plane dagre lays the nodes out over the plane's own edges plus a chain in walk
//    order, so a column reads top to bottom in the order a request meets it.
// 3. In the expert view an expanded parent becomes a container and its expert children sit
//    inside it: where their own `layout` hints put them (relative to the parent's top-left), or
//    else in a two-column grid. The padding and gaps leave room for an edge lane between them.
// 4. The runtime columns share one top and one bottom; the ops row sits beneath them.
// 5. An edge whose `layout` hint was drawn for these node boxes follows its hint: named sides,
//    bend points (route.ts). Every other edge is routed automatically around the node boxes,
//    with clear air round every node it does not connect and a lane gap between parallel
//    edges. In the expert view the edges inside one plane are routed first and the plane grows
//    to hold their lanes; the edges between planes are routed after, through the gutters.
// 6. In the simple view the edges to and from LLM Ops and the telemetry edges are optional: the
//    renderer hides them by default and draws one bus per runtime plane into each ops node
//    instead, clipped to start at the plane's border.
import { Graph, layout as dagreLayout } from '@dagrejs/dagre'
import type { Diagram, DiagramEdge, DiagramNode, EdgeKind, PlaneId, Side, View } from '@/api/types'
import { autoRoute, clipRouteFrom, hintedRoute, labelSpot, RouteBook, STUB, type LabelSpot, type Obstacle, type Pt, type Rect } from './route'

export const NODE_W = 184
export const NODE_H = 72
export const CHILD_W = 144
export const CHILD_H = 44
/** Inside an open group: padding round the children, and the gaps between them. */
export const GROUP_PAD = 22
export const GROUP_GAP_X = 30
export const GROUP_GAP_Y = 28
/** The group's own title and stat lines, above its children. */
export const PARENT_HEADER = 62
/** Where the title and stat lines end: an edge from a group to its own child starts there. */
const GROUP_TITLE = 42
export const PLANE_PAD = 22
export const PLANE_HEADER = 34
export const PLANE_GAP = 56
/** Clearance the automatic router keeps from a top-level node and from a nested child. */
const PAD_NODE = 16
const PAD_CHILD = 12
/** How close an edge may run along a group border it crosses, or along a plane border. */
const RAIL_GROUP = 12
const RAIL_PLANE = 14
/** Expert view: a plane grows to keep this much air round the lanes of its own edges. */
const PLANE_LANE_MARGIN = 18
/** The straight run out of a nested child before an edge may bend. */
const CHILD_STUB = 12
export const RANK_SEP = 36
/** Planes drawn as a horizontal row under the runtime columns. */
export const ROW_PLANES: ReadonlySet<PlaneId> = new Set<PlaneId>(['ops'])
/** The node whose store badges stand in for its collapsed children. */
export const BADGE_NODE = 'memory'
export const BADGE_ROW_H = 52

export interface ViewOptions {
  view: View
  /** Parents whose expert children are collapsed in the expert view. */
  collapsed: ReadonlySet<string>
}

export interface LaidOutNode {
  id: string
  node: DiagramNode
  /** Absolute top-left. */
  x: number
  y: number
  width: number
  height: number
  /** Set on expert children shown inside their parent; x and y are still absolute. */
  parentId?: string
  /** True for a parent drawn as an open container of its children. */
  expanded: boolean
  /** True when the node has expert children that this view could show. */
  hasChildren: boolean
}

export interface PlaneBox {
  id: PlaneId
  label: string
  x: number
  y: number
  width: number
  height: number
}

export type { Side }

export interface VisibleEdge {
  /** The diagram edge id, or `bus__<plane>__<node>` for a telemetry bus. */
  id: string
  /** The diagram edge; for a bus, the first edge it stands in for. */
  edge: DiagramEdge
  kind: EdgeKind
  label: string
  source: string
  target: string
  sourceSide: Side
  targetSide: Side
  /** Diagram edge ids whose activity this drawn edge shows (itself plus hidden refinements). */
  carries: string[]
  /** The route, absolute diagram coordinates, from the source port to the target port. */
  points: Pt[]
  labelAt: LabelSpot
  /** The plane of the source node: a request edge takes its colour. */
  sourcePlane: PlaneId
  /** A request edge travelling back against walk order (the reply). */
  reverse: boolean
  /** Simple view only: hidden unless telemetry is shown, an end is hovered or it is active. */
  optional: boolean
  /** Simple view only: the one line drawn in place of hidden telemetry edges. */
  bus: boolean
  /** True when the route came from diagram.json hints. */
  hinted: boolean
}

export interface DiagramLayout {
  nodes: LaidOutNode[]
  planes: PlaneBox[]
  edges: VisibleEdge[]
  width: number
  height: number
}

export function childrenOf(diagram: Diagram, parentId: string): DiagramNode[] {
  return diagram.nodes.filter((n) => n.parent === parentId)
}

/** Which nodes are drawn, and which parents are open containers. */
export function visibleNodeIds(diagram: Diagram, opts: ViewOptions): Set<string> {
  const ids = new Set<string>()
  for (const n of diagram.nodes) {
    if (n.view === 'simple') ids.add(n.id)
    else if (opts.view === 'expert' && n.parent && !opts.collapsed.has(n.parent)) ids.add(n.id)
  }
  return ids
}

/** The simple node a (possibly hidden) node rolls up to in this view. */
export function rollupTarget(diagram: Diagram, nodeId: string, visible: ReadonlySet<string>): string | null {
  if (visible.has(nodeId)) return nodeId
  const node = diagram.nodes.find((n) => n.id === nodeId)
  if (node?.parent && visible.has(node.parent)) return node.parent
  return null
}

/** Edges drawn in this view, with the refinement and roll-up rules applied. */
export function visibleEdges(
  diagram: Diagram,
  visible: ReadonlySet<string>,
): { edge: DiagramEdge; carries: string[] }[] {
  const drawn = diagram.edges.filter((e) => visible.has(e.from) && visible.has(e.to))
  const drawnIds = new Set(drawn.map((e) => e.id))
  // "In expert view the renderer hides a simple edge once any edge refines it."
  const refined = new Set(
    drawn.filter((e) => e.refines && drawnIds.has(e.id)).map((e) => e.refines as string),
  )
  const out: { edge: DiagramEdge; carries: string[] }[] = []
  for (const e of drawn) {
    if (refined.has(e.id)) continue
    const carries = [e.id]
    // Hidden refinements show their activity on the simple edge they detail.
    for (const r of diagram.edges) {
      if (r.refines === e.id && !drawnIds.has(r.id)) carries.push(r.id)
    }
    out.push({ edge: e, carries })
  }
  return out
}

interface Box {
  width: number
  height: number
}

/**
 * Where each shown child sits inside its open parent, relative to the parent's top-left: its
 * own layout hint, or the next cell of a two-column grid.
 */
function childSlots(kids: DiagramNode[]): Map<string, Box & { x: number; y: number }> {
  const cols = kids.length === 1 ? 1 : 2
  const out = new Map<string, Box & { x: number; y: number }>()
  kids.forEach((kid, i) => {
    const col = i % cols
    const row = Math.floor(i / cols)
    const at = kid.layout ?? { x: GROUP_PAD + col * (CHILD_W + GROUP_GAP_X), y: PARENT_HEADER + row * (CHILD_H + GROUP_GAP_Y) }
    out.set(kid.id, { x: at.x, y: at.y, width: CHILD_W, height: CHILD_H })
  })
  return out
}

function sizeOf(diagram: Diagram, node: DiagramNode, visible: ReadonlySet<string>): Box & { expanded: boolean } {
  const kids = childrenOf(diagram, node.id).filter((c) => visible.has(c.id))
  if (kids.length === 0) {
    const badges = node.id === BADGE_NODE && childrenOf(diagram, node.id).length > 0
    return { width: NODE_W, height: NODE_H + (badges ? BADGE_ROW_H : 0), expanded: false }
  }
  const slots = [...childSlots(kids).values()]
  const width = Math.max(NODE_W, ...slots.map((c) => c.x + c.width + GROUP_PAD))
  const height = Math.max(NODE_H, ...slots.map((c) => c.y + c.height + GROUP_PAD))
  return { width, height, expanded: true }
}

function layoutPlane(
  diagram: Diagram,
  members: DiagramNode[],
  sizes: Map<string, Box>,
  rankdir: 'TB' | 'LR',
): Map<string, { x: number; y: number }> {
  const walk = new Map(diagram.walk_order.map((id, i) => [id, i]))
  const rank = (id: string) => walk.get(id) ?? diagram.nodes.findIndex((n) => n.id === id) + 100
  const ordered = [...members].sort((a, b) => rank(a.id) - rank(b.id))
  const ids = new Set(members.map((m) => m.id))
  const g = new Graph({ multigraph: false })
  g.setGraph({ rankdir, nodesep: 30, ranksep: RANK_SEP, marginx: 0, marginy: 0 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const m of ordered) {
    const box = sizes.get(m.id)!
    g.setNode(m.id, { width: box.width, height: box.height })
  }
  // A chain in walk order gives each plane one reading direction.
  for (let i = 1; i < ordered.length; i++) g.setEdge(ordered[i - 1]!.id, ordered[i]!.id, { weight: 2 })
  for (const e of diagram.edges) {
    if (e.view !== 'simple' || !ids.has(e.from) || !ids.has(e.to) || e.from === e.to) continue
    // Orient by walk order so cycles (loop and model layer) do not flip the column.
    const [a, b] = rank(e.from) <= rank(e.to) ? [e.from, e.to] : [e.to, e.from]
    if (!g.hasEdge(a, b)) g.setEdge(a, b, { weight: 1 })
  }
  dagreLayout(g)
  const out = new Map<string, { x: number; y: number }>()
  for (const m of members) {
    const p = g.node(m.id) as { x: number; y: number }
    const box = sizes.get(m.id)!
    out.set(m.id, { x: p.x - box.width / 2, y: p.y - box.height / 2 })
  }
  return out
}

function hintPos(n: DiagramNode, view: View): { x: number; y: number } | undefined {
  if (!n.layout) return undefined
  return view === 'expert' && n.layout.expert ? n.layout.expert : n.layout
}

export function computeLayout(diagram: Diagram, opts: ViewOptions): DiagramLayout {
  const visible = visibleNodeIds(diagram, opts)
  const planes = [...diagram.groups].sort((a, b) => a.order - b.order)
  const tops = diagram.nodes.filter((n) => n.view === 'simple')
  const sizes = new Map<string, Box & { expanded: boolean }>()
  for (const n of tops) sizes.set(n.id, sizeOf(diagram, n, visible))

  const laid = new Map<string, LaidOutNode>()
  const placePlane = (plane: (typeof planes)[number], originX: number, originY: number, rankdir: 'TB' | 'LR') => {
    const members = tops.filter((n) => n.group === plane.id)
    if (members.length === 0) return { width: 0, height: 0 }
    const local = layoutPlane(diagram, members, sizes, rankdir)
    let maxRight = 0
    let maxBottom = 0
    for (const m of members) {
      const p = local.get(m.id)!
      const box = sizes.get(m.id)!
      const hint = hintPos(m, opts.view)
      maxRight = Math.max(maxRight, p.x + box.width)
      maxBottom = Math.max(maxBottom, p.y + box.height)
      laid.set(m.id, {
        id: m.id,
        node: m,
        x: hint ? hint.x : originX + PLANE_PAD + p.x,
        y: hint ? hint.y : originY + PLANE_HEADER + PLANE_PAD + p.y,
        width: box.width,
        height: box.height,
        expanded: box.expanded,
        hasChildren: opts.view === 'expert' && childrenOf(diagram, m.id).length > 0,
      })
    }
    return { width: maxRight + 2 * PLANE_PAD, height: maxBottom + 2 * PLANE_PAD + PLANE_HEADER }
  }

  let cursorX = 0
  let columnsBottom = 0
  for (const plane of planes.filter((p) => !ROW_PLANES.has(p.id))) {
    const box = placePlane(plane, cursorX, 0, 'TB')
    if (box.width === 0) continue
    cursorX += box.width + PLANE_GAP
    columnsBottom = Math.max(columnsBottom, box.height)
  }
  const columnsWidth = Math.max(0, cursorX - PLANE_GAP)
  let rowY = columnsBottom + PLANE_GAP
  for (const plane of planes.filter((p) => ROW_PLANES.has(p.id))) {
    // Measure first, then centre the row under the columns.
    const probe = layoutPlane(diagram, tops.filter((n) => n.group === plane.id), sizes, 'LR')
    let w = 0
    for (const [id, p] of probe) w = Math.max(w, p.x + sizes.get(id)!.width)
    const rowWidth = w + 2 * PLANE_PAD
    const box = placePlane(plane, Math.max(0, (columnsWidth - rowWidth) / 2), rowY, 'LR')
    rowY += box.height + PLANE_GAP
  }

  // Expert children inside their open parent.
  for (const parent of [...laid.values()]) {
    if (!parent.expanded) continue
    const kids = childrenOf(diagram, parent.id).filter((c) => visible.has(c.id))
    for (const [id, slot] of childSlots(kids)) {
      laid.set(id, {
        id,
        node: kids.find((k) => k.id === id)!,
        x: parent.x + slot.x,
        y: parent.y + slot.y,
        width: slot.width,
        height: slot.height,
        parentId: parent.id,
        expanded: false,
        hasChildren: false,
      })
    }
  }

  // Plane boxes are the bounding boxes of their members (layout hints included); the runtime
  // columns then share one top and one bottom so they read as one band.
  const planeBoxes: PlaneBox[] = []
  for (const plane of planes) {
    const members = [...laid.values()].filter((n) => n.node.group === plane.id && !n.parentId)
    if (members.length === 0) continue
    const minX = Math.min(...members.map((m) => m.x))
    const minY = Math.min(...members.map((m) => m.y))
    const maxX = Math.max(...members.map((m) => m.x + m.width))
    const maxY = Math.max(...members.map((m) => m.y + m.height))
    planeBoxes.push({
      id: plane.id,
      label: plane.label,
      x: minX - PLANE_PAD,
      y: minY - PLANE_PAD - PLANE_HEADER,
      width: maxX - minX + 2 * PLANE_PAD,
      height: maxY - minY + 2 * PLANE_PAD + PLANE_HEADER,
    })
  }
  alignColumns(planeBoxes)

  const edges = routeEdges(diagram, opts, visible, laid, planeBoxes)

  // Children follow their parent in the list so a renderer can nest them.
  const ordered: LaidOutNode[] = []
  for (const n of diagram.nodes) {
    const l = laid.get(n.id)
    if (l && !l.parentId) {
      ordered.push(l)
      for (const k of diagram.nodes) {
        const kl = laid.get(k.id)
        if (kl?.parentId === n.id) ordered.push(kl)
      }
    }
  }
  const width = Math.max(0, ...planeBoxes.map((p) => p.x + p.width))
  const height = Math.max(0, ...planeBoxes.map((p) => p.y + p.height))
  const minX = Math.min(0, ...planeBoxes.map((p) => p.x))
  const minY = Math.min(0, ...planeBoxes.map((p) => p.y))
  return { nodes: ordered, planes: planeBoxes, edges, width: width - minX, height: height - minY }
}

/** The runtime columns share one top and one bottom so they read as one band. */
function alignColumns(planeBoxes: PlaneBox[]): void {
  const columns = planeBoxes.filter((p) => !ROW_PLANES.has(p.id))
  if (columns.length < 2) return
  const top = Math.min(...columns.map((p) => p.y))
  const bottom = Math.max(...columns.map((p) => p.y + p.height))
  for (const p of columns) {
    p.y = top
    p.height = bottom - top
  }
}

/** Grow a plane box to hold `points` with PLANE_LANE_MARGIN of air round them. */
function growPlane(p: PlaneBox, points: Pt[]): void {
  if (points.length === 0) return
  const m = PLANE_LANE_MARGIN
  const x1 = Math.min(p.x, ...points.map((q) => q[0] - m))
  const y1 = Math.min(p.y, ...points.map((q) => q[1] - m))
  const x2 = Math.max(p.x + p.width, ...points.map((q) => q[0] + m))
  const y2 = Math.max(p.y + p.height, ...points.map((q) => q[1] + m))
  p.x = x1
  p.y = y1
  p.width = x2 - x1
  p.height = y2 - y1
}

/** True when an edge belongs to LLM Ops or telemetry, which the simple view may hide. */
export function isOpsEdge(diagram: Diagram, edge: DiagramEdge): boolean {
  if (edge.kind === 'telemetry') return true
  const group = (id: string) => diagram.nodes.find((n) => n.id === id)?.group
  const a = group(edge.from)
  const b = group(edge.to)
  return (a !== undefined && ROW_PLANES.has(a)) || (b !== undefined && ROW_PLANES.has(b))
}

function routeEdges(
  diagram: Diagram,
  opts: ViewOptions,
  visible: ReadonlySet<string>,
  laid: Map<string, LaidOutNode>,
  planeBoxes: PlaneBox[],
): VisibleEdge[] {
  const walk = new Map(diagram.walk_order.map((id, i) => [id, i]))
  const byId = new Map(diagram.nodes.map((n) => [n.id, n]))
  // Hints were drawn for the simple-view boxes. In the expert view they still hold for an edge
  // whose two ends sit exactly where they do in the simple view.
  const simpleSize = (n: DiagramNode) => sizeOf(diagram, n, visibleNodeIds(diagram, { view: 'simple', collapsed: new Set() }))
  const hintHolds = (id: string) => {
    const n = byId.get(id)!
    const l = laid.get(id)!
    if (!n.layout || l.parentId) return false
    if (opts.view === 'simple') return true
    const s = simpleSize(n)
    return l.x === n.layout.x && l.y === n.layout.y && l.width === s.width && l.height === s.height
  }

  const book = new RouteBook()
  const drawn = visibleEdges(diagram, visible)
  const routes = new Map<string, { points: Pt[]; sourceSide: Side; targetSide: Side; hinted: boolean }>()
  const rectOf = (id: string): Rect => {
    const l = laid.get(id)!
    return { x: l.x, y: l.y, width: l.width, height: l.height }
  }
  // Hinted edges first, so the automatic ones route around them.
  for (const { edge } of drawn) {
    const h = edge.layout
    if (!h?.handles?.source || !h.handles.target || !hintHolds(edge.from) || !hintHolds(edge.to)) continue
    const points = hintedRoute(rectOf(edge.from), rectOf(edge.to), {
      source: h.handles.source,
      target: h.handles.target,
      waypoints: (h.waypoints ?? []).map(([x, y]) => [x, y] as Pt),
    })
    book.add(edge.id, points)
    const along = (pt: Pt, side: Side) => (side === 'left' || side === 'right' ? pt[1] : pt[0])
    book.takePort(edge.from, h.handles.source, along(points[0]!, h.handles.source))
    book.takePort(edge.to, h.handles.target, along(points[points.length - 1]!, h.handles.target))
    routes.set(edge.id, { points, sourceSide: h.handles.source, targetSide: h.handles.target, hinted: true })
  }
  const ancestors = (id: string) => {
    const out = new Set([id])
    let p = laid.get(id)?.parentId
    while (p) {
      out.add(p)
      p = laid.get(p)?.parentId
    }
    return out
  }
  const topOf = (id: string) => {
    let l = laid.get(id)!
    while (l.parentId) l = laid.get(l.parentId)!
    return l
  }
  const planeOf = (id: string) => topOf(id).node.group
  const expert = opts.view === 'expert'
  const routeAuto = (edge: DiagramEdge, planeRails: boolean) => {
    const from = laid.get(edge.from)!
    const to = laid.get(edge.to)!
    // An edge from a group to one of its own children starts under the group's title, at the
    // end of the title band away from the stat text.
    const ownChild = to.parentId === from.id
    const src: Rect = ownChild
      ? { x: to.x + to.width / 2, y: from.y, width: to.width / 2, height: GROUP_TITLE }
      : rectOf(edge.from)
    const tgt = rectOf(edge.to)
    const sourceStub = from.parentId || ownChild ? CHILD_STUB : undefined
    const targetStub = to.parentId ? CHILD_STUB : undefined
    const skip = new Set([...ancestors(edge.from), ...ancestors(edge.to)])
    const obstacles: Obstacle[] = [...laid.values()]
      .filter((l) => !skip.has(l.id))
      .map((l) => ({ x: l.x, y: l.y, width: l.width, height: l.height, pad: l.parentId ? PAD_CHILD : PAD_NODE }))
    const rails: Obstacle[] = []
    for (const id of skip) {
      const l = laid.get(id)!
      if (!l.expanded || id === edge.from || id === edge.to) continue
      // An edge out of a nested child leaves its open parent by a side, not across the title,
      // and crosses the parent's border rather than running along it.
      obstacles.push({ x: l.x + 1, y: l.y + 1, width: l.width - 2, height: PARENT_HEADER - 8, pad: 0 })
      rails.push({ x: l.x, y: l.y, width: l.width, height: l.height, pad: RAIL_GROUP })
    }
    // Its own two boxes too, so a route never doubles back across the node it leaves.
    if (expert) {
      for (const [r, stub] of [
        [src, sourceStub],
        [tgt, targetStub],
      ] as const) {
        if (!ownChild || r !== src) obstacles.push({ ...r, pad: (stub ?? STUB) - 4 })
      }
    }
    if (planeRails) for (const p of planeBoxes) rails.push({ x: p.x, y: p.y, width: p.width, height: p.height, pad: RAIL_PLANE })
    // The hinted sides still say which way an edge should leave and arrive, where they were
    // drawn for these boxes: a simple edge's sides fit the simple-view boxes, so the expert
    // view, which moves and grows them, keeps only the sides of its own expert edges.
    const sides = !expert || edge.view === 'expert' ? edge.layout?.handles : undefined
    const parent = from.parentId && from.parentId === to.parentId ? laid.get(from.parentId)! : ownChild ? from : undefined
    const within: Rect | undefined = parent
      ? { x: parent.x + 6, y: parent.y + GROUP_TITLE, width: parent.width - 12, height: parent.height - GROUP_TITLE - 6 }
      : undefined
    const r = autoRoute(
      {
        id: edge.id,
        sourceId: edge.from,
        targetId: edge.to,
        src,
        tgt,
        sourceSides: sides?.source ? [sides.source] : ownChild ? ['bottom'] : undefined,
        targetSides: sides?.target ? [sides.target] : undefined,
        obstacles,
        // Ops edges keep to a lane below the runtime planes instead of crossing them.
        avoid: isOpsEdge(diagram, edge) && planeOf(edge.from) !== planeOf(edge.to) ? planeBoxes.filter((p) => !ROW_PLANES.has(p.id)) : undefined,
        rails,
        within,
        sourceStub,
        targetStub,
        // An arrow onto an open group from the side lands on its title band, clear of the
        // ports of the children inside.
        targetBand: to.expanded ? PARENT_HEADER - 12 : undefined,
      },
      book,
    )
    routes.set(edge.id, { ...r, hinted: false })
  }
  // Shorter edges first: they take the direct lanes and the longer ones nest outside them.
  const centre = (id: string) => {
    const l = laid.get(id)!
    return [l.x + l.width / 2, l.y + l.height / 2]
  }
  const span = (e: DiagramEdge) => {
    const [ax, ay] = centre(e.from)
    const [bx, by] = centre(e.to)
    return Math.abs(ax! - bx!) + Math.abs(ay! - by!)
  }
  const pending = drawn
    .filter(({ edge }) => !routes.has(edge.id))
    .map(({ edge }) => edge)
    .sort((a, b) => (expert ? span(a) - span(b) : 0))
  if (expert) {
    // Edges inside one plane first; the plane then grows to hold their lanes, and the edges
    // between planes route through the gutters that are left.
    const inner = pending.filter((e) => planeOf(e.from) === planeOf(e.to))
    for (const e of inner) routeAuto(e, false)
    for (const p of planeBoxes) {
      const pts = inner.filter((e) => planeOf(e.from) === p.id).flatMap((e) => routes.get(e.id)!.points)
      growPlane(p, pts)
    }
    alignColumns(planeBoxes)
    for (const e of pending) if (!routes.has(e.id)) routeAuto(e, true)
  } else {
    for (const e of pending) routeAuto(e, false)
  }

  const simple = opts.view === 'simple'
  const out: VisibleEdge[] = drawn.map(({ edge, carries }) => {
    const r = routes.get(edge.id)!
    const from = byId.get(edge.from)!
    const reverse = edge.kind === 'request' && (walk.get(edge.to) ?? 0) < (walk.get(edge.from) ?? 0)
    return {
      id: edge.id,
      edge,
      kind: edge.kind,
      label: edge.label,
      source: edge.from,
      target: edge.to,
      sourceSide: r.sourceSide,
      targetSide: r.targetSide,
      carries,
      points: r.points,
      labelAt: labelSpot(r.points, edge.layout?.label),
      sourcePlane: from.group,
      reverse,
      optional: simple && isOpsEdge(diagram, edge),
      bus: false,
      hinted: r.hinted,
    }
  })

  if (simple) {
    // One bus per (runtime plane, ops node) for the hidden edges that cross into LLM Ops.
    const buses = new Map<string, VisibleEdge>()
    for (const e of out) {
      if (!e.optional) continue
      const to = byId.get(e.target)!
      if (ROW_PLANES.has(e.sourcePlane) || !ROW_PLANES.has(to.group)) continue
      const id = `bus__${e.sourcePlane}__${e.target}`
      const prev = buses.get(id)
      if (prev) {
        prev.carries.push(...e.carries)
        continue
      }
      const box = planeBoxes.find((p) => p.id === e.sourcePlane)
      const points = box ? clipRouteFrom(e.points, box) : e.points
      buses.set(id, {
        ...e,
        id,
        kind: 'telemetry',
        label: 'telemetry',
        carries: [...e.carries],
        points,
        labelAt: labelSpot(points, e.edge.layout?.label),
        reverse: false,
        optional: false,
        bus: true,
      })
    }
    out.push(...buses.values())
  }
  return out
}
