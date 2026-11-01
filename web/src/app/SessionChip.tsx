// The session chip at the far right of the header: the mode (public, dev, private) and, in public
// mode, how long the sandbox session has left. It opens one popover that holds the session (the
// countdown in words with the time it ends, the session id with Copy, New session), the public
// sandbox's limits, and the turn shown (its exit, duration, tool calls, events, release, budgets
// and the policy's sentence). The status bar's budget readout opens it too.
import { useMemo, useState } from 'react'
import { Check, Copy, Hourglass, RotateCcw } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Separator } from '@/components/ui/separator'
import { budgetRows } from '@/diagram/budget'
import { SandboxLimits, TurnBudget, type TurnFact } from '@/diagram/BudgetDetails'
import { formatMs } from '@/diagram/stats'
import { useDisplayTurn } from '@/state/hooks'
import { packTitle } from '@/state/packs'
import { usePanels } from '@/state/panels'
import type { TurnState } from '@/state/reducer'
import { useApp } from '@/state/store'
import { budgetFacts } from '@/state/turnFacts'
import { CHIP_WARN_MS, chipText, endsSentence, leftText } from './sandbox'
import { useNow } from './useSandbox'

/** Public mode: the sandbox session's end and the time left (ticking), or null. */
function useSessionLeft(): { expiresAt: string; end: number; left: number } | null {
  const expiresAt = useApp((s) => (s.capabilities?.mode === 'public' ? (s.capabilities.public?.session_expires_at ?? null) : null))
  const now = useNow(15_000)
  if (!expiresAt) return null
  const end = Date.parse(expiresAt)
  if (!Number.isFinite(end)) return null
  return { expiresAt, end, left: end - now }
}

function CopyId({ sessionId }: { sessionId: string | null }) {
  const notify = useApp((s) => s.notify)
  const [copied, setCopied] = useState(false)
  const copy = () => {
    if (!sessionId) return
    navigator.clipboard?.writeText(sessionId).then(
      () => {
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
        notify('info', 'Session id copied.')
      },
      () => notify('error', 'Could not copy the session id.'),
    )
  }
  return (
    <Hint label={copied ? 'Copied' : 'Copy session id'}>
      <Button size="icon-xs" variant="ghost" aria-label="Copy session id" disabled={!sessionId} onClick={copy} data-testid="session-copy">
        {copied ? <Check /> : <Copy />}
      </Button>
    </Hint>
  )
}

/**
 * Which release serves turns: "Release r2", or the working set's unreleased edits in the
 * waiting colour. In the This turn card once a turn is shown, in the Session section before.
 */
function ReleaseLine() {
  const release = useApp((s) => s.capabilities?.release)
  if (!release) return null
  const dirty = release.working_set_dirty
  const active = release.active_release_id
  const text = dirty
    ? `Working set with unreleased changes${active ? `, active release ${active}` : ''}`
    : active
      ? `Release ${active}`
      : 'No release'
  return (
    <p
      className={`text-xs ${dirty ? 'text-state-waiting' : 'text-muted-foreground'}`}
      title={dirty ? 'The working set has edits that no release holds yet' : 'Turns use this release'}
      data-testid="release-status"
      data-dirty={dirty ? 'true' : 'false'}
    >
      {text}
    </p>
  )
}

/** The turn shown (live or replayed): its facts, the release, its budgets and the policy. */
function ThisTurn({ turn }: { turn: TurnState }) {
  const budget = useMemo(() => budgetFacts(turn.events), [turn.events])
  const rows = budgetRows(turn, budget)
  const tokens = turn.cost.input_tokens + turn.cost.output_tokens
  const facts: TurnFact[] = []
  if (turn.exit) facts.push({ label: 'Exit', value: turn.exit, testId: 'turn-exit' })
  const duration = turn.durationMs !== null ? formatMs(turn.durationMs) : undefined
  if (duration) facts.push({ label: 'Duration', value: duration, testId: 'turn-duration' })
  facts.push({ label: 'Tool calls', value: String(budget.toolCalls), testId: 'turn-tool-calls' })
  facts.push({ label: 'Events', value: String(turn.events.length), testId: 'turn-events' })
  // Tokens, cost and steps show as bars when the loop has a budget for them, else as facts.
  if (!budget.budgetTokens) facts.push({ label: 'Tokens', value: String(tokens), testId: 'turn-tokens', title: 'Input plus output tokens' })
  if (!budget.budgetUsd) facts.push({ label: 'Cost', value: `$${turn.cost.usd.toFixed(4)}`, testId: 'turn-cost' })
  if (!budget.maxSteps && budget.steps !== undefined) facts.push({ label: 'Steps', value: String(budget.steps), testId: 'turn-steps' })
  return (
    <>
      <Separator />
      <TurnBudget facts={facts} rows={rows} why={budget.why} release={<ReleaseLine />} />
    </>
  )
}

export function SessionChip() {
  const capabilities = useApp((s) => s.capabilities)
  const mock = useApp((s) => s.mock)
  const sessionId = useApp((s) => s.sessionId)
  const turns = useApp((s) => s.order.length)
  const newSession = useApp((s) => s.newSession)
  const packName = useApp((s) => (s.packs ? packTitle(s.packs, s.pack) : null))
  const limits = useApp((s) => (s.capabilities?.mode === 'public' ? s.capabilities.public?.limits : undefined))
  const open = usePanels((s) => s.sessionOpen)
  const setOpen = usePanels((s) => s.setSessionOpen)
  const session = useSessionLeft()
  const turn = useDisplayTurn()
  if (!capabilities) return null
  const mode = `${capabilities.mode}${mock ? ' · mock' : ''}`
  const warn = !!session && session.left <= CHIP_WARN_MS
  const short = session ? chipText(session.left) : null
  const label = `Session: ${capabilities.mode}${session ? (session.left > 0 ? `, ${leftText(session.left)} left` : ', ended') : ''}`

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Hint label={session ? endsSentence(session.left, session.end) : 'Session'}>
        <PopoverTrigger asChild>
          <Button size="sm" variant="ghost" className="gap-1.5 px-1.5" aria-label={label} data-testid="session-menu">
            <Badge variant="outline" className="font-mono" data-testid="mode-badge">
              {mode}
            </Badge>
            {session && short && (
              <span
                className={`flex items-center gap-1 text-xs tabular-nums ${warn ? 'text-state-waiting' : 'text-muted-foreground'}`}
                data-testid="session-countdown"
                data-expires-at={session.expiresAt}
                data-warn={warn ? 'true' : 'false'}
              >
                <Hourglass className="size-3.5" aria-hidden />
                {short}
              </span>
            )}
          </Button>
        </PopoverTrigger>
      </Hint>
      <PopoverContent id="session-popover" align="end" className="w-80 gap-3" aria-label="Session" data-testid="session-popover">
        <section className="flex flex-col gap-1.5" aria-label="Session">
          <h3 className="font-medium">Session</h3>
          <p className="text-xs text-muted-foreground" data-testid="session-mode">
            {capabilities.mode === 'public' ? 'Public sandbox' : capabilities.mode === 'dev' ? 'Dev mode' : 'Private mode'} · API{' '}
            <span className="font-mono">{capabilities.version}</span>
            {mock ? ', in-browser mock' : ''}
          </p>
          {session && (
            <p className={`text-xs leading-relaxed ${warn ? 'text-state-waiting' : 'text-muted-foreground'}`} data-testid="session-ends">
              {endsSentence(session.left, session.end)}
            </p>
          )}
          <div className="flex items-center gap-1">
            <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground" data-testid="session-id" title={sessionId ?? undefined}>
              {sessionId ? <span className="font-mono">{sessionId}</span> : 'No id yet: it starts with your first message'}
              {turns ? ` · ${turns} turn${turns === 1 ? '' : 's'}` : ''}
            </span>
            <CopyId sessionId={sessionId} />
          </div>
          {packName && (
            <span className="truncate text-xs text-muted-foreground" data-testid="session-pack">
              on {packName}
            </span>
          )}
          {turn.phase === 'idle' && <ReleaseLine />}
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="mt-1 self-start"
            onClick={() => {
              setOpen(false)
              newSession()
            }}
            data-testid="session-new"
          >
            <RotateCcw aria-hidden /> New session
          </Button>
        </section>
        {limits && (
          <>
            <Separator />
            <SandboxLimits limits={limits} />
          </>
        )}
        {turn.phase !== 'idle' && <ThisTurn turn={turn} />}
      </PopoverContent>
    </Popover>
  )
}
