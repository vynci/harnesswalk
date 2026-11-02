// The session chip at the far right of the header: the mode (public, dev, private) and, in public
// mode, how long the sandbox session has left. It opens one popover about the whole session: the
// session (the countdown in words with the time it ends, the session id with Copy, the pack, New
// session), its totals over every turn (with the requests left this hour in public mode), the
// public sandbox's limits and the release line. Each turn's own report is on the chat's timeline,
// and the status bar's readout opens the turn shown's.
import { useMemo, useState } from 'react'
import { useShallow } from 'zustand/react/shallow'
import { Check, ChevronDown, CircleUserRound, Copy, Hourglass, RotateCcw } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Separator } from '@/components/ui/separator'
import { RequestsLeft, SandboxLimits } from '@/diagram/BudgetDetails'
import type { PublicInfo } from '@/api/types'
import { packTitle } from '@/state/packs'
import { usePanels } from '@/state/panels'
import { useApp } from '@/state/store'
import { sessionTotals, turnReport } from '@/state/turnReport'
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

/** Which release serves turns: "Release r2", or the working set's unreleased edits in the waiting colour. */
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

/** The session's turns summed: turns, tokens, cost, tool calls and events, and the requests left (public). */
function SessionTotals({ limits }: { limits?: PublicInfo['limits'] }) {
  const list = useApp(useShallow((s) => s.order.map((id) => s.turns[id])))
  const totals = useMemo(() => sessionTotals(list.filter((t) => !!t).map((t) => turnReport(t.state, t.summary))), [list])
  const rows: { label: string; value: string; testId: string; title?: string }[] = [
    { label: 'Turns', value: String(totals.turns), testId: 'total-turns' },
    { label: 'Tokens', value: String(totals.tokens), testId: 'total-tokens', title: 'Input plus output tokens' },
    { label: 'Cost', value: `$${totals.usd.toFixed(4)}`, testId: 'total-cost' },
    {
      label: 'Tool calls',
      value: totals.toolCallTurns < totals.turns ? `${totals.toolCalls} (in ${totals.toolCallTurns} of ${totals.turns} turns)` : String(totals.toolCalls),
      testId: 'total-tool-calls',
      title: totals.toolCallTurns < totals.turns ? 'A turn read back from the session history carries no tool-call count until it is replayed' : undefined,
    },
    { label: 'Events', value: String(totals.events), testId: 'total-events' },
  ]
  return (
    <section className="flex flex-col gap-1.5" aria-label="Session totals" data-testid="session-totals">
      <h3 className="font-medium">Session totals</h3>
      <dl className="flex flex-col gap-1 text-xs">
        {rows.map((r) => (
          <div key={r.label} className="flex items-baseline justify-between gap-2" title={r.title}>
            <dt className="text-muted-foreground">{r.label}</dt>
            <dd className="truncate font-mono tabular-nums" data-testid={r.testId}>
              {r.value}
            </dd>
          </div>
        ))}
      </dl>
      {limits && <RequestsLeft limits={limits} />}
    </section>
  )
}

/** The release line under a separator, when the API reports releases. */
function ReleaseSection() {
  const has = useApp((s) => !!s.capabilities?.release)
  if (!has) return null
  return (
    <>
      <Separator />
      <ReleaseLine />
    </>
  )
}

export function SessionChip() {
  const capabilities = useApp((s) => s.capabilities)
  const mock = useApp((s) => s.mock)
  const sessionId = useApp((s) => s.sessionId)
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
          <Button size="sm" variant="outline" className="gap-1.5 pr-1.5 pl-2 text-muted-foreground" aria-label={label} data-testid="session-menu">
            <CircleUserRound className="size-4" aria-hidden />
            <span className="text-xs font-medium text-foreground">Session</span>
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
            <ChevronDown className="size-3.5 opacity-70" aria-hidden />
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
        <Separator />
        <SessionTotals limits={limits} />
        {limits && (
          <>
            <Separator />
            <SandboxLimits limits={limits} requests={false} />
          </>
        )}
        <ReleaseSection />
      </PopoverContent>
    </Popover>
  )
}
