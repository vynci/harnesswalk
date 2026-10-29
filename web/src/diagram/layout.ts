// Deterministic layout of the served diagram. Pure functions, no React: the same diagram,
// view and collapsed set always produce the same coordinates.
//
// Rules:
// 1. A node with a `layout` hint in diagram.json is placed exactly there.
// 2. Otherwise the runtime planes (control, context, execution) are columns left to right in
//    `groups[].order`, and the ops plane, which sits outside the runtime, is a row beneath them.
//    Inside a plane dagre lays the nodes out over the plane's own edges plus a chain in walk
//    order, so a column reads top to bottom in the order a request meets it.
// 3. In the expert view an expanded parent becomes a container and its expert children sit
//    inside it in a two-column grid.
// 4. Edges between planes leave and enter by the sides; an edge that skips over a node in its
//    own column bends around the column (downward on the right, upward on the left).
import { Graph, layout as dagreLayout } from '@dagrejs/dagre'
import type { Diagram, DiagramEdge, DiagramNode, PlaneId, View } from '@/api/types'

export const NODE_W = 184
export const NODE_H = 72
export const CHILD_W = 144
export const CHILD_H = 44
export const CHILD_GAP = 10
export const PARENT_HEADER = 64
export const PLANE_PAD = 22
export const PLANE_HEADER = 34
export const PLANE_GAP = 56
export const RANK_SEP = 36
/** Planes drawn as a horizontal row under the runtime columns. */
export const ROW_PLANES: ReadonlySet<PlaneId> = new Set<PlaneId>(['ops'])
/** The node whose store badges stand in for its collapsed children. */
export const BADGE_NODE = 'memory'
export const BADGE_ROW_H = 40

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

export type Side = 'top' | 'right' | 'bottom' | 'left'

export interface VisibleEdge {
  id: string
  edge: DiagramEdge
  source: string
  target: string
  sourceSide: Side
  targetSide: Side
  /** Diagram edge ids whose activity this drawn edge shows (itself plus hidden refinements). */
  carries: string[]
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

function sideBetween(a: LaidOutNode, b: LaidOutNode, others: LaidOutNode[]): [Side, Side] {
  const ax = a.x + a.width / 2
  const ay = a.y + a.height / 2
  const bx = b.x + b.width / 2
  const by = b.y + b.height / 2
  const dx = bx - ax
  const dy = by - ay
  // Side by side: leave and enter horizontally.
  if (Math.abs(dx) > Math.max(a.width, b.width) / 2 && Math.abs(dx) >= Math.abs(dy) / 2) {
    return dx > 0 ? ['right', 'left'] : ['left', 'right']
  }
  if (Math.abs(dx) > Math.max(a.width, b.width) / 2) {
    return dy >= 0 ? ['bottom', 'top'] : ['top', 'bottom']
  }
  // Same column: go straight unless another node sits in between, then bend around it.
  const top = Math.min(a.y + a.height, b.y + b.height)
  const bottom = Math.max(a.y, b.y)
  const blocked = others.some(
    (o) => o !== a && o !== b && !o.parentId && o.y >= top && o.y + o.height <= bottom && o.x < Math.max(a.x + a.width, b.x + b.width) && o.x + o.width > Math.min(a.x, b.x),
  )
  if (blocked) return dy >= 0 ? ['right', 'right'] : ['left', 'left']
  return dy >= 0 ? ['bottom', 'top'] : ['top', 'bottom']
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

function sizeOf(diagram: Diagram, node: DiagramNode, visible: ReadonlySet<string>): Box & { expanded: boolean } {
  const kids = childrenOf(diagram, node.id).filter((c) => visible.has(c.id))
  if (kids.length === 0) {
    const badges = node.id === BADGE_NODE && childrenOf(diagram, node.id).length > 0
    return { width: NODE_W, height: NODE_H + (badges ? BADGE_ROW_H : 0), expanded: false }
  }
  const cols = kids.length === 1 ? 1 : 2
  const rows = Math.ceil(kids.length / cols)
  const width = Math.max(NODE_W, cols * CHILD_W + (cols + 1) * CHILD_GAP)
  const height = PARENT_HEADER + rows * CHILD_H + (rows + 1) * CHILD_GAP - CHILD_GAP / 2
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
      maxRight = Math.max(maxRight, p.x + box.width)
      maxBottom = Math.max(maxBottom, p.y + box.height)
      laid.set(m.id, {
        id: m.id,
        node: m,
        x: m.layout ? m.layout.x : originX + PLANE_PAD + p.x,
        y: m.layout ? m.layout.y : originY + PLANE_HEADER + PLANE_PAD + p.y,
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
    const cols = kids.length === 1 ? 1 : 2
    const innerW = parent.width - (cols + 1) * CHILD_GAP
    const w = cols === 1 ? Math.min(innerW, CHILD_W * 1.4) : innerW / 2
    kids.forEach((kid, i) => {
      const col = i % cols
      const row = Math.floor(i / cols)
      laid.set(kid.id, {
        id: kid.id,
        node: kid,
        x: parent.x + CHILD_GAP + col * (w + CHILD_GAP),
        y: parent.y + PARENT_HEADER + row * (CHILD_H + CHILD_GAP),
        width: w,
        height: CHILD_H,
        parentId: parent.id,
        expanded: false,
        hasChildren: false,
      })
    })
  }

  const planeBoxes: PlaneBox[] = []
  // Plane boxes are the bounding boxes of their members (layout hints included).
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

  const all = [...laid.values()]
  const edges: VisibleEdge[] = visibleEdges(diagram, visible).map(({ edge, carries }) => {
    const a = laid.get(edge.from)!
    const b = laid.get(edge.to)!
    const [sourceSide, targetSide] = sideBetween(a, b, all)
    return { id: edge.id, edge, source: edge.from, target: edge.to, sourceSide, targetSide, carries }
  })

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
  return { nodes: ordered, planes: planeBoxes, edges, width, height }
}
