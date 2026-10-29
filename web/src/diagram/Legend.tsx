import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import type { EdgeKind, NodeKind, PlaneId, View } from '@/api/types'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'
import { edgeColor, KIND_HINT, KIND_ICON } from './planes'

// The three colour channels of the diagram, one row each: node state (accent bar and icon),
// node kind (icon chip) and edge kind (stroke colour and dash). See web/README.md.

const STATES: { state: string; label: string }[] = [
  { state: 'idle', label: 'idle' },
  { state: 'active', label: 'active' },
  { state: 'waiting', label: 'waiting' },
  { state: 'done', label: 'done' },
  { state: 'error', label: 'error' },
  { state: 'skipped', label: 'skipped' },
]

const KINDS: NodeKind[] = ['code', 'config', 'prompt', 'store', 'queue', 'model']

const RUNTIME: PlaneId[] = ['control', 'context', 'execution']

const EDGES: { kind: EdgeKind; label: string; title: string; reverse?: boolean }[] = [
  { kind: 'request', label: 'request', title: 'Request: solid, in the colour of the plane it leaves' },
  { kind: 'request', label: 'reply', title: 'Reply back to the gateway: a lighter tint of the plane colour', reverse: true },
  { kind: 'data', label: 'data', title: 'Data: memory reads and writes, results coming back' },
  { kind: 'control', label: 'control', title: 'Control: policy, approval and audit, dashed' },
  { kind: 'telemetry', label: 'telemetry', title: 'Telemetry: traces, dotted and thin' },
  { kind: 'release', label: 'release', title: 'Release: evals and versioned artifacts, dashed' },
]

const DASH: Partial<Record<EdgeKind, string>> = { control: '5 3', telemetry: '0.5 3.5', release: '7 3' }

function EdgeSwatch({ kind, reverse }: { kind: EdgeKind; reverse?: boolean }) {
  const common = { strokeWidth: kind === 'telemetry' ? 1.5 : 2, strokeLinecap: 'round' as const, strokeDasharray: DASH[kind] }
  return (
    <svg width="20" height="8" viewBox="0 0 24 8" aria-hidden className="shrink-0">
      {kind === 'request' ? (
        RUNTIME.map((p, i) => <line key={p} x1={1 + i * 8} y1="4" x2={7 + i * 8} y2="4" stroke={edgeColor('request', p, Boolean(reverse))} {...common} />)
      ) : (
        <line x1="1" y1="4" x2="23" y2="4" stroke={edgeColor(kind, 'control', false)} {...common} />
      )}
    </svg>
  )
}

function Row({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-1.5">
      <dt className="w-9 shrink-0 font-medium text-foreground">{title}</dt>
      <dd className="flex items-center gap-x-1">{children}</dd>
    </div>
  )
}

export function Legend({ view, showTelemetry, onShowTelemetry }: { view: View; showTelemetry: boolean; onShowTelemetry: (on: boolean) => void }) {
  // Open by default in the simple view, where people meet the colours first.
  const [open, setOpen] = useState(view === 'simple')
  return (
    <Card
      className="absolute right-3 bottom-8 z-10 hidden gap-0 rounded-md bg-card/95 py-0 text-[11px] whitespace-nowrap text-muted-foreground shadow-sm backdrop-blur-sm md:flex"
      data-testid="legend"
      data-open={open ? 'true' : 'false'}
    >
      <div className="flex items-center gap-3 px-2 py-1">
        <Button
          type="button"
          variant="ghost"
          size="xs"
          className="-ml-1 h-5 gap-1 px-1 text-[11px] text-foreground"
          aria-expanded={open}
          aria-controls="diagram-legend"
          onClick={() => setOpen(!open)}
          data-testid="legend-toggle"
        >
          {open ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronUp className="size-3.5" aria-hidden />}
          Legend
        </Button>
        {view === 'simple' && (
          <Hint label="Draw every edge to and from LLM Ops (telemetry, eval, release) instead of one telemetry bus. Hovering a node shows its own." side="top">
            <label className="ml-auto flex cursor-pointer items-center gap-1.5">
              <Switch size="sm" checked={showTelemetry} onCheckedChange={onShowTelemetry} data-testid="telemetry-toggle" aria-label="Show telemetry and LLM Ops edges" />
              Show telemetry
            </label>
          </Hint>
        )}
      </div>
      {open && (
        <dl id="diagram-legend" className="space-y-1 border-t border-border px-2 pt-1.5 pb-1.5" title="Faint: not used this turn. Moving: in use now. Stronger: used this turn.">
          <Row title="State">
            {STATES.map((s) => (
              <Badge key={s.state} variant="ghost" className="gap-1 px-1 py-0 text-[11px] font-normal text-muted-foreground">
                <span
                  className={cn('inline-block h-2.5 w-[3px] rounded-sm')}
                  style={{
                    background:
                      s.state === 'skipped'
                        ? 'repeating-linear-gradient(to bottom, var(--state-skipped) 0 2px, transparent 2px 4px)'
                        : `var(--state-${s.state})`,
                  }}
                />
                {s.label}
              </Badge>
            ))}
            <Badge variant="ghost" className="gap-1 px-1 py-0 text-[11px] font-normal text-muted-foreground">
              <span className="inline-block h-2.5 w-3.5 rounded-sm border border-dashed border-current opacity-60" />
              not built
            </Badge>
          </Row>
          <Row title="Node">
            {KINDS.map((k) => {
              const Icon = KIND_ICON[k]
              return (
                <Badge key={k} variant="ghost" className="gap-1 px-1 py-0 text-[11px] font-normal text-muted-foreground" title={KIND_HINT[k]}>
                  <Badge variant="kind" className="size-4 rounded-[4px] p-0 [&>svg]:size-2.5" data-kind={k}>
                    <Icon strokeWidth={2.25} aria-hidden />
                  </Badge>
                  {k}
                </Badge>
              )
            })}
          </Row>
          <Row title="Edge">
            {EDGES.map((e) => (
              <Badge key={e.label} variant="ghost" className="gap-1 px-1 py-0 text-[11px] font-normal text-muted-foreground [&>svg]:h-2 [&>svg]:w-5" title={e.title}>
                <EdgeSwatch kind={e.kind} reverse={e.reverse} />
                {e.label}
              </Badge>
            ))}
          </Row>
        </dl>
      )}
    </Card>
  )
}
