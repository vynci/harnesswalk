import { useCallback, useMemo, useState, type ComponentProps, type ReactElement } from 'react'
import type { Citation, TurnNotice } from '@/api/types'
import { Coins, Eye, History, Info, MessagesSquare, OctagonX, Play, RefreshCcw, Route, TriangleAlert } from 'lucide-react'
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
import type { OpsMark } from '@/state/ops'
import { turnReport } from '@/state/turnReport'
import { useApp, type ChatTurn } from '@/state/store'
import { FeedbackActions, ScoreChips } from './Feedback'
import { OpsMarker } from './OpsMarker'
import { ApprovalCard } from './ApprovalCard'
import { Composer } from './Composer'
import { StepControls } from './StepControls'
import { ReplyMarkdown, Sources } from './Citations'
import { Plan, Reasoning } from './Reasoning'
import { InputGuardBadges, OutputGuardBadges } from './Guardrails'
import { MemoryTrace, ToolTrace } from './ToolTrace'
import { usePackPrompts } from './usePackPrompts'
import { TurnReportRow } from './TurnReport'
import { DiagnoseAction } from '@/inspector/Diagnosis'
import { packTitle } from '@/state/packs'

/** The `_starter` pack's prompts, shown until the API lists packs (see usePackPrompts). */
const TRY_THESE = [
  'What are your support hours?',
  'Where is order A-1001?',
  'Please cancel order A-1002.',
  'Can I return order A-1003, the Trailhead backpack?',
]

const NO_CITATIONS: Citation[] = []

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

/** What a reply notice says when the API sent a reason without words. */
const REPLY_NOTICE_TEXT: Record<string, string> = {
  budget_exhausted: "Today's shared budget for Claude models is spent, so the rest of this turn ran on the fake model. It resets at 00:00 UTC.",
}

/**
 * P6: the walk's notes about the reply (`turn_end.summary.notices`), as a small line under it:
 * a turn that fell through to the fake model when the shared budget ran out, say.
 */
function ReplyNotes({ notices }: { notices: TurnNotice[] }) {
  if (notices.length === 0) return null
  return (
    <div className="flex flex-col gap-1" data-testid="reply-notices">
      {notices.map((n) => (
        <Marker key={n.reason} role="note" className="text-xs text-muted-foreground" data-testid="reply-notice" data-reason={n.reason}>
          <MarkerIcon className={n.reason === 'budget_exhausted' ? 'text-state-waiting' : undefined}>
            {n.reason === 'budget_exhausted' ? <Coins className="size-3.5" aria-hidden /> : <Info className="size-3.5" aria-hidden />}
          </MarkerIcon>
          <MarkerContent>{n.message || REPLY_NOTICE_TEXT[n.reason] || n.reason.replace(/_/g, ' ')}</MarkerContent>
        </Marker>
      ))}
    </div>
  )
}

/** A Message action: a ghost icon button with a tooltip (Replay; Good and Bad are in Feedback.tsx). */
function MessageAction({ label, children, ...props }: { label: string; children: ReactElement } & Omit<ComponentProps<typeof Button>, 'children'>) {
  return (
    <Hint label={label} side="top">
      <Button variant="ghost" size="icon-sm" className="text-muted-foreground" aria-label={label} {...props}>
        {children}
      </Button>
    </Hint>
  )
}

function TurnView({
  turn,
  index,
  state,
  live,
  replaying,
  latest,
}: {
  turn: ChatTurn
  index: number
  state?: TurnState
  live: boolean
  replaying: boolean
  latest: boolean
}) {
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
  const onCite = useCallback((c: Citation) => openDoc(c.doc, c.chunk), [openDoc])
  const dev = useApp((s) => s.capabilities?.mode === 'dev')
  const feedbackOn = useApp((s) => s.capabilities?.features.feedback === true)
  const feedback = turn.feedback ?? turn.summary?.feedback ?? null
  const checkpoint = turn.summary?.checkpoint
  const [reveal, setReveal] = useState(false)
  const events = state?.events
  // The divider's report: a replayed turn's stays at the replay's cursor, so no stored summary.
  const report = useMemo(() => turnReport(state, replaying ? null : turn.summary), [state, replaying, turn.summary])
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
      <TurnReportRow turnId={turn.turnId} index={index} started={started} report={report} latest={latest} />
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
                  <mark key={i} className="rounded-xs bg-state-waiting/15 px-1 font-mono text-foreground" data-testid="placeholder">
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
          {/* P4 fills these from the stream (thinking, the loop's plan); empty slots until then. */}
          <Reasoning reasoning={null} />
          <Plan plan={null} />
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
              className="min-w-0 text-sm leading-relaxed"
              data-testid="assistant-message"
              data-masked={canReveal && !reveal ? 'true' : 'false'}
              aria-live={live ? 'polite' : undefined}
            >
              <ReplyMarkdown text={finished ? shownReply : reply} citations={finished ? citations : NO_CITATIONS} onOpen={onCite} />
            </div>
          )}
          {finished && <ReplyNotes notices={state?.notices ?? (replaying ? [] : (turn.summary?.notices ?? []))} />}
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
          {checkpoint?.restored && (
            <Marker variant="border" role="status" data-testid="checkpoint-notice">
              <MarkerIcon>
                <History />
              </MarkerIcon>
              <MarkerContent>
                The harness restarted during this turn and resumed it from its checkpoint at step {checkpoint.step}, with its output schema and pending memory proposals.
              </MarkerContent>
            </Marker>
          )}
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
              <Hint label="Replay this turn on the diagram" side="top">
                <Button variant="ghost" size="xs" className="gap-1 text-muted-foreground" onClick={() => void enterReplay(turn.turnId)} data-testid="replay-button">
                  <Play />
                  Replay
                </Button>
              </Hint>
              {feedbackOn && <FeedbackActions turnId={turn.turnId} feedback={feedback} />}
              <ScoreChips scores={turn.summary?.scores} />
              {feedback?.note && (
                <span className="ml-1 min-w-0 truncate font-normal" title={feedback.note} data-testid="feedback-note-text">
                  “{feedback.note}”
                </span>
              )}
            </MessageFooter>
          )}
          {/* P5: a turn rated bad, or one that did not answer, can be diagnosed from its trace. */}
          {finished && !replaying && (turn.diagnosis || turn.summary?.diagnosis || feedback?.rating === 'bad' || (exit && exit !== 'answered')) && (
            <div className="flex flex-col" data-testid="turn-diagnose">
              <DiagnoseAction stored={turn.diagnosis ?? turn.summary?.diagnosis} target={{ turn_id: turn.turnId }} label="Diagnose this turn" />
            </div>
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
  const marks = useApp((s) => s.marks)
  const pack = useApp((s) => s.pack)
  const packs = useApp((s) => s.packs)
  const prompts = usePackPrompts(pack) ?? TRY_THESE
  const packInfo = packs?.find((p) => p.id === pack)
  // Markers go after the turn they followed; one whose turn is not here goes first.
  const marksAfter = useMemo(() => {
    const out = new Map<string | null, OpsMark[]>()
    for (const m of marks) {
      const key = m.afterTurnId && order.includes(m.afterTurnId) ? m.afterTurnId : null
      out.set(key, [...(out.get(key) ?? []), m])
    }
    return out
  }, [marks, order])

  const live = liveTurnId ? turns[liveTurnId]?.state : undefined
  const busy = !!live && (live.phase === 'running' || live.phase === 'paused')
  const visible = useMemo(() => {
    if (!replay) return order
    const i = order.indexOf(replay.turnId)
    return i >= 0 ? order.slice(0, i + 1) : order
  }, [order, replay])

  const suggestions = visible.length === 0 && (
    <div className="-mx-3 flex scroll-fade-x no-scrollbar gap-2 overflow-x-auto px-3" role="group" aria-label="Try these">
      {prompts.map((t) => (
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
        {replay?.label && (
          <span className="min-w-0 truncate text-xs text-muted-foreground" data-testid="replay-label">
            Replaying {replay.label} on the diagram
          </span>
        )}
        {replay && (
          <Button variant="outline" size="sm" className="ml-auto shrink-0" onClick={exitReplay} aria-label="Leave replay and return to live">
            Back to live
          </Button>
        )}
      </div>
      {visible.length === 0 ? (
        <>
          {(marksAfter.get(null) ?? []).length > 0 && (
            <div className="flex flex-col gap-3 px-4 pt-4">
              {marksAfter.get(null)!.map((m) => (
                <OpsMarker key={m.id} mark={m} interactive />
              ))}
            </div>
          )}
          <Empty className="min-h-0 p-8">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <Route />
              </EmptyMedia>
              <EmptyTitle className="text-base">Walk the harness</EmptyTitle>
              <EmptyDescription>Send a message and watch it walk through the harness on the right, one node at a time.</EmptyDescription>
              {packInfo && (
                <EmptyDescription data-testid="empty-pack">
                  On {packTitle(packs, pack)}
                  {packInfo.feature ? `: ${packInfo.feature.toLowerCase()}` : ''}.
                </EmptyDescription>
              )}
            </EmptyHeader>
          </Empty>
        </>
      ) : (
        <MessageScrollerProvider autoScroll defaultScrollPosition="end">
          <MessageScroller className="min-h-0 flex-1">
            <MessageScrollerViewport aria-label="Messages">
              <MessageScrollerContent className="gap-6 px-4 py-4">
                {(marksAfter.get(null) ?? []).map((m) => (
                  <OpsMarker key={m.id} mark={m} interactive={!replay} />
                ))}
                {visible.map((id, index) => {
                  const turn = turns[id]!
                  const isReplayed = replay?.turnId === id
                  return (
                    <MessageScrollerItem key={id} messageId={id} scrollAnchor>
                      <TurnView
                        turn={turn}
                        index={index}
                        state={isReplayed ? replayState : turn.state}
                        live={id === liveTurnId}
                        replaying={isReplayed}
                        latest={index === order.length - 1}
                      />
                      {!isReplayed && (marksAfter.get(id) ?? []).length > 0 && (
                        <div className="mt-6 flex flex-col gap-3">
                          {marksAfter.get(id)!.map((m) => (
                            <OpsMarker key={m.id} mark={m} interactive={!replay} />
                          ))}
                        </div>
                      )}
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
