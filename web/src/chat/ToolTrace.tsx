import { useMemo } from 'react'
import { Ban, Box, Brain, CircleAlert, ShieldQuestion, Wrench } from 'lucide-react'
import type { HarnessEvent } from '@/api/types'
import { Code } from '@/components/code'
import { Badge } from '@/components/ui/badge'
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
import { memoryPoints, memoryRejections } from '@/state/turnFacts'
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
    call.decision ? `policy ${call.decision.replace(/_/g, ' ')}${call.budget ? ` (${call.budget.name.replace(/_/g, ' ')} ${call.budget.used}/${call.budget.limit})` : ''}` : null,
    call.approval && call.approval.state !== 'requested' ? `${call.approval.state === 'approve' ? 'approved' : call.approval.state === 'deny' ? 'denied' : 'expired'}${call.approval.decidedBy ? ` by ${call.approval.decidedBy}` : ''}` : null,
    call.status === 'done' || call.status === 'error' || call.status === 'denied' ? (call.outcome ?? (turnOver ? 'not finished' : null)) : null,
    formatMs(call.durationMs) ?? null,
  ]
  return parts.filter(Boolean).join(' · ')
}

/** Where the call ran, as a chip: sandbox level, then network and jail when they apply. */
function SandboxChip({ sandbox }: { sandbox: NonNullable<ToolCallView['sandbox']> }) {
  const parts = [
    sandbox.level,
    sandbox.network && sandbox.network !== 'n/a' ? `network ${sandbox.network}` : null,
    sandbox.jail && sandbox.jail !== 'none' ? `jail ${sandbox.jail}` : null,
  ].filter(Boolean)
  return (
    <Badge
      variant="outline"
      className="shrink-0 gap-1 rounded-sm px-1 py-0 font-mono text-[10px] leading-4 font-normal text-muted-foreground"
      title={`Sandbox level ${sandbox.level}, network ${sandbox.network ?? 'n/a'}, jail ${sandbox.jail ?? 'none'}`}
      data-testid="sandbox-chip"
      data-level={sandbox.level}
      data-network={sandbox.network}
      data-jail={sandbox.jail}
    >
      <Box aria-hidden className="size-2.5" />
      {parts.join(' · ')}
    </Badge>
  )
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
      <TraceTitle className="shrink-0">{turnOver && call.status === 'running' ? `Stopped before ${call.tool} finished` : title(call)}</TraceTitle>
      <TraceDescription className="min-w-0 shrink truncate" title={call.why}>
        {description(call, turnOver)}
      </TraceDescription>
      {call.sandbox && <SandboxChip sandbox={call.sandbox} />}
    </TraceLine>
  )
  const detail = {
    tool: call.tool,
    call_id: call.callId,
    ...(call.actionClass ? { action_class: call.actionClass } : {}),
    ...(call.args !== undefined ? { args_preview: call.args } : {}),
    ...(call.decision ? { policy: { decision: call.decision, rule: call.rule, ...(call.why ? { why: call.why } : {}), ...(call.budget ? { budget: call.budget } : {}) } } : {}),
    ...(call.sandbox ? { sandbox: call.sandbox } : {}),
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

/**
 * Memories the loop asked for (the `remember` tool), from the memory_proposed and memory_written
 * point events: one Trace line each, written or waiting at the gate. The per-turn episode the
 * writer records on every turn (source `turn`) stays in the inspector, not the chat.
 */
export function MemoryTrace({ events }: { events: HarnessEvent[] }) {
  const lines = useMemo(() => {
    const points = memoryPoints(events)
    const written = new Map(points.filter((p) => p.type === 'memory_written').map((p) => [p.proposalId, p]))
    const rejected = memoryRejections(events)
    return points
      .filter((p) => p.type === 'memory_proposed' && p.source !== 'turn')
      .map((p) => ({ ...p, written: written.get(p.proposalId), rejected: rejected.get(p.proposalId) }))
  }, [events])
  if (lines.length === 0) return null
  return (
    <Trace role="group" aria-label="Memory writes" data-testid="memory-trace" className="text-xs">
      {lines.map((l) => (
        <TraceLine
          key={l.proposalId}
          className="text-xs"
          status={l.written || l.rejected ? undefined : 'pending'}
          data-testid="memory-line"
          data-store={l.store}
          data-written={l.written ? 'true' : 'false'}
          data-rejected={l.rejected ?? undefined}
        >
          <TraceIcon>
            <Brain />
          </TraceIcon>
          <TraceTitle className="shrink-0">{l.written ? `Remembered in ${l.store}` : l.rejected ? `Not remembered in ${l.store}` : `Proposed for ${l.store}`}</TraceTitle>
          <TraceDescription className="min-w-0 shrink truncate">
            {[
              l.preview,
              l.written ? `${l.written.op ?? 'write'} v${l.written.version ?? 1}` : l.rejected ? `rejected: ${l.rejected.replace(/_/g, ' ')}` : `waiting at the ${l.gate ?? 'review'} gate`,
            ]
              .filter(Boolean)
              .join(' · ')}
          </TraceDescription>
        </TraceLine>
      ))}
    </Trace>
  )
}
