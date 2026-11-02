import { useMemo, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Hint } from '@/components/hint'
import { Card } from '@/components/ui/card'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import type { PublicInfo } from '@/api/types'
import type { TurnState } from '@/state/reducer'
import { selectDisplayTurnId, useApp } from '@/state/store'
import { turnReport, type TurnReport } from '@/state/turnReport'
import { SandboxLimits, TurnReportCard } from './BudgetDetails'
import { budgetReadout } from './budget'
import { formatMs } from './stats'

const PHASE_TEXT: Record<TurnState['phase'], string> = {
  idle: 'No turn yet',
  running: 'Running',
  paused: 'Paused',
  done: 'Done',
  error: 'Error',
}

/**
 * The turn's budget as a one-line readout ("54% budget"), a control that opens the turn shown's
 * full report in a popover under the bar: its facts, bars, the policy's sentence and the release.
 * Before a turn the popover holds the sandbox's limits (public) or says what will show.
 */
function BudgetReadout({ text, report, limits }: { text: string; report: TurnReport | null; limits?: PublicInfo['limits'] }) {
  const [open, setOpen] = useState(false)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Hint label="This turn's report">
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="xs"
            className="pointer-events-auto -my-1 gap-0.5 pr-1 text-muted-foreground tabular-nums"
            aria-label={`${text}: this turn's report`}
            data-testid="budget-trigger"
          >
            {text}
            <ChevronDown className="size-3 opacity-70" aria-hidden />
          </Button>
        </PopoverTrigger>
      </Hint>
      <PopoverContent align="start" className="w-80 gap-3" aria-label="This turn's report" data-testid="turn-report-popover">
        {report ? (
          <TurnReportCard report={report} title="This turn" />
        ) : limits ? (
          <SandboxLimits limits={limits} />
        ) : (
          <p className="text-xs text-muted-foreground" data-testid="turn-report-empty">
            Send a message to see this turn's budget.
          </p>
        )}
      </PopoverContent>
    </Popover>
  )
}

export function TurnStatusBar({ turn }: { turn: TurnState }) {
  // The stored summary fills in only for a live turn; a replayed one stays at the replay's cursor.
  const replaying = useApp((s) => !!s.replay)
  const summary = useApp((s) => {
    const id = selectDisplayTurnId(s)
    return id ? s.turns[id]?.summary : undefined
  })
  const report = useMemo(() => (turn.phase === 'idle' ? null : turnReport(turn, replaying ? null : summary)), [turn, replaying, summary])
  const release = useApp((s) => s.capabilities?.release)
  // P6: the public sandbox's limits count too, shown even before the first turn.
  const limits = useApp((s) => (s.capabilities?.mode === 'public' ? s.capabilities.public?.limits : undefined))
  // Only the phase is announced. The duration changes as the turn runs, and a live region over
  // it would read each one out. The counters (tokens, cost, tool calls, events) and the release
  // are in the readout's popover, and every turn's on the chat's timeline.
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
      {turn.exit && turn.exit !== 'answered' && <span className="text-muted-foreground">exit: {turn.exit}</span>}
      {turn.phase !== 'idle' && turn.durationMs !== null && <span className="text-muted-foreground tabular-nums">{formatMs(turn.durationMs)}</span>}
      <BudgetReadout text={budgetReadout(report?.rows ?? [], limits)} report={report} limits={limits} />
      {release?.working_set_dirty && (
        <Hint label="Working set has unreleased changes">
          <span className="pointer-events-auto flex size-4 items-center justify-center" role="img" aria-label="Working set has unreleased changes" data-testid="release-dirty">
            <span className="size-2 rounded-full bg-state-waiting" aria-hidden />
          </span>
        </Hint>
      )}
    </Card>
  )
}
