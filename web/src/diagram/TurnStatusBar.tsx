import type { TurnState } from '@/state/reducer'
import { formatMs } from './stats'

const PHASE_TEXT: Record<TurnState['phase'], string> = {
  idle: 'No turn yet',
  running: 'Running',
  paused: 'Paused',
  done: 'Done',
  error: 'Error',
}

export function TurnStatusBar({ turn }: { turn: TurnState }) {
  const tokens = turn.cost.input_tokens + turn.cost.output_tokens
  return (
    <div
      className="pointer-events-none absolute top-3 left-3 z-10 flex flex-wrap items-center gap-2 rounded-md bg-card/90 px-2.5 py-1.5 text-xs shadow-sm ring-1 ring-border backdrop-blur"
      data-testid="turn-status"
      data-phase={turn.phase}
      aria-live="polite"
    >
      <span className="font-medium">{PHASE_TEXT[turn.phase]}</span>
      {turn.paused && <span className="text-state-waiting">at {turn.paused.nodeId} ({turn.paused.reason})</span>}
      {turn.exit && <span className="text-muted-foreground">exit: {turn.exit}</span>}
      {turn.phase !== 'idle' && (
        <>
          <span className="text-muted-foreground" title="Input plus output tokens">
            {tokens} tok
          </span>
          <span className="text-muted-foreground" data-testid="turn-cost">
            ${turn.cost.usd.toFixed(4)}
          </span>
          {turn.durationMs !== null && <span className="text-muted-foreground">{formatMs(turn.durationMs)}</span>}
          <span className="text-muted-foreground">{turn.events.length} events</span>
        </>
      )}
    </div>
  )
}
