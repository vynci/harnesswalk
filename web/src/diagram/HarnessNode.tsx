import { memo } from 'react'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { ChevronDown, ChevronRight, CircleAlert, CircleCheck, CircleDashed, CirclePause, Loader, MinusCircle } from 'lucide-react'
import type { DiagramNode } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type { NodeState } from '@/state/reducer'
import { KIND_HINT, KIND_ICON, planeStyle } from './planes'
import type { StoreBadge } from './stats'

export interface HarnessNodeData extends Record<string, unknown> {
  node: DiagramNode
  state: NodeState
  stat?: string
  /** The decision's plain-words reason (`why`), shown on hover over the stat. */
  why?: string
  /** Point events emitted this turn, by type. */
  points?: Record<string, number>
  badges?: StoreBadge[]
  expanded: boolean
  hasChildren: boolean
  isChild: boolean
  current: boolean
  /** `now`: the API serves the node disabled; `next`: switched off here for the next turn. */
  off: 'now' | 'next' | null
  onToggle?: (id: string) => void
}

export type HarnessFlowNode = Node<HarnessNodeData, 'harness'>

const SIDES: [Position, string][] = [
  [Position.Top, 'top'],
  [Position.Right, 'right'],
  [Position.Bottom, 'bottom'],
  [Position.Left, 'left'],
]

function StateIcon({ state }: { state: NodeState }) {
  const cls = 'size-3.5 shrink-0'
  switch (state) {
    case 'active':
      return <Loader className={cn(cls, 'text-state-active motion-safe:animate-spin')} aria-hidden />
    case 'waiting':
      return <CirclePause className={cn(cls, 'text-state-waiting')} aria-hidden />
    case 'done':
      return <CircleCheck className={cn(cls, 'text-state-done')} aria-hidden />
    case 'error':
      return <CircleAlert className={cn(cls, 'text-state-error')} aria-hidden />
    case 'skipped':
      return <MinusCircle className={cn(cls, 'text-state-skipped')} aria-hidden />
    default:
      return <CircleDashed className={cn(cls, 'text-state-idle')} aria-hidden />
  }
}

function KindChip({ kind, small }: { kind: DiagramNode['kind']; small: boolean }) {
  const Icon = KIND_ICON[kind]
  return (
    <Badge variant="kind" className={cn('rounded-[4px] p-0', small ? 'size-4 [&>svg]:size-2.5' : 'size-[18px] [&>svg]:size-3')} data-kind={kind} title={KIND_HINT[kind]} data-testid="kind-chip">
      <Icon aria-hidden strokeWidth={2.25} />
      <span className="sr-only">{kind}</span>
    </Badge>
  )
}

function HarnessNodeImpl({ data }: NodeProps<HarnessFlowNode>) {
  const { node, state, stat, why, points, badges, expanded, hasChildren, isChild, current, off } = data
  const notYet = node.implemented === false
  return (
    <Card
      className={cn(
        'hw-node relative h-full w-full gap-0 rounded-[10px] py-2 pr-2 pl-3.5 text-left shadow-none',
        isChild && 'py-1.5 pr-1.5 pl-3',
      )}
      style={planeStyle(node.group)}
      data-testid={`node-${node.id}`}
      data-state={state}
      data-implemented={notYet ? 'false' : 'true'}
      data-current={current ? 'true' : 'false'}
      data-node-kind={node.kind}
      data-points={points ? Object.entries(points).map(([k, v]) => `${k}:${v}`).join(' ') : undefined}
    >
      <span className="hw-accent" aria-hidden />
      {SIDES.map(([pos, side]) => (
        <Handle key={`t-${side}`} id={`t-${side}`} type="target" position={pos} className="!h-1 !w-1 !min-w-0 !border-0 !bg-transparent" isConnectable={false} />
      ))}
      {SIDES.map(([pos, side]) => (
        <Handle key={`s-${side}`} id={`s-${side}`} type="source" position={pos} className="!h-1 !w-1 !min-w-0 !border-0 !bg-transparent" isConnectable={false} />
      ))}
      <div className="flex items-center gap-1.5">
        <StateIcon state={state} />
        <CardTitle className={cn('min-w-0 truncate leading-normal font-medium tracking-[-0.005em]', isChild ? 'text-xs' : 'text-[13.5px]')}>{node.label}</CardTitle>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          <KindChip kind={node.kind} small={isChild} />
          {off && (
            <Badge
              variant="outline"
              className="rounded-sm border-current px-1 py-0 text-[10px] leading-4 font-normal text-state-error"
              title={off === 'now' ? 'Disabled for this session' : 'Disabled from the next turn you send'}
              data-testid={`off-${node.id}`}
            >
              {off === 'now' ? 'off' : 'off next turn'}
            </Badge>
          )}
          {hasChildren && data.onToggle && (
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              className="nodrag nopan size-5 text-muted-foreground"
              aria-label={expanded ? `Collapse ${node.label}` : `Expand ${node.label}`}
              aria-expanded={expanded}
              onClick={(e) => {
                e.stopPropagation()
                data.onToggle?.(node.id)
              }}
              onKeyDown={(e) => e.stopPropagation()}
            >
              {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
            </Button>
          )}
        </span>
      </div>
      <CardDescription className={cn('mt-0.5 flex shrink-0 items-center gap-1 truncate', isChild ? 'text-[10px] leading-3' : 'text-[11px] leading-4')} data-testid={`stat-${node.id}`}>
        {notYet && (
          <Badge variant="outline" className="rounded-sm border-dashed border-current px-1 py-0 text-[10px] leading-3.5 font-normal text-muted-foreground" title="Not implemented yet">
            not yet
          </Badge>
        )}
        <span className="truncate" title={why} data-why={why ? 'true' : undefined}>
          {stat ?? (notYet || isChild ? '' : node.kind)}
        </span>
      </CardDescription>
      {badges && badges.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-0.5" role="group" aria-label="Memory stores">
          {badges.map((b) => (
            <Badge
              key={b.id}
              variant="outline"
              className="hw-store-badge rounded-sm border-(--plane-stroke)/40 px-1 py-0 text-[10px] leading-4 font-normal text-muted-foreground"
              data-testid={`badge-${b.id}`}
              // A store that was skipped (a stub, or switched off) did no work: it stays unlit.
              data-lit={b.state !== 'idle' && b.state !== 'skipped' ? 'true' : 'false'}
              data-state={b.state}
              title={`${b.label}: ${b.state}`}
            >
              {b.label}
            </Badge>
          ))}
        </div>
      )}
    </Card>
  )
}

export const HarnessNode = memo(HarnessNodeImpl)
