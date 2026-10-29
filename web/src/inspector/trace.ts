// The inspector's Events tab as a shadcn Trace: one line per event, its status taken from the
// event and from what came after it. A node_enter is `running` only while its span is still
// open, a pause or an approval request is `pending` only while nothing has resumed it, and an
// event that failed is `error`. Everything else is a plain line.
import type { HarnessEvent } from '@/api/types'
import { formatMs } from '@/diagram/stats'

export type TraceStatus = 'running' | 'error' | 'pending' | undefined

export interface TraceItem {
  event: HarnessEvent
  status: TraceStatus
  title: string
  /** Seq, duration and tokens, in that order, whichever the event has. */
  description: string
  /** The JSON the line opens to: summary, error and cost. Null when the event has none. */
  detail: Record<string, unknown> | null
}

const ENDS_RUN: ReadonlySet<HarnessEvent['type']> = new Set(['node_exit', 'node_paused', 'turn_end'])
const ENDS_WAIT: ReadonlySet<HarnessEvent['type']> = new Set(['node_resumed', 'approval_decided', 'node_exit', 'turn_end'])

/** Whether a later event closes what `ev` opened, for the same node. */
function closedLater(events: HarnessEvent[], i: number, ends: ReadonlySet<HarnessEvent['type']>): boolean {
  const ev = events[i]!
  for (let j = i + 1; j < events.length; j++) {
    const next = events[j]!
    if (next.type === 'turn_end' && ends.has('turn_end')) return true
    if (next.node_id === ev.node_id && ends.has(next.type)) return true
  }
  return false
}

export function traceStatus(events: HarnessEvent[], i: number, opts: { turnOver?: boolean } = {}): TraceStatus {
  const ev = events[i]!
  if (ev.status === 'error' || ev.type === 'error') return 'error'
  if (ev.status === 'waiting') return opts.turnOver || closedLater(events, i, ENDS_WAIT) ? undefined : 'pending'
  if (ev.status === 'active' && (ev.type === 'node_enter' || ev.type === 'node_resumed')) {
    return opts.turnOver || closedLater(events, i, ENDS_RUN) ? undefined : 'running'
  }
  return undefined
}

function tokens(ev: HarnessEvent): string | null {
  if (!ev.cost) return null
  return `${ev.cost.input_tokens} in / ${ev.cost.output_tokens} out tok`
}

/**
 * Trace lines for a node's events. `label` names a node (children appear by their own label)
 * and `self` is the inspected node, whose own events need no name. `turnOver` closes every
 * open span, for a turn that ended without closing them (a cancel, a lost stream).
 */
export function traceItems(
  events: HarnessEvent[],
  { self, label, turnOver = false }: { self: string; label: (id: string) => string; turnOver?: boolean },
): TraceItem[] {
  return events.map((event, i) => {
    const who = event.node_id && event.node_id !== self ? ` · ${label(event.node_id)}` : ''
    const skipped = event.status === 'skipped' ? ' (skipped)' : ''
    const description = [`#${event.seq}`, formatMs(event.duration_ms), tokens(event)].filter(Boolean).join(' · ')
    const detail =
      event.summary || event.error || event.cost
        ? { ...(event.summary ? { summary: event.summary } : {}), ...(event.error ? { error: event.error } : {}), ...(event.cost ? { cost: event.cost } : {}) }
        : null
    return { event, status: traceStatus(events, i, { turnOver }), title: `${event.type}${who}${skipped}`, description, detail }
  })
}
