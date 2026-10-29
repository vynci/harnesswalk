import { useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/components/ui/hover-card'
import { Progress } from '@/components/ui/progress'
import type { TurnState } from '@/state/reducer'
import { budgetFacts, type BudgetFacts } from '@/state/turnFacts'
import { formatMs, formatTokens } from './stats'

const PHASE_TEXT: Record<TurnState['phase'], string> = {
  idle: 'No turn yet',
  running: 'Running',
  paused: 'Paused',
  done: 'Done',
  error: 'Error',
}

interface BudgetRow {
  label: string
  used: number
  limit: number
  text: string
}

function budgetRows(turn: TurnState, budget: BudgetFacts): BudgetRow[] {
  const rows: BudgetRow[] = []
  const tokens = turn.cost.input_tokens + turn.cost.output_tokens
  if (budget.budgetTokens) rows.push({ label: 'Tokens', used: tokens, limit: budget.budgetTokens, text: `${formatTokens(tokens)} / ${formatTokens(budget.budgetTokens)}` })
  if (budget.budgetUsd) rows.push({ label: 'Cost', used: turn.cost.usd, limit: budget.budgetUsd, text: `$${turn.cost.usd.toFixed(4)} / $${budget.budgetUsd.toFixed(2)}` })
  if (budget.maxSteps && budget.steps !== undefined) rows.push({ label: 'Steps', used: budget.steps, limit: budget.maxSteps, text: `${budget.steps} / ${budget.maxSteps}` })
  if (budget.budget) rows.push({ label: budget.budget.name.replace(/_/g, ' '), used: budget.budget.used, limit: budget.budget.limit, text: `${budget.budget.used} / ${budget.budget.limit}` })
  return rows
}

/**
 * The AI Elements Context pattern for the turn's budgets: "{n}% budget" (the most used one),
 * opening to each budget with a Progress bar.
 */
function BudgetHover({ rows, why }: { rows: BudgetRow[]; why?: string }) {
  const share = (r: BudgetRow) => Math.min(100, Math.round((r.used / r.limit) * 100))
  const top = Math.max(...rows.map(share))
  return (
    <HoverCard openDelay={10} closeDelay={100}>
      <HoverCardTrigger asChild>
        <Button variant="ghost" size="xs" className="pointer-events-auto -my-1 text-muted-foreground tabular-nums" data-testid="budget-trigger">
          {top}% budget
        </Button>
      </HoverCardTrigger>
      <HoverCardContent align="start" className="flex w-64 flex-col gap-3 p-4" data-testid="budget-card">
        <p className="font-medium">Budget this turn</p>
        {rows.map((r) => (
          <div key={r.label} className="flex flex-col gap-1.5">
            <div className="flex items-baseline justify-between text-xs">
              <span className="text-muted-foreground">{r.label}</span>
              <span className="font-mono tabular-nums">{r.text}</span>
            </div>
            <Progress value={share(r)} aria-label={`${r.label} used: ${share(r)} percent`} />
          </div>
        ))}
        {why && <p className="text-xs text-muted-foreground">Policy: {why}</p>}
      </HoverCardContent>
    </HoverCard>
  )
}

export function TurnStatusBar({ turn }: { turn: TurnState }) {
  const tokens = turn.cost.input_tokens + turn.cost.output_tokens
  const budget = useMemo(() => budgetFacts(turn.events), [turn.events])
  const rows = budgetRows(turn, budget)
  // Only the phase is announced. The counters change on every event, and a live region over
  // them would read each one out.
  const announce = [
    PHASE_TEXT[turn.phase],
    turn.paused ? `at ${turn.paused.nodeId}, ${turn.paused.reason}` : '',
    turn.exit ? `exit ${turn.exit}` : '',
  ]
    .filter(Boolean)
    .join(', ')
  return (
    <Card
      size="sm"
      className="pointer-events-none absolute top-3 left-3 z-10 flex-row flex-wrap items-center gap-x-3 gap-y-1 rounded-2xl bg-card/95 px-3 py-1.5 text-xs"
      data-testid="turn-status"
      data-phase={turn.phase}
    >
      <span className="sr-only" role="status" aria-live="polite">
        {turn.phase === 'idle' ? '' : `Turn ${announce}`}
      </span>
      <span className="font-medium">{PHASE_TEXT[turn.phase]}</span>
      {turn.paused && <span className="text-state-waiting">at {turn.paused.nodeId} ({turn.paused.reason})</span>}
      {turn.exit && <span className="text-muted-foreground">exit: {turn.exit}</span>}
      {turn.phase !== 'idle' && (
        <>
          <span className="text-muted-foreground tabular-nums" title={budget.budgetTokens !== undefined ? `Input plus output tokens, of the loop's budget of ${budget.budgetTokens}` : 'Input plus output tokens'}>
            {tokens} tok{budget.budgetTokens !== undefined ? ` / ${formatTokens(budget.budgetTokens)}` : ''}
          </span>
          <span className="text-muted-foreground tabular-nums" data-testid="turn-cost">
            ${turn.cost.usd.toFixed(4)}
            {budget.budgetUsd ? ` / $${budget.budgetUsd.toFixed(2)}` : ''}
          </span>
          {budget.toolCalls > 0 && (
            <span className="text-muted-foreground" data-testid="turn-tool-calls">
              {budget.toolCalls} tool call{budget.toolCalls === 1 ? '' : 's'}
            </span>
          )}
          {budget.budget && (
            <span
              className={`pointer-events-auto cursor-help ${budget.budget.used >= budget.budget.limit ? 'text-state-waiting' : 'text-muted-foreground'}`}
              title={budget.why}
              data-testid="turn-budget"
            >
              {budget.budget.name.replace(/_/g, ' ')} {budget.budget.used}/{budget.budget.limit}
            </span>
          )}
          {!budget.budget && budget.why && (
            <span className="pointer-events-auto max-w-56 cursor-help truncate text-muted-foreground" title={budget.why} data-testid="turn-why">
              policy: {budget.why}
            </span>
          )}
          {turn.durationMs !== null && <span className="text-muted-foreground tabular-nums">{formatMs(turn.durationMs)}</span>}
          <span className="text-muted-foreground tabular-nums">{turn.events.length} events</span>
          {rows.length > 0 && <BudgetHover rows={rows} why={budget.why} />}
        </>
      )}
    </Card>
  )
}
