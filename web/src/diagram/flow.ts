// Turn a computed layout plus the current turn state into React Flow nodes and edges.
import type { Diagram } from '@/api/types'
import type { TurnState } from '@/state/reducer'
import type { FlowEdgeType } from './FlowEdge'
import type { HarnessFlowNode } from './HarnessNode'
import { BADGE_NODE, type DiagramLayout, type VisibleEdge } from './layout'
import { edgeColor } from './planes'
import type { PlaneFlowNode } from './PlaneNode'
import { memoryBadges, nodeStat, nodeWhy } from './stats'

export interface FlowOptions {
  disabled: ReadonlySet<string>
  onToggle: (id: string) => void
  /** Simple view: draw the optional edges (LLM Ops and telemetry) instead of the bus. */
  showOps?: boolean
  /** A hovered node shows its optional edges. */
  hovered?: string | null
  /** P4: alert chips by node id (the regressions of the latest eval run). */
  alerts?: Record<string, { count: number; text: string; title: string }>
}

/** Which laid-out edges are drawn, given the toggles, the hovered node and the active edge. */
export function drawnEdges(lay: DiagramLayout, opts: Pick<FlowOptions, 'showOps' | 'hovered'>, activeEdge: string | null): VisibleEdge[] {
  const shown = (e: VisibleEdge) =>
    !e.optional ||
    Boolean(opts.showOps) ||
    (opts.hovered != null && (e.source === opts.hovered || e.target === opts.hovered)) ||
    (activeEdge !== null && e.carries.includes(activeEdge))
  const real = lay.edges.filter((e) => !e.bus && shown(e))
  const realIds = new Set(real.map((e) => e.id))
  // A bus stands in for its edges only while none of them is drawn.
  const buses = lay.edges.filter((e) => e.bus && !opts.showOps && !e.carries.some((id) => realIds.has(id)))
  return [...real, ...buses]
}

export function toFlow(
  diagram: Diagram,
  lay: DiagramLayout,
  turn: TurnState,
  opts: FlowOptions,
): { nodes: (HarnessFlowNode | PlaneFlowNode)[]; edges: FlowEdgeType[] } {
  const groups = new Map(diagram.groups.map((g) => [g.id, g]))
  const planeNodes: PlaneFlowNode[] = lay.planes.map((p) => ({
    id: `plane:${p.id}`,
    type: 'plane',
    position: { x: p.x, y: p.y },
    width: p.width,
    height: p.height,
    style: { width: p.width, height: p.height },
    data: { plane: p.id, label: p.label, description: groups.get(p.id)?.description },
    selectable: false,
    draggable: false,
    focusable: false,
    zIndex: -1,
  }))

  const byId = new Map(lay.nodes.map((n) => [n.id, n]))
  // The node that holds control. When it is not drawn (an expert child in the simple view, or
  // inside a collapsed parent), its nearest drawn ancestor stands in for it.
  const parentOf = new Map(diagram.nodes.map((n) => [n.id, n.parent]))
  let current = turn.paused?.nodeId ?? (turn.phase === 'running' ? turn.focus : null)
  while (current && !byId.has(current)) current = parentOf.get(current) ?? null
  const harnessNodes: HarnessFlowNode[] = lay.nodes.map((l) => {
    const run = turn.nodes[l.id]
    const state = run?.state ?? 'idle'
    const parent = l.parentId ? byId.get(l.parentId) : undefined
    // Badges stand in for the stores whenever the stores themselves are not drawn.
    const childIds = diagram.nodes.filter((n) => n.parent === l.id).map((n) => n.id)
    const showBadges = l.id === BADGE_NODE && !l.expanded && childIds.length > 0
    const stat = nodeStat(run)
    // Served as disabled for this session, or switched off here for the next turn.
    const off = l.node.disabled === true ? 'now' : opts.disabled.has(l.id) ? 'next' : null
    const offText = off === 'now' ? ', disabled' : off === 'next' ? ', off next turn' : ''
    const alert = opts.alerts?.[l.id]
    return {
      id: l.id,
      type: 'harness',
      position: parent ? { x: l.x - parent.x, y: l.y - parent.y } : { x: l.x, y: l.y },
      parentId: l.parentId,
      extent: l.parentId ? 'parent' : undefined,
      width: l.width,
      height: l.height,
      style: { width: l.width, height: l.height },
      draggable: false,
      ariaLabel: `${l.node.label}, ${l.node.kind}, ${state}${stat ? `, ${stat}` : ''}${l.node.implemented === false ? ', not implemented yet' : ''}${offText}${alert ? `, ${alert.title}` : ''}. Press Enter to inspect.`,
      data: {
        node: l.node,
        state,
        stat,
        why: nodeWhy(run),
        points: run?.points,
        badges: showBadges ? memoryBadges(diagram, turn, l.id) : undefined,
        expanded: l.expanded,
        hasChildren: l.hasChildren,
        isChild: Boolean(l.parentId),
        current: current === l.id,
        off,
        alert,
        onToggle: opts.onToggle,
      },
    }
  })

  const activeId = turn.activeEdge?.id ?? null
  const edges: FlowEdgeType[] = drawnEdges(lay, opts, activeId).map((e) => {
    const active = activeId !== null && e.carries.includes(activeId)
    const visited = e.carries.some((id) => (turn.edges[id]?.count ?? 0) > 0)
    const end = (id: string) => (byId.has(id) ? id : null)
    return {
      id: e.id,
      type: 'flow',
      source: end(e.source) ?? e.target,
      target: end(e.target) ?? e.source,
      sourceHandle: `s-${e.sourceSide}`,
      targetHandle: `t-${e.targetSide}`,
      zIndex: active ? 10 : 0,
      focusable: false,
      selectable: false,
      data: {
        kind: e.kind,
        label: e.label,
        active,
        visited,
        points: e.points,
        labelAt: e.labelAt,
        color: edgeColor(e.kind, e.sourcePlane, e.reverse),
        bus: e.bus,
      },
    }
  })

  return { nodes: [...planeNodes, ...harnessNodes], edges }
}
