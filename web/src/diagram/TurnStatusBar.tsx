import { Card } from '@/components/ui/card'
import type { TurnState } from '@/state/reducer'
import { useMemo } from 'react'
import { budgetFacts } from '@/state/turnFacts'
import { formatMs, formatTokens } from './stats'

const PHASE_TEXT: Record<TurnState['phase'], string> = {
  idle: 'No turn yet',
  running: 'Running',
  paused: 'Paused',
  done: 'Done',
  error: 'Error',
}

export function TurnStatusBar({ turn }: { turn: TurnState }) {
  const tokens = turn.cost.input_tokens + turn.cost.output_tokens
  const budget = useMemo(() => budgetFacts(turn.events), [turn.events])
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
      className="pointer-events-none absolute top-3 left-3 z-10 flex-row flex-wrap items-center gap-2 rounded-md bg-card/90 px-2.5 py-1.5 text-xs shadow-sm backdrop-blur"
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
          <span className="text-muted-foreground" title={budget.budgetTokens !== undefined ? `Input plus output tokens, of the loop's budget of ${budget.budgetTokens}` : 'Input plus output tokens'}>
            {tokens} tok{budget.budgetTokens !== undefined ? ` / ${formatTokens(budget.budgetTokens)}` : ''}
          </span>
          <span className="text-muted-foreground" data-testid="turn-cost">
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
              className={budget.budget.used >= budget.budget.limit ? 'text-state-waiting' : 'text-muted-foreground'}
              title={budget.why}
              data-testid="turn-budget"
            >
              {budget.budget.name.replace(/_/g, ' ')} {budget.budget.used}/{budget.budget.limit}
            </span>
          )}
          {!budget.budget && budget.why && (
            <span className="max-w-64 truncate text-muted-foreground" title={budget.why} data-testid="turn-why">
              policy: {budget.why}
            </span>
          )}
          {turn.durationMs !== null && <span className="text-muted-foreground">{formatMs(turn.durationMs)}</span>}
          <span className="text-muted-foreground">{turn.events.length} events</span>
        </>
      )}
    </Card>
  )
}
