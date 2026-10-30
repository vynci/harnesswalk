import { useState } from 'react'
import { ChevronDown, ChevronUp } from 'lucide-react'
import type { EdgeKind, NodeKind, PlaneId, View } from '@/api/types'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { load, save } from '@/state/persist'
import type { NodeState } from '@/state/reducer'
import { StateIcon } from './HarnessNode'
import { edgeColor, KIND_HINT, KIND_ICON } from './planes'

// The three colour channels of the diagram, one row each: node state (status icon, and a ring
// while active, waiting or failed),
// node kind (icon chip) and edge kind (stroke colour and dash). See web/README.md.

const STATES: { state: NodeState; label: string; title: string }[] = [
  { state: 'idle', label: 'idle', title: 'Idle: not reached this turn' },
  { state: 'active', label: 'active', title: 'Active: working now, with a pulsing ring' },
  { state: 'waiting', label: 'waiting', title: 'Waiting: paused (approval or step mode), with a dashed ring' },
  { state: 'done', label: 'done', title: 'Done this turn' },
  { state: 'error', label: 'error', title: 'Error: failed, with a ring' },
  { state: 'skipped', label: 'skipped', title: 'Skipped: switched off or not needed, faded' },
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
    <div className="flex items-start gap-2">
      <dt className="w-10 shrink-0 font-medium text-foreground">{title}</dt>
      <dd className="flex flex-wrap items-center gap-x-2.5 gap-y-1">{children}</dd>
    </div>
  )
}

/** One legend entry: a swatch and its word. */
function Entry({ children, title }: { children: React.ReactNode; title?: string }) {
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap" title={title}>
      {children}
    </span>
  )
}

export function Legend({
  view,
  roomy,
  showTelemetry,
  onShowTelemetry,
}: {
  view: View
  /** The diagram is wide enough for the open legend not to sit over the LLM Ops row. */
  roomy: boolean
  showTelemetry: boolean
  onShowTelemetry: (on: boolean) => void
}) {
  // Open by default in the simple view, where people meet the colours first, and only while the
  // diagram has room (an open inspector narrows it). Once the reader opens or closes it, that
  // choice holds across view switches and reloads.
  const [pref, setPref] = useState<boolean | null>(() => load<boolean | null>('legend', null))
  const open = pref ?? (view === 'simple' && roomy)
  const setOpen = (next: boolean) => {
    setPref(next)
    save('legend', next)
  }
  return (
    <Card
      size="sm"
      className="absolute right-3 bottom-8 z-10 hidden max-w-[calc(100%-5rem)] gap-0 rounded-2xl bg-card/95 py-0 text-xs text-muted-foreground md:flex"
      data-testid="legend"
      data-open={open ? 'true' : 'false'}
    >
      <div className="flex items-center gap-3 px-2 py-1.5">
        <Button
          type="button"
          variant="ghost"
          size="xs"
          className="text-foreground"
          aria-expanded={open}
          aria-controls="diagram-legend"
          onClick={() => setOpen(!open)}
          data-testid="legend-toggle"
        >
          {open ? <ChevronDown aria-hidden /> : <ChevronUp aria-hidden />}
          Legend
        </Button>
        {view === 'simple' && (
          <Hint label="Draw every edge to and from LLM Ops (telemetry, eval, release) instead of one telemetry bus. Hovering a node shows its own." side="top">
            <label className="ml-auto flex cursor-pointer items-center gap-1.5 pr-1">
              <Switch size="sm" checked={showTelemetry} onCheckedChange={onShowTelemetry} data-testid="telemetry-toggle" aria-label="Show telemetry and LLM Ops edges" />
              Show telemetry
            </label>
          </Hint>
        )}
      </div>
      {open && (
        <dl id="diagram-legend" className="flex flex-col gap-1.5 border-t px-3.5 pt-2 pb-2.5" title="Faint: not used this turn. Moving: in use now. Stronger: used this turn.">
          <Row title="State">
            {STATES.map((s) => (
              <Entry key={s.state} title={s.title}>
                <StateIcon state={s.state} spin={false} />
                {s.label}
              </Entry>
            ))}
            <Entry title="Not built yet: faded, with a not yet pill">
              <Badge variant="outline" className="h-4 border-dashed border-current px-1.5 font-normal text-muted-foreground">
                not yet
              </Badge>
              not built
            </Entry>
          </Row>
          <Row title="Node">
            {KINDS.map((k) => {
              const Icon = KIND_ICON[k]
              return (
                <Entry key={k} title={KIND_HINT[k]}>
                  <Badge variant="kind" className="size-4 rounded-[4px] p-0 [&>svg]:size-2.5!" data-kind={k}>
                    <Icon strokeWidth={2.25} aria-hidden />
                  </Badge>
                  {k}
                </Entry>
              )
            })}
          </Row>
          <Row title="Edge">
            {EDGES.map((e) => (
              <Entry key={e.label} title={e.title}>
                <EdgeSwatch kind={e.kind} reverse={e.reverse} />
                {e.label}
              </Entry>
            ))}
          </Row>
        </dl>
      )}
    </Card>
  )
}
