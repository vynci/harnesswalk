import { memo } from 'react'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import { ChevronDown, ChevronRight, CircleAlert, CircleCheck, CircleDashed, CirclePause, Loader, MinusCircle } from 'lucide-react'
import type { DiagramNode } from '@/api/types'
import { cn } from '@/lib/utils'
import type { NodeState } from '@/state/reducer'
import { planeStyle } from './planes'
import type { StoreBadge } from './stats'

export interface HarnessNodeData extends Record<string, unknown> {
  node: DiagramNode
  state: NodeState
  stat?: string
  badges?: StoreBadge[]
  expanded: boolean
  hasChildren: boolean
  isChild: boolean
  current: boolean
  off: boolean
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
      return <CircleDashed className={cn(cls, 'text-muted-foreground/60')} aria-hidden />
  }
}

function HarnessNodeImpl({ data }: NodeProps<HarnessFlowNode>) {
  const { node, state, stat, badges, expanded, hasChildren, isChild, current, off } = data
  const notYet = node.implemented === false
  return (
    <div
      className={cn(
        'hw-node relative flex h-full w-full flex-col rounded-[10px] px-3 py-2 text-left',
        isChild && 'px-2 py-1.5',
      )}
      style={planeStyle(node.group)}
      data-testid={`node-${node.id}`}
      data-state={state}
      data-implemented={notYet ? 'false' : 'true'}
      data-current={current ? 'true' : 'false'}
    >
      {SIDES.map(([pos, side]) => (
        <Handle key={`t-${side}`} id={`t-${side}`} type="target" position={pos} className="!h-1 !w-1 !min-w-0 !border-0 !bg-transparent" isConnectable={false} />
      ))}
      {SIDES.map(([pos, side]) => (
        <Handle key={`s-${side}`} id={`s-${side}`} type="source" position={pos} className="!h-1 !w-1 !min-w-0 !border-0 !bg-transparent" isConnectable={false} />
      ))}
      <div className="flex items-center gap-1.5">
        <StateIcon state={state} />
        <span className={cn('truncate font-medium', isChild ? 'text-xs' : 'text-sm')}>{node.label}</span>
        <span className="ml-auto flex items-center gap-1">
          {off && (
            <span className="rounded border border-current px-1 text-[10px] leading-4 text-state-error" title="Disabled for the next turn">
              off
            </span>
          )}
          {hasChildren && data.onToggle && (
            <button
              type="button"
              className="nodrag nopan rounded p-0.5 text-muted-foreground hover:bg-black/5 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring dark:hover:bg-white/10"
              aria-label={expanded ? `Collapse ${node.label}` : `Expand ${node.label}`}
              aria-expanded={expanded}
              onClick={(e) => {
                e.stopPropagation()
                data.onToggle?.(node.id)
              }}
              onKeyDown={(e) => e.stopPropagation()}
            >
              {expanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
            </button>
          )}
        </span>
      </div>
      <div className={cn('mt-0.5 flex items-center gap-1 truncate text-muted-foreground', isChild ? 'text-[10px] leading-3' : 'text-[11px] leading-4')} data-testid={`stat-${node.id}`}>
        {notYet && (
          <span className="shrink-0 rounded border border-dashed border-current px-1 text-[10px] leading-3.5" title="Not implemented yet">
            not yet
          </span>
        )}
        <span className="truncate">{stat ?? (notYet || isChild ? '' : node.kind)}</span>
      </div>
      {badges && badges.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1" aria-label="Memory stores">
          {badges.map((b) => (
            <span
              key={b.id}
              className="hw-store-badge rounded border border-[var(--plane-stroke)]/40 px-1 text-[10px] leading-4 text-muted-foreground"
              data-testid={`badge-${b.id}`}
              data-lit={b.state !== 'idle' ? 'true' : 'false'}
              data-state={b.state}
              title={`${b.label}: ${b.state}`}
            >
              {b.label}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

export const HarnessNode = memo(HarnessNodeImpl)
