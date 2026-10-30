// EventSource wrapper for GET /v1/turns/{id}/events.
//
// - Listens for every event type by name (the server sends `event: <type>`).
// - Tracks the cursor from each message's `id:` (which is the event's seq). If the connection
//   drops before turn_end, it reconnects with `after=<cursor>` so nothing is lost or repeated;
//   a browser-level reconnect carrying Last-Event-ID is cut short in favour of that, so the
//   cursor is explicit whatever the server's precedence between the two.
// - Drops any event at or below the cursor, so a replayed overlap is harmless.
// - Closes itself on turn_end, and after the replay when follow=false.
import { eventsPath, type EventSourceLike, type Transport } from './client'
import { EVENT_TYPES, type EvalStreamEvent, type HarnessEvent, type OpsEvent } from './types'

export type StreamEnd = 'turn_end' | 'replayed' | 'closed' | 'failed'

export interface StreamOptions {
  after?: number
  follow?: boolean
  /** Another path serving a turn's stream (an eval case's `events_url`); default the turn's own. */
  path?: string
  /** Returns a list of problems, or null when the event is valid. Dev only. */
  validate?: (ev: unknown) => string[] | null
  maxRetries?: number
  retryBaseMs?: number
}

export interface StreamHandlers {
  onEvent: (ev: HarnessEvent) => void
  onStatus?: (status: 'connecting' | 'open' | 'reconnecting') => void
  onInvalid?: (ev: unknown, problems: string[]) => void
  onEnd?: (why: StreamEnd) => void
}

export interface TurnStream {
  close: () => void
  readonly cursor: number
}

/** The turn's own events path, or another path serving the same SSE format, with the cursor. */
function streamPath(turnId: string, path: string | undefined, q: { after?: number; follow?: boolean }): string {
  if (!path) return eventsPath(turnId, q)
  const own = eventsPath(turnId, q)
  const qs = own.includes('?') ? own.slice(own.indexOf('?') + 1) : ''
  return qs ? `${path}${path.includes('?') ? '&' : '?'}${qs}` : path
}

export function openTurnStream(
  t: Transport,
  turnId: string,
  opts: StreamOptions,
  handlers: StreamHandlers,
): TurnStream {
  const follow = opts.follow !== false
  const maxRetries = opts.maxRetries ?? 8
  const base = opts.retryBaseMs ?? 400
  let cursor = opts.after ?? -1
  let es: EventSourceLike | null = null
  let retries = 0
  let ended = false
  let timer: ReturnType<typeof setTimeout> | null = null

  const finish = (why: StreamEnd) => {
    if (ended) return
    ended = true
    if (timer) clearTimeout(timer)
    es?.close()
    es = null
    handlers.onEnd?.(why)
  }

  const onMessage = (msg: MessageEvent) => {
    // The harness has an event type named `error`, and so does EventSource: a dropped
    // connection reaches the `error` listener too, as a plain Event without data. That is the
    // transport's business (onerror below), not an invalid harness event.
    if (ended || typeof msg.data !== 'string') return
    let ev: HarnessEvent
    try {
      ev = JSON.parse(String(msg.data)) as HarnessEvent
    } catch {
      handlers.onInvalid?.(msg.data, ['data is not JSON'])
      return
    }
    const idSeq = msg.lastEventId ? Number(msg.lastEventId) : NaN
    const seq = typeof ev.seq === 'number' ? ev.seq : idSeq
    if (!Number.isFinite(seq) || seq <= cursor) return
    if (opts.validate) {
      const problems = opts.validate(ev)
      if (problems) handlers.onInvalid?.(ev, problems)
    }
    cursor = seq
    retries = 0
    handlers.onEvent(ev)
    if (ev.type === 'turn_end') finish('turn_end')
  }

  const connect = () => {
    if (ended) return
    handlers.onStatus?.(retries === 0 ? 'connecting' : 'reconnecting')
    const url = `${t.baseUrl}${streamPath(turnId, opts.path, { after: cursor >= 0 ? cursor : undefined, follow })}`
    const source = new t.EventSource(url, { withCredentials: true })
    es = source
    for (const type of EVENT_TYPES) source.addEventListener(type, onMessage)
    source.onmessage = onMessage
    source.onopen = () => handlers.onStatus?.('open')
    source.onerror = () => {
      if (ended || es !== source) return
      source.close()
      es = null
      if (!follow) {
        // The server closes a follow=false stream after the replay.
        finish('replayed')
        return
      }
      if (retries >= maxRetries) {
        finish('failed')
        return
      }
      const delay = Math.min(5000, base * 2 ** retries)
      retries += 1
      handlers.onStatus?.('reconnecting')
      timer = setTimeout(connect, delay)
    }
  }

  connect()
  return {
    close: () => finish('closed'),
    get cursor() {
      return cursor
    },
  }
}

/** Load a finished turn's events in one go (replay mode). */
export function loadTurnEvents(
  t: Transport,
  turnId: string,
  validate?: StreamOptions['validate'],
  path?: string,
): Promise<HarnessEvent[]> {
  return new Promise((resolve, reject) => {
    const events: HarnessEvent[] = []
    openTurnStream(
      t,
      turnId,
      { follow: false, validate, path },
      {
        onEvent: (ev) => events.push(ev),
        onEnd: (why) => {
          if (why === 'failed' && events.length === 0) reject(new Error('could not load the turn'))
          else resolve(events)
        },
      },
    )
  })
}

export const EVAL_STREAM_TYPES = ['eval_run_started', 'eval_case_finished', 'eval_run_finished'] as const

/**
 * GET /v1/evals/runs/{id}/events: the progress of one eval run. The server closes it after
 * `eval_run_finished`; a drop before that ends the stream too (`lost`), and the caller reads the
 * run's state back from GET /v1/evals/runs/{id} either way, so nothing is retried here.
 */
export function openEvalStream(
  t: Transport,
  runId: string,
  handlers: { onEvent: (ev: EvalStreamEvent) => void; onEnd: (why: 'finished' | 'lost' | 'closed') => void },
): { close: () => void } {
  let ended = false
  const source = new t.EventSource(`${t.baseUrl}/v1/evals/runs/${encodeURIComponent(runId)}/events`, { withCredentials: true })
  const finish = (why: 'finished' | 'lost' | 'closed') => {
    if (ended) return
    ended = true
    source.close()
    handlers.onEnd(why)
  }
  const onMessage = (msg: MessageEvent) => {
    if (ended || typeof msg.data !== 'string') return
    let ev: EvalStreamEvent
    try {
      ev = JSON.parse(msg.data) as EvalStreamEvent
    } catch {
      return
    }
    if (!ev.type && msg.type && msg.type !== 'message') ev = { ...ev, type: msg.type as EvalStreamEvent['type'] }
    handlers.onEvent(ev)
    if (ev.type === 'eval_run_finished') finish('finished')
  }
  for (const type of EVAL_STREAM_TYPES) source.addEventListener(type, onMessage)
  source.onmessage = onMessage
  source.onerror = () => finish('lost')
  return { close: () => finish('closed') }
}

export const OPS_EVENT_TYPES = ['eval_run_started', 'eval_case_finished', 'eval_run_finished', 'feedback_recorded', 'artifact_changed', 'artifact_released'] as const

/**
 * GET /v1/events: every ops event of the tenant (feedback, artifact changes, releases, eval run
 * progress), from whichever process wrote it. The browser's EventSource reconnects by itself
 * with Last-Event-ID, which the server honours, so a drop loses nothing; events at or below the
 * last cursor are dropped in case one is sent twice. Open while the page is.
 */
export function openOpsStream(
  t: Transport,
  handlers: { onEvent: (ev: OpsEvent) => void; onStatus?: (open: boolean) => void },
): { close: () => void } {
  let closed = false
  let cursor = -1
  const source = new t.EventSource(`${t.baseUrl}/v1/events`, { withCredentials: true })
  const onMessage = (msg: MessageEvent) => {
    if (closed || typeof msg.data !== 'string') return
    let ev: OpsEvent
    try {
      ev = JSON.parse(msg.data) as OpsEvent
    } catch {
      return
    }
    const at = typeof ev.cursor === 'number' ? ev.cursor : Number(msg.lastEventId)
    if (Number.isFinite(at)) {
      if (at <= cursor) return
      cursor = at
    }
    handlers.onEvent(ev)
  }
  for (const type of OPS_EVENT_TYPES) source.addEventListener(type, onMessage)
  source.onmessage = onMessage
  source.onopen = () => handlers.onStatus?.(true)
  source.onerror = () => {
    if (!closed) handlers.onStatus?.(false)
  }
  return {
    close: () => {
      closed = true
      source.close()
    },
  }
}
