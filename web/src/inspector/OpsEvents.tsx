// 0.2.0: what an LLM Ops node did lately, from the tenant's ops stream (GET /v1/events): a tune
// run's start, its steps (folded into the last one) and its end, a curation's progress, the
// model gate. Turn events stay in the Events tab; these happen outside every turn.
import { useMemo } from 'react'
import { Activity } from 'lucide-react'
import { Code } from '@/components/code'
import { Trace, TraceCollapsible, TraceCollapsibleContent, TraceCollapsibleTrigger, TraceDescription, TraceIcon, TraceLine, TraceTitle } from '@/components/ui/trace'
import { useApp } from '@/state/store'
import { latestOpsEvents, opsEventText } from './tuneView'

function time(iso: string | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isFinite(d.getTime()) ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : ''
}

export function OpsEvents({ ids }: { ids: ReadonlySet<string> }) {
  const log = useApp((s) => s.opsLog)
  const items = useMemo(() => latestOpsEvents(log, ids), [log, ids])
  if (items.length === 0) return null
  return (
    <section className="mt-4 flex flex-col gap-1.5" aria-label="Latest ops events" data-testid="ops-events">
      <h4 className="text-xs font-medium text-muted-foreground">Latest ops events (outside any turn), newest first</h4>
      <Trace role="list">
        {items.map((ev, i) => (
          <TraceCollapsible key={ev.event_id ?? `${ev.type}-${i}`} role="listitem">
            <TraceCollapsibleTrigger>
              <TraceLine status={ev.status === 'error' ? 'error' : ev.status === 'active' && ev.type !== 'tune_step' ? 'running' : undefined} data-type={ev.type}>
                <TraceIcon>
                  <Activity />
                </TraceIcon>
                <TraceTitle>{ev.type}</TraceTitle>
                <TraceDescription>
                  {opsEventText(ev)}
                  {ev.ts ? ` · ${time(ev.ts)}` : ''}
                </TraceDescription>
              </TraceLine>
            </TraceCollapsibleTrigger>
            <TraceCollapsibleContent>
              <Code code={JSON.stringify(ev.summary ?? {}, null, 2)} language="json" size="sm" wrap label={`Ops event ${ev.type}`} contentClassName="max-h-64 text-xs" />
            </TraceCollapsibleContent>
          </TraceCollapsible>
        ))}
      </Trace>
    </section>
  )
}
