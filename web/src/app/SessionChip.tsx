// The session chip at the far right of the header: the mode (public, dev, private) and, in public
// mode, how long the sandbox session has left. It opens one popover that holds the session (the
// countdown in words with the time it ends, the session id with Copy, New session), the public
// sandbox's limits, and the turn's budgets. The status bar's budget readout opens it too.
import { useMemo, useState } from 'react'
import { Check, Copy, Hourglass, RotateCcw } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Separator } from '@/components/ui/separator'
import { budgetRows } from '@/diagram/budget'
import { SandboxLimits, TurnBudget } from '@/diagram/BudgetDetails'
import { useDisplayTurn } from '@/state/hooks'
import { packTitle } from '@/state/packs'
import { usePanels } from '@/state/panels'
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

/** The turn shown (live or replayed): its budgets, when it has any. */
function ThisTurn() {
  const turn = useDisplayTurn()
  const budget = useMemo(() => budgetFacts(turn.events), [turn.events])
  if (turn.phase === 'idle') return null
  const rows = budgetRows(turn, budget)
  if (rows.length === 0 && !budget.why) return null
  return (
    <>
      <Separator />
      <TurnBudget rows={rows} why={budget.why} />
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
        <ThisTurn />
      </PopoverContent>
    </Popover>
  )
}
