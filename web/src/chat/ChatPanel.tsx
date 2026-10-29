import { useMemo, useState, type ComponentProps, type ReactElement } from 'react'
import { Eye, Info, MessagesSquare, OctagonX, Play, RefreshCcw, Route, TriangleAlert } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Bubble, BubbleContent } from '@/components/ui/bubble'
import { Button } from '@/components/ui/button'
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from '@/components/ui/empty'
import { Marker, MarkerContent, MarkerIcon } from '@/components/ui/marker'
import { Message, MessageContent, MessageFooter } from '@/components/ui/message'
import {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
} from '@/components/ui/message-scroller'
import { Spinner } from '@/components/ui/spinner'
import { Switch } from '@/components/ui/switch'
import { useDisplayTurn } from '@/state/hooks'
import type { TurnState } from '@/state/reducer'
import {
  citationsOf,
  errorWithCode,
  inputGuardFacts,
  limitFacts,
  outputGuardFacts,
  placeholderValues,
  splitPlaceholders,
  withPlaceholders,
  type LimitFacts,
} from '@/state/turnFacts'
import { useApp, type ChatTurn } from '@/state/store'
import { ApprovalCard } from './ApprovalCard'
import { Composer } from './Composer'
import { StepControls } from './StepControls'
import { ReplyText, Sources } from './Citations'
import { InputGuardBadges, OutputGuardBadges } from './Guardrails'
import { MemoryTrace, ToolTrace } from './ToolTrace'

const TRY_THESE = [
  'What are your support hours?',
  'Where is order A-1001?',
  'Please cancel order A-1002.',
  'Can I return order A-1003, the Trailhead backpack?',
]

type Notice = { tone: 'info' | 'warn' | 'error'; text: string; reason?: string }

const LIMIT_TEXT: Record<string, string> = {
  max_steps: 'The loop used all its steps before it finished.',
  budget_tokens: 'The loop used its token budget for this turn.',
  budget_usd: 'The loop used its cost budget for this turn.',
  budget_tool_calls: 'The tool-call budget for this turn ran out, so the loop stopped.',
  timeout: 'The loop ran out of time.',
  stuck: 'The loop kept repeating the same call, so it stopped.',
}

function limitText(limit: LimitFacts): string {
  const base = (limit.reason && LIMIT_TEXT[limit.reason]) || 'The loop hit a limit (steps, tokens, budget or time) before it finished.'
  const used = limit.budget ? ` Policy: ${limit.budget.name.replace(/_/g, ' ')} ${limit.budget.used} of ${limit.budget.limit}` : ''
  const why = limit.why ? `${used ? ',' : ' Policy:'} ${limit.why}.` : used ? '.' : ''
  return `${base}${used}${why}`
}

function noticeFor(exit: TurnState['exit'] | null | undefined, state?: TurnState): Notice | null {
  const error = state?.error
  const events = state?.events ?? []
  switch (exit) {
    case 'blocked':
      return { tone: 'warn', text: 'Blocked by a guardrail. The model never saw this message.' }
    case 'limit':
      return { tone: 'warn', text: limitText(limitFacts(events)), reason: limitFacts(events).reason }
    case 'cancelled':
      return { tone: 'info', text: 'Turn cancelled. Open spans were closed and nothing else ran.' }
    case 'error':
      if (error?.code === 'restarted' || errorWithCode(events, 'restarted'))
        return {
          tone: 'warn',
          text: 'The harness restarted while this turn was running, so recovery closed it (error restarted). Send the message again.',
          reason: 'restarted',
        }
      if (error?.code === 'fixture_exhausted')
        return {
          tone: 'error',
          text: 'The fake provider replays canned replies, and this fixture has no reply scripted for this turn of the session. Start a new session or pick another fixture.',
        }
      return { tone: 'error', text: `The turn failed${error ? `: ${error.code}, ${error.message}` : '.'}` }
    default:
      return null
  }
}

/** A system notice (blocked, limit, error, cancelled) as a bordered Marker; tone on the icon. */
function SystemNotice({ notice }: { notice: Notice }) {
  const Icon = notice.tone === 'error' ? OctagonX : notice.tone === 'warn' ? TriangleAlert : Info
  const tone = notice.tone === 'error' ? 'text-destructive' : notice.tone === 'warn' ? 'text-state-waiting' : undefined
  return (
    <Marker variant="border" role="status" data-testid="system-notice" data-tone={notice.tone} data-reason={notice.reason}>
      <MarkerIcon className={tone}>
        <Icon />
      </MarkerIcon>
      <MarkerContent className={notice.tone === 'error' ? 'text-destructive' : undefined}>{notice.text}</MarkerContent>
    </Marker>
  )
}

/** A Message action: a ghost icon button with a tooltip (Replay now; P4 adds Good and Bad). */
function MessageAction({ label, children, ...props }: { label: string; children: ReactElement } & Omit<ComponentProps<typeof Button>, 'children'>) {
  return (
    <Hint label={label} side="top">
      <Button variant="ghost" size="icon-sm" className="text-muted-foreground" aria-label={label} {...props}>
        {children}
      </Button>
    </Hint>
  )
}

function TurnView({ turn, index, state, live, replaying }: { turn: ChatTurn; index: number; state?: TurnState; live: boolean; replaying: boolean }) {
  const decide = useApp((s) => s.decide)
  const resume = useApp((s) => s.resume)
  const enterReplay = useApp((s) => s.enterReplay)
  const diagram = useApp((s) => s.diagram)
  const phase = state?.phase ?? (turn.summary?.status === 'done' ? 'done' : turn.summary ? 'running' : 'idle')
  const reply = state?.reply || (replaying ? '' : (turn.summary?.reply ?? ''))
  const exit = state?.exit ?? (replaying ? null : turn.summary?.exit)
  const notice = noticeFor(exit, state)
  const finished = phase === 'done' || phase === 'error'
  const working = !finished && !reply
  const streaming = !finished && !!reply && live && !replaying
  const paused = state?.paused
  const pausedLabel = paused ? (diagram?.nodes.find((n) => n.id === paused.nodeId)?.label ?? paused.nodeId) : ''
  const started = turn.summary?.started_at ? new Date(turn.summary.started_at) : turn.createdAt ? new Date(turn.createdAt) : null
  const openDoc = useApp((s) => s.openDoc)
  const dev = useApp((s) => s.capabilities?.mode === 'dev')
  const [reveal, setReveal] = useState(false)
  const events = state?.events
  const input = useMemo(() => (events ? inputGuardFacts(events) : null), [events])
  const output = useMemo(() => (events ? outputGuardFacts(events) : null), [events])
  const citations = useMemo(() => (events ? citationsOf(events) : []), [events])
  // The message as the model saw it (placeholders), and the values behind them, read by lining
  // it up with what this browser sent. Only then can the reply show the placeholders again.
  const seen = turn.summary?.message ?? null
  const redacted = !!input && Object.keys(input.redactions).length > 0 && !!seen && seen !== turn.userText
  const values = useMemo(() => (redacted && turn.userText ? placeholderValues(turn.userText, seen!) : {}), [redacted, turn.userText, seen])
  const masked = output && output.restored > 0 && Object.keys(values).length ? withPlaceholders(reply, values) : reply
  const canReveal = dev && masked !== reply
  const shownReply = canReveal && !reveal ? masked : reply

  return (
    <div className="flex flex-col gap-4" data-testid="chat-turn" data-turn-id={turn.turnId} data-phase={phase}>
      <Marker variant="separator" className="text-xs">
        <MarkerContent>
          Turn {index + 1}
          {started && Number.isFinite(started.getTime()) ? ` · ${started.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
        </MarkerContent>
      </Marker>
      <Message align="end">
        <MessageContent className="gap-1.5">
          <Bubble variant="muted" align="end">
            <BubbleContent data-testid="user-message">
              {turn.userText ?? <span className="text-muted-foreground italic">Message sent from another browser</span>}
            </BubbleContent>
          </Bubble>
          {redacted && (
            <p className="max-w-[80%] self-end text-right text-xs text-muted-foreground" data-testid="model-saw">
              <span className="font-medium">The model saw: </span>
              {splitPlaceholders(seen!).map((part, i) =>
                part.placeholder ? (
                  <mark key={i} className="rounded-md bg-state-waiting/15 px-1 font-mono text-foreground" data-testid="placeholder">
                    {part.text}
                  </mark>
                ) : (
                  <span key={i}>{part.text}</span>
                ),
              )}
            </p>
          )}
          {input && (input.action !== 'pass' || Object.keys(input.redactions).length > 0 || input.warnings.length > 0 || (input.injectionScore ?? 0) > 0 || dev) && (
            <InputGuardBadges facts={input} />
          )}
        </MessageContent>
      </Message>
      <Message align="start">
        <MessageContent className="gap-3">
          {state && <ToolTrace events={state.events} turnOver={finished} />}
          {turn.reconnected && state?.phase === 'paused' && state.paused?.reason === 'approval' && (
            <Marker variant="border" role="status" data-testid="reattached-notice">
              <MarkerIcon className="text-state-waiting">
                <RefreshCcw />
              </MarkerIcon>
              <MarkerContent>
                The event stream dropped and came back, and this turn is still waiting for your decision. A pending approval survives a restart of the harness.
              </MarkerContent>
            </Marker>
          )}
          {state?.approval && <ApprovalCard approval={state.approval} interactive={live && !replaying && phase === 'paused'} onDecide={decide} />}
          {live && !replaying && paused?.reason === 'breakpoint' && (
            <StepControls nodeId={paused.nodeId} nodeLabel={pausedLabel} seq={paused.seq} onResume={resume} />
          )}
          {working && (
            <Marker role="status" data-testid="assistant-working">
              <MarkerIcon>
                <Spinner role={undefined} aria-hidden className="motion-reduce:animate-none" />
              </MarkerIcon>
              <MarkerContent className="shimmer motion-reduce:shimmer-none">{paused ? 'Waiting…' : 'Walking the harness…'}</MarkerContent>
            </Marker>
          )}
          {reply && (
            <div
              className="text-sm leading-relaxed whitespace-pre-wrap"
              data-testid="assistant-message"
              data-masked={canReveal && !reveal ? 'true' : 'false'}
              aria-live={live ? 'polite' : undefined}
            >
              {finished ? <ReplyText text={shownReply} citations={citations} onOpen={(c) => openDoc(c.doc, c.chunk)} /> : reply}
            </div>
          )}
          {reply && finished && output && <OutputGuardBadges facts={output} />}
          {reply && canReveal && finished && (
            <label className="flex items-center gap-1.5 text-xs text-muted-foreground" data-testid="reveal-toggle">
              <Switch size="sm" checked={reveal} onCheckedChange={setReveal} aria-label="Show the reply with your values restored (dev mode)" />
              <Eye className="size-3.5" aria-hidden />
              Show restored values (dev only)
            </label>
          )}
          {reply && <Sources citations={finished ? citations : []} onOpen={(c) => openDoc(c.doc, c.chunk)} />}
          {state && finished && <MemoryTrace events={state.events} />}
          {notice && <SystemNotice notice={notice} />}
          {streaming && (
            <MessageFooter className="px-0">
              <span className="shimmer motion-reduce:shimmer-none" role="status">
                Streaming the reply…
              </span>
            </MessageFooter>
          )}
          {finished && !replaying && (
            <MessageFooter className="-ml-1.5 px-0" data-testid="message-actions">
              <MessageAction label="Replay this turn on the diagram" onClick={() => void enterReplay(turn.turnId)} data-testid="replay-button">
                <Play />
              </MessageAction>
            </MessageFooter>
          )}
        </MessageContent>
      </Message>
    </div>
  )
}

export function ChatPanel() {
  const order = useApp((s) => s.order)
  const turns = useApp((s) => s.turns)
  const liveTurnId = useApp((s) => s.liveTurnId)
  const stopRequested = useApp((s) => s.stopRequested)
  const replay = useApp((s) => s.replay)
  const capabilities = useApp((s) => s.capabilities)
  const sending = useApp((s) => s.sending)
  const send = useApp((s) => s.send)
  const stop = useApp((s) => s.stop)
  const exitReplay = useApp((s) => s.exitReplay)
  const replayState = useDisplayTurn()

  const live = liveTurnId ? turns[liveTurnId]?.state : undefined
  const busy = !!live && (live.phase === 'running' || live.phase === 'paused')
  const visible = useMemo(() => {
    if (!replay) return order
    const i = order.indexOf(replay.turnId)
    return i >= 0 ? order.slice(0, i + 1) : order
  }, [order, replay])

  const suggestions = visible.length === 0 && (
    <div className="-mx-3 flex scroll-fade-x no-scrollbar gap-2 overflow-x-auto px-3" role="group" aria-label="Try these">
      {TRY_THESE.map((t) => (
        <Button key={t} type="button" variant="outline" size="sm" className="shrink-0" onClick={() => void send(t)} disabled={busy || sending} data-testid="try-prompt">
          {t}
        </Button>
      ))}
    </div>
  )

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label="Chat">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b px-4">
        <MessagesSquare className="size-4 text-muted-foreground" aria-hidden />
        <h2 className="text-sm font-medium">Chat</h2>
        <span className="text-xs text-muted-foreground">{order.length ? `${order.length} turn${order.length === 1 ? '' : 's'}` : 'new session'}</span>
        {replay && (
          <Button variant="outline" size="sm" className="ml-auto" onClick={exitReplay} aria-label="Leave replay and return to live">
            Back to live
          </Button>
        )}
      </div>
      {visible.length === 0 ? (
        <Empty className="min-h-0 p-8">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <Route />
            </EmptyMedia>
            <EmptyTitle className="text-base">Walk the harness</EmptyTitle>
            <EmptyDescription>Send a message and watch it walk through the harness on the right, one node at a time.</EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <MessageScrollerProvider autoScroll defaultScrollPosition="end">
          <MessageScroller className="min-h-0 flex-1">
            <MessageScrollerViewport aria-label="Messages">
              <MessageScrollerContent className="gap-6 px-4 py-4">
                {visible.map((id, index) => {
                  const turn = turns[id]!
                  const isReplayed = replay?.turnId === id
                  return (
                    <MessageScrollerItem key={id} messageId={id} scrollAnchor>
                      <TurnView turn={turn} index={index} state={isReplayed ? replayState : turn.state} live={id === liveTurnId} replaying={isReplayed} />
                    </MessageScrollerItem>
                  )
                })}
              </MessageScrollerContent>
            </MessageScrollerViewport>
            <MessageScrollerButton aria-label="Scroll to the latest message" />
          </MessageScroller>
        </MessageScrollerProvider>
      )}
      <Composer
        busy={busy}
        stopping={busy && stopRequested === liveTurnId}
        sending={sending}
        canStop={capabilities?.features.cancel !== false}
        maxChars={capabilities?.limits.max_message_chars ?? 4000}
        onSend={send}
        onStop={stop}
        suggestions={suggestions}
      />
    </section>
  )
}
