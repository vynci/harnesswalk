import { useMemo } from 'react'
import { Ban, BookOpen, CircleAlert, ShieldQuestion, Wrench } from 'lucide-react'
import type { HarnessEvent } from '@/api/types'
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
import { formatMs } from '@/diagram/stats'
import { toolCalls, type ToolCallView } from './toolCalls'

function Json({ value }: { value: unknown }) {
  return <Code code={JSON.stringify(value, null, 2)} language="json" size="sm" wrap contentClassName="max-h-56 text-[11px]" label="Tool call details" />
}

function title(call: ToolCallView): string {
  switch (call.status) {
    case 'running':
      return `Running ${call.tool}`
    case 'waiting':
      return `Waiting for approval to run ${call.tool}`
    case 'denied':
      return `Denied ${call.tool}`
    case 'error':
      return `Failed to run ${call.tool}`
    default:
      return `Ran ${call.tool}`
  }
}

function description(call: ToolCallView, turnOver: boolean): string {
  const parts = [
    call.decision ? `policy ${call.decision.replace(/_/g, ' ')}` : null,
    call.approval && call.approval.state !== 'requested' ? `${call.approval.state === 'approve' ? 'approved' : call.approval.state === 'deny' ? 'denied' : 'expired'}${call.approval.decidedBy ? ` by ${call.approval.decidedBy}` : ''}` : null,
    call.status === 'done' || call.status === 'error' || call.status === 'denied' ? (call.outcome ?? (turnOver ? 'not finished' : null)) : null,
    formatMs(call.durationMs) ?? null,
  ]
  return parts.filter(Boolean).join(' · ')
}

function ToolLine({ call, turnOver }: { call: ToolCallView; turnOver: boolean }) {
  // A call still open when the turn is over (a cancel) did not finish.
  const status = call.status === 'error' || (turnOver && call.status === 'running') ? 'error' : call.status === 'running' ? 'running' : call.status === 'waiting' ? 'pending' : undefined
  const Icon = call.status === 'denied' ? Ban : status === 'error' ? CircleAlert : call.status === 'waiting' ? ShieldQuestion : Wrench
  const line = (
    <TraceLine status={status} className="text-xs" data-testid="tool-line" data-tool={call.tool} data-call-status={call.status}>
      <TraceIcon>
        <Icon />
      </TraceIcon>
      <TraceTitle>{turnOver && call.status === 'running' ? `Stopped before ${call.tool} finished` : title(call)}</TraceTitle>
      <TraceDescription>{description(call, turnOver)}</TraceDescription>
    </TraceLine>
  )
  const detail = {
    tool: call.tool,
    call_id: call.callId,
    ...(call.actionClass ? { action_class: call.actionClass } : {}),
    ...(call.args !== undefined ? { args_preview: call.args } : {}),
    ...(call.decision ? { policy: { decision: call.decision, rule: call.rule } } : {}),
    ...(call.approval ? { approval: call.approval } : {}),
    ...(call.outcome ? { outcome: call.outcome, executed: call.executed } : {}),
    ...(call.error ? { error: call.error } : {}),
  }
  if (call.status === 'running' && !turnOver) return line
  return (
    <TraceCollapsible>
      <TraceCollapsibleTrigger aria-label={`${title(call)}: details`}>{line}</TraceCollapsibleTrigger>
      <TraceCollapsibleContent>
        <Json value={detail} />
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}

/** The tool calls of a turn, inline in the chat: shadcn's Tool pattern (Trace plus Code Block). */
export function ToolTrace({ events, turnOver }: { events: HarnessEvent[]; turnOver: boolean }) {
  const calls = useMemo(() => toolCalls(events), [events])
  if (calls.length === 0) return null
  return (
    <Trace role="group" aria-label="Tool calls" data-testid="tool-trace" className="text-xs">
      {calls.map((c) => (
        <ToolLine key={c.callId} call={c} turnOver={turnOver} />
      ))}
    </Trace>
  )
}

export interface Source {
  id: string
  title: string
  href?: string
}

/**
 * Knowledge citations for a reply: shadcn's Sources pattern on Trace. Empty until the
 * Knowledge store lands in P3, so it renders nothing yet; the slot keeps its place.
 */
export function Sources({ sources }: { sources: Source[] }) {
  if (sources.length === 0) return <div data-slot="citations" hidden />
  return (
    <Trace data-slot="citations" className="text-xs">
      <TraceCollapsible>
        <TraceCollapsibleTrigger>
          <TraceLine>
            <TraceIcon>
              <BookOpen />
            </TraceIcon>
            <TraceTitle>
              Used {sources.length} source{sources.length === 1 ? '' : 's'}
            </TraceTitle>
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent>
          <Trace>
            {sources.map((s) => (
              <TraceLine key={s.id}>
                <TraceIcon>
                  <BookOpen />
                </TraceIcon>
                <TraceTitle>{s.title}</TraceTitle>
              </TraceLine>
            ))}
          </Trace>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  )
}
