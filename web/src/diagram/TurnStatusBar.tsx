import { useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Hint } from '@/components/hint'
import { Card } from '@/components/ui/card'
import { usePanels } from '@/state/panels'
import type { TurnState } from '@/state/reducer'
import { useApp } from '@/state/store'
import { budgetFacts } from '@/state/turnFacts'
import { budgetReadout, budgetRows } from './budget'
import { formatMs, formatTokens } from './stats'

const PHASE_TEXT: Record<TurnState['phase'], string> = {
  idle: 'No turn yet',
  running: 'Running',
  paused: 'Paused',
  done: 'Done',
  error: 'Error',
}

/**
 * The turn's budget as a one-line readout ("54% budget"). The bars live in the session chip's
 * popover in the header; pressing the readout opens it.
 */
function BudgetReadout({ text }: { text: string }) {
  const open = usePanels((s) => s.sessionOpen)
  const setOpen = usePanels((s) => s.setSessionOpen)
  return (
    <Hint label="Budgets and sandbox limits: in the session menu">
      <Button
        variant="ghost"
        size="xs"
        className="pointer-events-auto -my-1 text-muted-foreground tabular-nums"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? 'session-popover' : undefined}
        onClick={() => setOpen(true)}
        data-testid="budget-trigger"
      >
        {text}
      </Button>
    </Hint>
  )
}

export function TurnStatusBar({ turn }: { turn: TurnState }) {
  const tokens = turn.cost.input_tokens + turn.cost.output_tokens
  const budget = useMemo(() => budgetFacts(turn.events), [turn.events])
  const rows = budgetRows(turn, budget)
  const release = useApp((s) => s.capabilities?.release)
  // P6: the public sandbox's limits count too, shown even before the first turn.
  const limits = useApp((s) => (s.capabilities?.mode === 'public' ? s.capabilities.public?.limits : undefined))
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
      className="pointer-events-none absolute top-3 left-3 z-10 max-w-[calc(100%-15rem)] flex-row flex-wrap items-center gap-x-3 gap-y-1 bg-card/95 px-3 py-1.5 text-xs"
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
          {(rows.length > 0 || limits) && <BudgetReadout text={budgetReadout(rows, limits)} />}
        </>
      )}
      {turn.phase === 'idle' && limits && <BudgetReadout text={budgetReadout([], limits)} />}
      {release && (
        <span
          className={release.working_set_dirty ? 'text-state-waiting' : 'text-muted-foreground'}
          title={
            release.working_set_dirty
              ? `The working set has edits that no release holds yet${release.active_release_id ? ` (active: ${release.active_release_id})` : ''}`
              : 'Turns use this release'
          }
          data-testid="release-status"
          data-dirty={release.working_set_dirty ? 'true' : 'false'}
        >
          {release.working_set_dirty ? 'working set (unreleased changes)' : release.active_release_id ? `release ${release.active_release_id}` : 'no release'}
        </span>
      )}
    </Card>
  )
}
