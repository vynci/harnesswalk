import { memo } from 'react'
import { EdgeLabelRenderer, getSmoothStepPath, Position, type Edge, type EdgeProps } from '@xyflow/react'
import type { EdgeKind } from '@/api/types'

export interface FlowEdgeData extends Record<string, unknown> {
  kind: EdgeKind
  label: string
  active: boolean
  visited: boolean
  /** Perpendicular shift so an edge and its reverse twin do not overlap. */
  shift: number
}

export type FlowEdgeType = Edge<FlowEdgeData, 'flow'>

const DASH: Record<EdgeKind, string | undefined> = {
  request: undefined,
  data: undefined,
  control: '5 4',
  telemetry: '2 4',
  release: undefined,
}

function shifted(x: number, y: number, pos: Position, d: number): [number, number] {
  return pos === Position.Left || pos === Position.Right ? [x, y + d] : [x + d, y]
}

function FlowEdgeImpl(props: EdgeProps<FlowEdgeType>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd } = props
  const d = data!
  const [sx, sy] = shifted(sourceX, sourceY, sourcePosition, d.shift)
  const [tx, ty] = shifted(targetX, targetY, targetPosition, d.shift)
  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX: sx,
    sourceY: sy,
    targetX: tx,
    targetY: ty,
    sourcePosition,
    targetPosition,
    borderRadius: 10,
    offset: 14,
  })
  return (
    <>
      <path
        id={id}
        d={path}
        markerEnd={markerEnd}
        className="react-flow__edge-path hw-edge-path"
        style={{
          strokeDasharray: d.active ? undefined : DASH[d.kind],
          strokeWidth: d.kind === 'release' && !d.active ? 2.5 : undefined,
        }}
        data-active={d.active ? 'true' : 'false'}
        data-visited={d.visited && !d.active ? 'true' : 'false'}
        data-testid={`edge-${id}`}
      />
      <path d={path} fill="none" strokeOpacity={0} strokeWidth={12} className="react-flow__edge-interaction">
        <title>{d.label}</title>
      </path>
      {d.active && (
        <>
          <circle r={4} className="hw-travel-dot" fill="var(--state-active)">
            <animateMotion dur="0.9s" repeatCount="indefinite" path={path} />
          </circle>
          <EdgeLabelRenderer>
            <div
              className="pointer-events-none absolute rounded bg-background/90 px-1.5 py-0.5 text-[10px] font-medium text-foreground shadow-sm ring-1 ring-border"
              style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
            >
              {d.label}
            </div>
          </EdgeLabelRenderer>
        </>
      )}
    </>
  )
}

export const FlowEdge = memo(FlowEdgeImpl)
