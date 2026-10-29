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
import { EVENT_TYPES, type HarnessEvent } from './types'

export type StreamEnd = 'turn_end' | 'replayed' | 'closed' | 'failed'

export interface StreamOptions {
  after?: number
  follow?: boolean
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
    const url = `${t.baseUrl}${eventsPath(turnId, { after: cursor >= 0 ? cursor : undefined, follow })}`
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
): Promise<HarnessEvent[]> {
  return new Promise((resolve, reject) => {
    const events: HarnessEvent[] = []
    openTurnStream(
      t,
      turnId,
      { follow: false, validate },
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
