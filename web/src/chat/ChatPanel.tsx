import { useEffect, useMemo, useRef } from 'react'
import { History, Info, OctagonX, Play, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { useDisplayTurn } from '@/state/hooks'
import type { TurnState } from '@/state/reducer'
import { useApp, type ChatTurn } from '@/state/store'
import { ApprovalCard } from './ApprovalCard'
import { Composer } from './Composer'
import { StepControls } from './StepControls'

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
      return { tone: 'error', text: `The turn failed${error ? `: ${error.code}, ${error.message}` : '.'}` }
    default:
      return null
  }
}

function SystemNotice({ notice }: { notice: Notice }) {
  const Icon = notice.tone === 'error' ? OctagonX : notice.tone === 'warn' ? TriangleAlert : Info
  return (
    <div
      role="status"
      className="mx-auto flex max-w-[90%] items-start gap-2 rounded-md border bg-muted/60 px-3 py-1.5 text-xs text-muted-foreground"
      data-testid="system-notice"
      data-tone={notice.tone}
    >
      <Icon className="mt-0.5 size-3.5 shrink-0" aria-hidden />
      <span>{notice.text}</span>
    </div>
  )
}

function TurnView({ turn, state, live, replaying }: { turn: ChatTurn; state?: TurnState; live: boolean; replaying: boolean }) {
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
  const paused = state?.paused
  const pausedLabel = paused ? (diagram?.nodes.find((n) => n.id === paused.nodeId)?.label ?? paused.nodeId) : ''

  return (
    <li className="flex flex-col gap-2" data-testid="chat-turn" data-turn-id={turn.turnId}>
      <div className="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-sm text-primary-foreground" data-testid="user-message">
        {turn.userText ?? <span className="italic opacity-80">Message sent from another browser</span>}
      </div>
      {state?.approval && (
        <ApprovalCard approval={state.approval} interactive={live && !replaying && phase === 'paused'} onDecide={decide} />
      )}
      {live && !replaying && paused?.reason === 'breakpoint' && <StepControls nodeLabel={pausedLabel} onResume={resume} />}
      {(reply || working) && (
        <div className="group mr-auto flex max-w-[90%] flex-col gap-1">
          <div
            className="rounded-2xl rounded-bl-sm bg-muted px-3 py-2 text-sm whitespace-pre-wrap"
            data-testid="assistant-message"
            aria-live={live ? 'polite' : undefined}
          >
            {reply || <span className="text-muted-foreground">{paused ? 'Waiting…' : 'Walking the harness…'}</span>}
          </div>
          {/* Citation chips arrive with Knowledge in P3. */}
          <div data-slot="citations" hidden />
        </div>
      )}
      {notice && <SystemNotice notice={notice} />}
      {finished && !replaying && (
        <Button
          variant="ghost"
          size="sm"
          className="mr-auto h-6 px-2 text-xs text-muted-foreground"
          onClick={() => void enterReplay(turn.turnId)}
          aria-label="Replay this turn on the diagram"
          data-testid="replay-button"
        >
          <Play aria-hidden className="size-3" /> Replay
        </Button>
      )}
    </li>
  )
}

export function ChatPanel() {
  const order = useApp((s) => s.order)
  const turns = useApp((s) => s.turns)
  const liveTurnId = useApp((s) => s.liveTurnId)
  const replay = useApp((s) => s.replay)
  const capabilities = useApp((s) => s.capabilities)
  const sending = useApp((s) => s.sending)
  const send = useApp((s) => s.send)
  const stop = useApp((s) => s.stop)
  const exitReplay = useApp((s) => s.exitReplay)
  const replayState = useDisplayTurn()
  const listRef = useRef<HTMLDivElement>(null)

  const live = liveTurnId ? turns[liveTurnId]?.state : undefined
  const busy = !!live && (live.phase === 'running' || live.phase === 'paused')
  const visible = useMemo(() => {
    if (!replay) return order
    const i = order.indexOf(replay.turnId)
    return i >= 0 ? order.slice(0, i + 1) : order
  }, [order, replay])
  const lastState = visible.length ? turns[visible[visible.length - 1]!]?.state : undefined

  useEffect(() => {
    const el = listRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [visible.length, lastState?.reply, lastState?.approval, lastState?.paused, lastState?.phase, replay?.cursor])

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
      <div ref={listRef} className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {visible.length === 0 ? (
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
        ) : (
          <ol className="flex flex-col gap-4" aria-label="Messages">
            {visible.map((id) => {
              const turn = turns[id]!
              const isReplayed = replay?.turnId === id
              return (
                <TurnView
                  key={id}
                  turn={turn}
                  state={isReplayed ? replayState : turn.state}
                  live={id === liveTurnId}
                  replaying={isReplayed}
                />
              )
            })}
          </ol>
        )}
      </div>
      <Composer
        busy={busy}
        sending={sending}
        canStop={capabilities?.features.cancel !== false}
        maxChars={capabilities?.limits.max_message_chars ?? 4000}
        onSend={send}
        onStop={stop}
      />
    </section>
  )
}
