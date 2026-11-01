// The turn's budgets and the public sandbox's limits, as bars (the AI Elements Context pattern).
// The session chip's popover in the header shows them; the status bar keeps a one-line readout
// ("54% budget") that opens that popover.
import { Progress } from '@/components/ui/progress'
import type { PublicInfo } from '@/api/types'
import { rowShare, type BudgetRow } from './budget'
import { formatTokens } from './stats'

/** Public mode: the sandbox's own limits, the same for every turn (CONTRACT section 2). */
export function SandboxLimits({ limits }: { limits: PublicInfo['limits'] }) {
  const used = Math.max(0, limits.per_ip_per_hour - limits.remaining_this_hour)
  const share = limits.per_ip_per_hour > 0 ? Math.min(100, Math.round((used / limits.per_ip_per_hour) * 100)) : 0
  return (
    <section className="flex flex-col gap-1.5" aria-label="Sandbox limits" data-testid="limits-card">
      <h3 className="font-medium">Sandbox limits</h3>
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted-foreground">Requests left this hour</span>
        <span className="font-mono tabular-nums" data-testid="limit-remaining">
          {limits.remaining_this_hour} / {limits.per_ip_per_hour}
        </span>
      </div>
      <Progress value={share} aria-label={`Requests used this hour: ${share} percent`} />
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted-foreground">Steps per turn</span>
        <span className="font-mono tabular-nums" data-testid="limit-steps">
          {limits.max_steps}
        </span>
      </div>
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted-foreground">Tokens per turn</span>
        <span className="font-mono tabular-nums" data-testid="limit-tokens">
          {formatTokens(limits.budget_tokens)}
        </span>
      </div>
    </section>
  )
}

/** The turn's budgets, each with a Progress bar, and the policy's sentence. */
export function TurnBudget({ rows, why }: { rows: BudgetRow[]; why?: string }) {
  if (rows.length === 0 && !why) return null
  return (
    <section className="flex flex-col gap-3" aria-label="This turn" data-testid="budget-card">
      <h3 className="font-medium">This turn</h3>
      {rows.map((r) => (
        <div key={r.label} className="flex flex-col gap-1.5">
          <div className="flex items-baseline justify-between text-xs">
            <span className="text-muted-foreground">{r.label}</span>
            <span className="font-mono tabular-nums">{r.text}</span>
          </div>
          <Progress value={rowShare(r)} aria-label={`${r.label} used: ${rowShare(r)} percent`} />
        </div>
      ))}
      {why && <p className="text-xs text-muted-foreground">Policy: {why}</p>}
    </section>
  )
}
