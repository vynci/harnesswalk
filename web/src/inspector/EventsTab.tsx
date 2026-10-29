import type { HarnessEvent } from '@/api/types'

export function EventsTab({ events }: { events: HarnessEvent[] }) {
  if (events.length === 0) return <p className="text-xs text-muted-foreground">No events for this node in this turn.</p>
  return (
    <ol className="flex flex-col gap-1.5" aria-label="Node events" data-testid="node-events">
      {events.map((e) => (
        <li key={e.seq} className="rounded border px-2 py-1 text-[11px]">
          <div className="flex gap-2">
            <span className="font-mono text-muted-foreground">#{e.seq}</span>
            <span className="font-medium">{e.type}</span>
            <span className="text-muted-foreground">{e.status}</span>
            {e.duration_ms !== undefined && <span className="ml-auto text-muted-foreground">{e.duration_ms} ms</span>}
          </div>
          {(e.summary || e.error || e.cost) && (
            <pre className="mt-1 overflow-x-auto font-mono text-[10px] leading-4 whitespace-pre-wrap text-muted-foreground">
              {JSON.stringify({ ...(e.summary ? { summary: e.summary } : {}), ...(e.error ? { error: e.error } : {}), ...(e.cost ? { cost: e.cost } : {}) })}
            </pre>
          )}
        </li>
      ))}
    </ol>
  )
}
