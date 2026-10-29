import { useMemo } from 'react'
import { History, Info, OctagonX, Play, TriangleAlert } from 'lucide-react'
import { Bubble, BubbleContent } from '@/components/ui/bubble'
import { Button } from '@/components/ui/button'
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
import { useDisplayTurn } from '@/state/hooks'
import type { TurnState } from '@/state/reducer'
import { useApp, type ChatTurn } from '@/state/store'
import { ApprovalCard } from './ApprovalCard'
import { Composer } from './Composer'
import { StepControls } from './StepControls'
import { Sources, ToolTrace } from './ToolTrace'

const TRY_THESE = ['What are your support hours?', 'Where is order A-1001?', 'Please cancel order A-1002.']

type Notice = { tone: 'info' | 'warn' | 'error'; text: string }

function noticeFor(exit: TurnState['exit'] | null | undefined, error?: TurnState['error']): Notice | null {
  switch (exit) {
    case 'blocked':
      return { tone: 'warn', text: 'Blocked by a guardrail. The model never saw this message.' }
    case 'limit':
      return { tone: 'warn', text: 'The loop hit a limit (steps, tokens, budget or time) before it finished.' }
    case 'cancelled':
      return { tone: 'info', text: 'Turn cancelled. Open spans were closed and nothing else ran.' }
    case 'error':
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

/** A system notice (blocked, limit, error, cancelled) as a bordered Marker. */
function SystemNotice({ notice }: { notice: Notice }) {
  const Icon = notice.tone === 'error' ? OctagonX : notice.tone === 'warn' ? TriangleAlert : Info
  return (
    <Marker
      variant="border"
      role="status"
      className={notice.tone === 'error' ? 'text-xs text-destructive' : notice.tone === 'warn' ? 'text-xs text-state-waiting' : 'text-xs'}
      data-testid="system-notice"
      data-tone={notice.tone}
    >
      <MarkerIcon>
        <Icon className="size-3.5" />
      </MarkerIcon>
      <MarkerContent className="text-muted-foreground">{notice.text}</MarkerContent>
    </Marker>
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
  const notice = noticeFor(exit, state?.error)
  const finished = phase === 'done' || phase === 'error'
  const working = !finished && !reply
  const streaming = !finished && !!reply && live && !replaying
  const paused = state?.paused
  const pausedLabel = paused ? (diagram?.nodes.find((n) => n.id === paused.nodeId)?.label ?? paused.nodeId) : ''
  const started = turn.summary?.started_at ? new Date(turn.summary.started_at) : turn.createdAt ? new Date(turn.createdAt) : null

  return (
    <div className="flex flex-col gap-3" data-testid="chat-turn" data-turn-id={turn.turnId} data-phase={phase}>
      <Marker variant="separator" className="text-[11px]">
        <MarkerContent>
          Turn {index + 1}
          {started && Number.isFinite(started.getTime()) ? ` · ${started.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}` : ''}
        </MarkerContent>
      </Marker>
      <Message align="end">
        <MessageContent>
          <Bubble variant="default" align="end">
            <BubbleContent data-testid="user-message">
              {turn.userText ?? <span className="italic opacity-80">Message sent from another browser</span>}
            </BubbleContent>
          </Bubble>
        </MessageContent>
      </Message>
      {state && <ToolTrace events={state.events} turnOver={finished} />}
      {state?.approval && <ApprovalCard approval={state.approval} interactive={live && !replaying && phase === 'paused'} onDecide={decide} />}
      {live && !replaying && paused?.reason === 'breakpoint' && (
        <StepControls nodeId={paused.nodeId} nodeLabel={pausedLabel} seq={paused.seq} onResume={resume} />
      )}
      {working && (
        <Marker role="status" className="text-xs" data-testid="assistant-working">
          <MarkerIcon>
            <Spinner role={undefined} aria-hidden className="size-3.5 motion-reduce:animate-none" />
          </MarkerIcon>
          <MarkerContent className="shimmer">{paused ? 'Waiting…' : 'Walking the harness…'}</MarkerContent>
        </Marker>
      )}
      {reply && (
        <Message align="start">
          <MessageContent>
            <Bubble variant="muted">
              <BubbleContent className="whitespace-pre-wrap" data-testid="assistant-message" aria-live={live ? 'polite' : undefined}>
                {reply}
              </BubbleContent>
            </Bubble>
            {/* Citation chips arrive with Knowledge in P3. */}
            <Sources sources={[]} />
            {streaming && (
              <MessageFooter>
                <span className="shimmer" role="status">
                  Streaming the reply…
                </span>
              </MessageFooter>
            )}
          </MessageContent>
        </Message>
      )}
      {notice && <SystemNotice notice={notice} />}
      {finished && !replaying && (
        <Button
          variant="ghost"
          size="xs"
          className="mr-auto text-muted-foreground"
          onClick={() => void enterReplay(turn.turnId)}
          aria-label="Replay this turn on the diagram"
          data-testid="replay-button"
        >
          <Play aria-hidden /> Replay
        </Button>
      )}
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

  return (
    <section className="flex h-full min-h-0 flex-col" aria-label="Chat">
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <History className="size-4 text-muted-foreground" aria-hidden />
        <h2 className="text-sm font-semibold">Chat</h2>
        <span className="text-xs text-muted-foreground">{order.length ? `${order.length} turn${order.length === 1 ? '' : 's'}` : 'new session'}</span>
        {replay && (
          <Button variant="outline" size="sm" className="ml-auto h-7" onClick={exitReplay} aria-label="Leave replay and return to live">
            Back to live
          </Button>
        )}
      </div>
      {visible.length === 0 ? (
        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
          <div className="mx-auto mt-6 max-w-sm text-center text-sm text-muted-foreground">
            <p>Send a message and watch it walk through the harness on the right.</p>
            <p className="mt-4 text-xs font-medium tracking-wide uppercase">Try these</p>
            <div className="mt-2 flex flex-col gap-2">
              {TRY_THESE.map((t) => (
                <Button key={t} variant="outline" size="sm" onClick={() => void send(t)} disabled={busy || sending} data-testid="try-prompt">
                  {t}
                </Button>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <MessageScrollerProvider autoScroll defaultScrollPosition="end">
          <MessageScroller className="min-h-0 flex-1">
            <MessageScrollerViewport aria-label="Messages">
              <MessageScrollerContent className="gap-5 px-3 py-3">
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
      />
    </section>
  )
}
