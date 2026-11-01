// A diagnosis (evaluator.md, P5 additions): the layer at fault as a chip, the trace facts that
// point at it, and what to look at first. Diagnose proposes; it never changes anything.
import { useState } from 'react'
import { Stethoscope } from 'lucide-react'
import type { Diagnosis } from '@/api/types'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useFeature } from '@/state/hooks'
import { useApp } from '@/state/store'
import { LAYER_HINT, layerLabel } from './p5View'

/** The layer as a small chip, with what it means in a tooltip. */
export function DiagnosisChip({ layer }: { layer: string }) {
  return (
    <Hint label={`Diagnosis: ${LAYER_HINT[layer] ?? layerLabel(layer)}`} side="top">
      <Badge variant="secondary" className="h-5 shrink-0 font-normal" tabIndex={0} data-testid="diagnosis-chip" data-layer={layer}>
        <Stethoscope aria-hidden />
        {layerLabel(layer)}
      </Badge>
    </Hint>
  )
}

/** The whole diagnosis: chip, evidence and suggestion, and the pass it compared with. */
export function DiagnosisView({ diagnosis }: { diagnosis: Pick<Diagnosis, 'layer' | 'suggestion'> & Partial<Diagnosis> }) {
  const evidence = (diagnosis.evidence ?? []).filter((e) => e && typeof e.fact === 'string')
  const base = diagnosis.baseline
  return (
    <section className="flex flex-col gap-1.5 rounded-lg bg-muted/50 p-3 text-xs" aria-label="Diagnosis" data-testid="diagnosis" data-layer={diagnosis.layer}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium text-foreground">Diagnosis</span>
        <DiagnosisChip layer={diagnosis.layer} />
        {diagnosis.automatic && <span className="text-muted-foreground">automatic, on the regression</span>}
      </div>
      {diagnosis.suggestion && (
        <p className="leading-relaxed text-foreground" data-testid="diagnosis-suggestion">
          {diagnosis.suggestion}
        </p>
      )}
      {evidence.length > 0 && (
        <ul className="flex list-disc flex-col gap-1 pl-4 leading-relaxed break-words text-muted-foreground" aria-label="Evidence" data-testid="diagnosis-evidence">
          {evidence.map((e, i) => (
            <li key={i} data-kind={e.kind}>
              {e.fact}
            </li>
          ))}
        </ul>
      )}
      {diagnosis.evidence !== undefined && (
        <p className="text-muted-foreground/70 tabular-nums">
          {base ? `Compared with ${base.run_id}${base.release_id ? `, on release ${base.release_id}` : ''}, the last pass.` : 'No earlier pass to compare with.'}
        </p>
      )}
    </section>
  )
}

/**
 * The stored diagnosis, or a Diagnose button that asks for one (POST /v1/diagnose) and shows it.
 * Without `features.diagnose` (public mode) the button stays, disabled, with the reason.
 */
export function DiagnoseAction({
  stored,
  target,
  label = 'Diagnose',
}: {
  stored: Diagnosis | null | undefined
  target: { run_id?: string; case_id: string } | { turn_id: string }
  label?: string
}) {
  const feature = useFeature('diagnose')
  const diagnose = useApp((s) => s.diagnose)
  const [mine, setMine] = useState<Diagnosis | null>(null)
  const [busy, setBusy] = useState(false)
  const shown = mine ?? stored
  if (shown) return <DiagnosisView diagnosis={shown} />
  return (
    <Hint label={feature.reason ?? 'Read the stored trace and name the layer at fault: retrieval, prompt or skill, tool, model, guardrail or budget'} side="top">
      <Button
        size="xs"
        variant="outline"
        className="self-start aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
        disabled={busy}
        aria-disabled={!feature.enabled || undefined}
        onClick={async () => {
          if (!feature.enabled) return
          setBusy(true)
          const out = await diagnose(target)
          setBusy(false)
          if (out) setMine(out)
        }}
        data-testid="diagnose-button"
      >
        <Stethoscope aria-hidden /> {busy ? 'Diagnosing…' : label}
      </Button>
    </Hint>
  )
}
