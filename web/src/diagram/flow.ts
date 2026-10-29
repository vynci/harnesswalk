// Turn a computed layout plus the current turn state into React Flow nodes and edges.
import { MarkerType } from '@xyflow/react'
import type { Diagram } from '@/api/types'
import type { TurnState } from '@/state/reducer'
import type { FlowEdgeType } from './FlowEdge'
import type { HarnessFlowNode } from './HarnessNode'
import { BADGE_NODE, type DiagramLayout } from './layout'
import type { PlaneFlowNode } from './PlaneNode'
import { memoryBadges, nodeStat } from './stats'

export interface FlowOptions {
  disabled: ReadonlySet<string>
  onToggle: (id: string) => void
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
      ariaLabel: `${l.node.label}, ${state}${stat ? `, ${stat}` : ''}${l.node.implemented === false ? ', not implemented yet' : ''}${offText}. Press Enter to inspect.`,
      data: {
        node: l.node,
        state,
        stat,
        badges: showBadges ? memoryBadges(diagram, turn, l.id) : undefined,
        expanded: l.expanded,
        hasChildren: l.hasChildren,
        isChild: Boolean(l.parentId),
        current: current === l.id,
        off,
        onToggle: opts.onToggle,
      },
    }
  })

  const pairs = new Set(lay.edges.map((e) => `${e.source}>${e.target}`))
  const edges: FlowEdgeType[] = lay.edges.map((e) => {
    const active = turn.activeEdge ? e.carries.includes(turn.activeEdge.id) : false
    const visited = e.carries.some((id) => (turn.edges[id]?.count ?? 0) > 0)
    const twin = pairs.has(`${e.target}>${e.source}`)
    const color = active ? 'var(--state-active)' : visited ? 'var(--state-done)' : 'var(--xy-edge-stroke)'
    return {
      id: e.id,
      type: 'flow',
      source: e.source,
      target: e.target,
      sourceHandle: `s-${e.sourceSide}`,
      targetHandle: `t-${e.targetSide}`,
      zIndex: active ? 10 : 0,
      focusable: false,
      selectable: false,
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color },
      data: {
        kind: e.edge.kind,
        label: e.edge.label,
        active,
        visited,
        shift: twin ? (e.source < e.target ? -6 : 6) : 0,
      },
    }
  })

  return { nodes: [...planeNodes, ...harnessNodes], edges }
}
