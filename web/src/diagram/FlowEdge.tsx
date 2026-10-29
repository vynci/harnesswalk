import { memo, type CSSProperties } from 'react'
import { EdgeLabelRenderer, type Edge, type EdgeProps } from '@xyflow/react'
import type { EdgeKind } from '@/api/types'
import { roundedPath, trimEnd, type LabelSpot, type Pt } from './route'

export interface FlowEdgeData extends Record<string, unknown> {
  kind: EdgeKind
  label: string
  active: boolean
  visited: boolean
  /** The precomputed route (layout.ts), absolute diagram coordinates. */
  points: Pt[]
  labelAt: LabelSpot
  /** CSS colour of the stroke (planes.ts edgeColor). */
  color: string
  bus: boolean
}

export type FlowEdgeType = Edge<FlowEdgeData, 'flow'>

const ARROW = 7

// The label sits beside the line, never on it, so the dot moving along the edge never passes
// under it.
const LABEL_SHIFT: Record<LabelSpot['side'], string> = {
  above: 'translate(-50%, calc(-100% - 5px))',
  below: 'translate(-50%, 5px)',
  left: 'translate(calc(-100% - 7px), -50%)',
  right: 'translate(7px, -50%)',
}

function FlowEdgeImpl({ id, data }: EdgeProps<FlowEdgeType>) {
  const d = data!
  const { points, tip, dir } = trimEnd(d.points, ARROW - 1)
  const path = roundedPath(points)
  const full = roundedPath(d.points)
  // Arrowhead: a small triangle whose point is the port.
  const [tx, ty] = tip
  const [dx, dy] = dir
  const back: Pt = [tx - dx * ARROW, ty - dy * ARROW]
  const arrow = `M ${tx} ${ty} L ${back[0] - dy * 4} ${back[1] + dx * 4} L ${back[0] + dy * 4} ${back[1] - dx * 4} Z`
  const state = d.active ? 'active' : d.visited ? 'visited' : 'idle'
  return (
    <>
      <g
        className="hw-edge"
        data-kind={d.kind}
        data-state={state}
        data-bus={d.bus ? 'true' : undefined}
        style={{ '--edge-color': d.color } as CSSProperties}
        data-testid={`edge-${id}`}
      >
        <path id={id} d={path} className="hw-edge-path" data-active={d.active ? 'true' : 'false'} data-visited={d.visited && !d.active ? 'true' : 'false'} />
        <path d={arrow} className="hw-edge-arrow" />
        {d.active && (
          <circle r={4} className="hw-travel-dot">
            <animateMotion dur="0.9s" repeatCount="indefinite" path={full} />
          </circle>
        )}
      </g>
      <path d={full} fill="none" strokeOpacity={0} strokeWidth={12} className="react-flow__edge-interaction">
        <title>{d.label}</title>
      </path>
      {d.active && (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none absolute rounded bg-background/95 px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap text-foreground shadow-sm ring-1 ring-border"
            style={{ transform: `translate(${d.labelAt.x}px, ${d.labelAt.y}px) ${LABEL_SHIFT[d.labelAt.side]}` }}
            data-testid={`edge-label-${id}`}
          >
            {d.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

export const FlowEdge = memo(FlowEdgeImpl)
