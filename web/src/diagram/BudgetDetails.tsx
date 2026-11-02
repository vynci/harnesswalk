// A turn's report card (its facts, the release it ran on, its budgets as bars and the policy's
// sentence, the AI Elements Context pattern) and the public sandbox's limits. The chat's turn
// rows expand to the card, the status bar's readout opens it for the turn shown, and the session
// chip in the header holds the sandbox's limits and the requests left this hour.
import type { ReactNode } from 'react'
import { Progress } from '@/components/ui/progress'
import type { PublicInfo } from '@/api/types'
import { releaseText, reportFacts, type TurnFact, type TurnReport } from '@/state/turnReport'
import { rowShare, type BudgetRow } from './budget'
import { formatTokens } from './stats'

export type { TurnFact } from '@/state/turnReport'

/** Public mode: the requests this address has left this hour, with a bar of the share used. */
export function RequestsLeft({ limits }: { limits: PublicInfo['limits'] }) {
  const used = Math.max(0, limits.per_ip_per_hour - limits.remaining_this_hour)
  const share = limits.per_ip_per_hour > 0 ? Math.min(100, Math.round((used / limits.per_ip_per_hour) * 100)) : 0
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between text-xs">
        <span className="text-muted-foreground">Requests left this hour</span>
        <span className="font-mono tabular-nums" data-testid="limit-remaining">
          {limits.remaining_this_hour} / {limits.per_ip_per_hour}
        </span>
      </div>
      <Progress value={share} aria-label={`Requests used this hour: ${share} percent`} />
    </div>
  )
}

/**
 * Public mode: the sandbox's own limits, the same for every turn (CONTRACT section 2). The
 * session chip shows the requests left in its Session totals, so it leaves them out here.
 */
export function SandboxLimits({ limits, requests = true }: { limits: PublicInfo['limits']; requests?: boolean }) {
  return (
    <section className="flex flex-col gap-1.5" aria-label="Sandbox limits" data-testid="limits-card">
      <h3 className="font-medium">Sandbox limits</h3>
      {requests && <RequestsLeft limits={limits} />}
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

/**
 * A turn, compact: a two-column list of its facts, the release line, then its budgets as
 * Progress bars and the policy's sentence. `title` heads the card (the status bar's popover);
 * the chat's rows already name the turn, so theirs has none.
 */
export function TurnBudget({
  facts,
  rows,
  why,
  release,
  title,
  label = 'Turn report',
  testId = 'budget-card',
}: {
  facts: TurnFact[]
  rows: BudgetRow[]
  why?: string
  release?: ReactNode
  title?: string
  label?: string
  testId?: string
}) {
  return (
    <section className="flex flex-col gap-3" aria-label={title ?? label} data-testid={testId}>
      {title && <h3 className="font-medium">{title}</h3>}
      {(facts.length > 0 || release) && (
        <div className="flex flex-col gap-1.5">
          {facts.length > 0 && (
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs" data-testid="turn-facts">
              {facts.map((f) => (
                <div key={f.label} className="flex min-w-0 items-baseline justify-between gap-2" title={f.title}>
                  <dt className="text-muted-foreground">{f.label}</dt>
                  <dd className="truncate font-mono tabular-nums" data-testid={f.testId}>
                    {f.value}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {release}
        </div>
      )}
      {rows.map((r) => (
        <div key={r.label} className="flex flex-col gap-1.5" data-testid="budget-row" data-label={r.label} data-used={r.used} data-limit={r.limit}>
          <div className="flex items-baseline justify-between text-xs">
            <span className="text-muted-foreground">{r.label}</span>
            <span className="font-mono tabular-nums">{r.text}</span>
          </div>
          <Progress value={rowShare(r)} aria-label={`${r.label} used: ${rowShare(r)} percent`} />
        </div>
      ))}
      {why && (
        <p className="text-xs text-muted-foreground" data-testid="turn-why">
          Policy: {why}
        </p>
      )}
    </section>
  )
}

/** A turn's full report: the facts, the release it ran on, the bars and the policy. */
export function TurnReportCard({ report, title, label, testId }: { report: TurnReport; title?: string; label?: string; testId?: string }) {
  const release = releaseText(report.release)
  return (
    <TurnBudget
      facts={reportFacts(report)}
      rows={report.rows}
      why={report.why}
      title={title}
      label={label}
      testId={testId}
      release={
        release || report.fromSummary ? (
          <>
            {release && (
              <p className="text-xs text-muted-foreground" data-testid="turn-release" title={report.release?.hash ? `Artifacts ${report.release.hash}` : undefined}>
                {release}
              </p>
            )}
            {report.fromSummary && (
              <p className="text-xs text-muted-foreground" data-testid="turn-report-partial">
                From the session history: tool calls, steps and budgets are in this turn's events (Replay shows them).
              </p>
            )}
          </>
        ) : undefined
      }
    />
  )
}
