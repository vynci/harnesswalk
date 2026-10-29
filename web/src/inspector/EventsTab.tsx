import { useMemo } from 'react'
import type { HarnessEvent, NodeKind } from '@/api/types'
import { Code } from '@/components/code'
import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceDescription,
  TraceIcon,
  TraceLine,
  TraceTitle,
} from '@/components/ui/trace'
import { KIND_ICON } from '@/diagram/planes'
import { useApp } from '@/state/store'
import { traceItems, type TraceItem } from './trace'

function Line({ item, kind }: { item: TraceItem; kind: NodeKind }) {
  const Icon = KIND_ICON[kind]
  return (
    <TraceLine status={item.status} className="text-xs" data-type={item.event.type} data-status={item.status}>
      <TraceIcon className="text-(--kind)" data-kind={kind}>
        <Icon className="size-3.5" />
      </TraceIcon>
      <TraceTitle>{item.title}</TraceTitle>
      <TraceDescription>{item.description}</TraceDescription>
    </TraceLine>
  )
}

/** A node's events in this turn as a shadcn Trace, each line opening to its summary JSON. */
export function EventsTab({ events, nodeId, turnOver }: { events: HarnessEvent[]; nodeId: string; turnOver: boolean }) {
  const diagram = useApp((s) => s.diagram)
  const items = useMemo(() => {
    const byId = new Map(diagram?.nodes.map((n) => [n.id, n]) ?? [])
    return traceItems(events, { self: nodeId, label: (id) => byId.get(id)?.label ?? id, turnOver }).map((item) => ({
      item,
      kind: byId.get(item.event.node_id ?? nodeId)?.kind ?? 'code',
    }))
  }, [events, nodeId, turnOver, diagram])
  if (events.length === 0) return <p className="text-xs text-muted-foreground">No events for this node in this turn.</p>
  return (
    <Trace aria-label="Node events" role="list" data-testid="node-events">
      {items.map(({ item, kind }) =>
        item.detail ? (
          <TraceCollapsible key={item.event.seq} role="listitem">
            <TraceCollapsibleTrigger>
              <Line item={item} kind={kind} />
            </TraceCollapsibleTrigger>
            <TraceCollapsibleContent>
              <Code code={JSON.stringify(item.detail, null, 2)} language="json" size="sm" wrap label={`Event ${item.event.seq}`} contentClassName="max-h-64 text-[11px]" />
            </TraceCollapsibleContent>
          </TraceCollapsible>
        ) : (
          <div key={item.event.seq} role="listitem">
            <Line item={item} kind={kind} />
          </div>
        ),
      )}
    </Trace>
  )
}
