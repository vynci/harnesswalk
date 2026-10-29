// Guardrail verdicts as small badges in the chat: what the input guardrails redacted and how
// they scored the message for injection, and what the output guardrails restored or checked.
import { ShieldAlert, ShieldCheck, ShieldOff, TriangleAlert } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { InputGuardFacts, OutputGuardFacts } from '@/state/turnFacts'

// Small secondary chips: monochrome, with tone only in the text colour of a warning or a block.
const chip = 'font-normal'

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

export function InputGuardBadges({ facts, align = 'end' }: { facts: InputGuardFacts; align?: 'start' | 'end' }) {
  const redactions = Object.entries(facts.redactions)
  const score = facts.injectionScore
  const scoreVariant = score !== undefined && score >= 0.8 ? 'destructive' : 'secondary'
  const Icon = facts.action === 'block' ? ShieldOff : redactions.length || facts.warnings.length ? ShieldAlert : ShieldCheck
  return (
    <div
      className={cn('flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground', align === 'end' ? 'justify-end' : 'justify-start')}
      role="group"
      aria-label="Input guardrails"
      data-testid="input-guard"
      data-action={facts.action}
    >
      <Icon className="size-3.5 text-plane-control" aria-hidden />
      <span>Input guardrails</span>
      {facts.action && (
        <Badge variant="secondary" className={chip} data-testid="guard-action">
          {facts.action}
        </Badge>
      )}
      {redactions.map(([kind, n]) => (
        <Badge key={kind} variant="secondary" className={chip} data-testid="guard-redaction" data-kind={kind} title={`${plural(n, kind.replace(/_/g, ' '))} replaced by a placeholder before the model saw the message`}>
          redacted {kind.replace(/_/g, ' ')} ×{n}
        </Badge>
      ))}
      {score !== undefined && (
        <Badge
          variant={scoreVariant}
          className={chip}
          data-testid="guard-injection"
          data-score={score}
          title={Object.keys(facts.injectionSignals).length ? `Signals: ${Object.entries(facts.injectionSignals).map(([k, v]) => `${k} ${v}`).join(', ')}` : 'No injection signals fired'}
        >
          injection {score.toFixed(2)}
        </Badge>
      )}
      {facts.classifierScore !== undefined && (
        <Badge variant="secondary" className={chip} data-testid="guard-classifier">
          classifier {facts.classifierScore.toFixed(2)}
        </Badge>
      )}
      {facts.warnings.map((w) => (
        <Badge key={w} variant="secondary" className={chip} data-testid="guard-warning">
          <TriangleAlert className="text-state-waiting" aria-hidden />
          warning: {w.replace(/_/g, ' ')}
        </Badge>
      ))}
    </div>
  )
}

export function OutputGuardBadges({ facts }: { facts: OutputGuardFacts }) {
  const c = facts.citations
  const show = facts.restored > 0 || (c?.required ?? false) || (facts.action && facts.action !== 'pass')
  if (!show) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground" role="group" aria-label="Output guardrails" data-testid="output-guard" data-action={facts.action}>
      <ShieldCheck className="size-3.5 text-plane-execution" aria-hidden />
      <span>Output guardrails</span>
      {facts.action && facts.action !== 'pass' && (
        <Badge variant="secondary" className={chip} data-testid="guard-output-action" title={facts.reasons.join(', ')}>
          <TriangleAlert className="text-state-waiting" aria-hidden />
          {facts.action}
          {facts.reasons.length ? `: ${facts.reasons.join(', ').replace(/_/g, ' ')}` : ''}
        </Badge>
      )}
      {c?.required && (
        <Badge variant="secondary" className={chip} data-testid="guard-citations" title="Citation markers present in the reply, of the claims drawn from knowledge">
          citations {c.present ?? 0}
          {c.uncited_claims ? `, ${plural(c.uncited_claims, 'uncited claim')}` : ''}
          {c.invalid ? `, ${c.invalid} invalid` : ''}
        </Badge>
      )}
      {facts.restored > 0 && (
        <Badge variant="secondary" className={chip} data-testid="guard-restored" title="Placeholders the output guardrails put back with your own values (dev mode only)">
          restored {facts.restored}
        </Badge>
      )}
    </div>
  )
}
