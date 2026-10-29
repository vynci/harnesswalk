import { useMemo, type ReactNode } from 'react'
import { Box, Brain, Wrench } from 'lucide-react'
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

const words = (s: string) => s.replace(/_/g, ' ')

function title(call: ToolCallView, turnOver: boolean): string {
  if (turnOver && call.status === 'running') return `Stopped before ${call.tool} finished`
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

/** How a decided approval went, in a few words. */
function verdict(call: ToolCallView): string | null {
  const a = call.approval
  if (!a || a.state === 'requested') return null
  const word = a.state === 'approve' ? 'approved' : a.state === 'deny' ? 'denied' : 'expired'
  return a.decidedBy && a.state !== 'expired' ? `${word} by ${a.decidedBy}` : word
}

/**
 * The line's description: a short outcome and the duration, nothing else (the AI Elements Tool
 * pattern). A denied call says what denied it; policy, sandbox and arguments are in the content.
 */
function description(call: ToolCallView, turnOver: boolean): string {
  const denial = call.status === 'denied' && !verdict(call) ? (call.budget ? `${words(call.budget.name)} ${call.budget.used}/${call.budget.limit}` : `policy ${words(call.decision ?? 'deny')}`) : null
  const outcome = call.status === 'error' ? (call.error?.code ?? (call.outcome && call.outcome !== 'ok' ? words(call.outcome) : null)) : null
  const parts = [verdict(call), denial, outcome, turnOver && call.status === 'running' ? 'not finished' : null, formatMs(call.durationMs) ?? null]
  return parts.filter(Boolean).join(' · ')
}

/** Where the call ran, as a chip: sandbox level, then network and jail when they apply. */
function SandboxChip({ sandbox }: { sandbox: NonNullable<ToolCallView['sandbox']> }) {
  const parts = [
    sandbox.level,
    sandbox.network === 'blocked' ? 'no network' : sandbox.network === 'allowed' ? 'network' : null,
    sandbox.jail && sandbox.jail !== 'none' ? sandbox.jail : null,
  ].filter(Boolean)
  return (
    <Badge
      variant="secondary"
      className="max-w-full justify-start font-mono font-normal"
      title={`Sandbox level ${sandbox.level}, network ${sandbox.network ?? 'n/a'}, jail ${sandbox.jail ?? 'none'}`}
      data-testid="sandbox-chip"
      data-level={sandbox.level}
      data-network={sandbox.network}
      data-jail={sandbox.jail}
    >
      <Box aria-hidden />
      <span className="truncate">{parts.join(' · ')}</span>
    </Badge>
  )
}

/** The policy, approval, sandbox and outcome of one call, then its arguments when it has them. */
function CallDetails({ call }: { call: ToolCallView }) {
  const rows: [string, ReactNode][] = []
  if (call.decision) rows.push(['Policy', `${words(call.decision)}${call.rule ? `, rule ${call.rule}` : ''}`])
  if (call.why) rows.push(['Why', call.why])
  if (call.budget) rows.push(['Budget', `${words(call.budget.name)} ${call.budget.used}/${call.budget.limit}`])
  if (call.actionClass) rows.push(['Action', call.actionClass])
  if (call.approval) rows.push(['Approval', verdict(call) ?? 'waiting for a person'])
  if (call.sandbox) rows.push(['Sandbox', <SandboxChip key="sandbox" sandbox={call.sandbox} />])
  if (call.outcome) rows.push(['Outcome', `${words(call.outcome)}${call.executed === false ? ', not executed' : ''}`])
  if (call.error) rows.push(['Error', `${call.error.code}: ${call.error.message}`])
  rows.push(['Call', <code key="call" className="font-mono">{call.callId}</code>])
  return (
    <div className="flex flex-col gap-2">
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs" data-testid="tool-details">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted-foreground/70">{k}</dt>
            <dd className="min-w-0 text-muted-foreground">{v}</dd>
          </div>
        ))}
      </dl>
      {call.args !== undefined && <Code code={JSON.stringify(call.args, null, 2)} language="json" size="sm" wrap label="Arguments" contentClassName="max-h-64" />}
    </div>
  )
}

function ToolLine({ call, turnOver }: { call: ToolCallView; turnOver: boolean }) {
  // A call still open when the turn is over (a cancel) did not finish. Status is text colour and
  // shimmer, never an icon swap; a call waiting for a person is not "pending" (not started): the
  // Confirmation below it asks.
  const status = call.status === 'error' || (turnOver && call.status === 'running') ? 'error' : call.status === 'running' ? 'running' : undefined
  const line = (
    <TraceLine status={status} data-testid="tool-line" data-tool={call.tool} data-call-status={call.status}>
      <TraceIcon>
        <Wrench />
      </TraceIcon>
      <TraceTitle>{title(call, turnOver)}</TraceTitle>
      <TraceDescription>{description(call, turnOver)}</TraceDescription>
    </TraceLine>
  )
  if (call.status === 'running' && !turnOver) return line
  return (
    <TraceCollapsible>
      <TraceCollapsibleTrigger>{line}</TraceCollapsibleTrigger>
      <TraceCollapsibleContent>
        <CallDetails call={call} />
      </TraceCollapsibleContent>
    </TraceCollapsible>
  )
}

/** The tool calls of a turn, inline in the chat: the AI Elements Tool pattern (Trace plus Code Block). */
export function ToolTrace({ events, turnOver }: { events: HarnessEvent[]; turnOver: boolean }) {
  const calls = useMemo(() => toolCalls(events), [events])
  if (calls.length === 0) return null
  return (
    <Trace role="group" aria-label="Tool calls" data-testid="tool-trace">
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
    <Trace role="group" aria-label="Memory writes" data-testid="memory-trace">
      {lines.map((l) => {
        const detail = [
          l.preview,
          l.written ? `${l.written.op ?? 'write'} v${l.written.version ?? 1}` : l.rejected ? `rejected: ${words(l.rejected)}` : `waiting at the ${l.gate ?? 'review'} gate`,
        ]
          .filter(Boolean)
          .join(' · ')
        return (
          <TraceLine
            key={l.proposalId}
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
            <TraceDescription className="min-w-0 shrink truncate" title={detail}>
              {detail}
            </TraceDescription>
          </TraceLine>
        )
      })}
    </Trace>
  )
}
