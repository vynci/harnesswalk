// Application state. Stream state per turn comes from the pure reducer; this store wires it to
// the API, the stream client, the chat, replay and the view toggles.
import { create } from 'zustand'
import { ApiError, createApi, type Api, type Transport } from '@/api/client'
import { loadTurnEvents, openTurnStream, type TurnStream } from '@/api/stream'
import type { Capabilities, Diagram, HarnessEvent, TurnSummary, View } from '@/api/types'
import { buildEdgeIndex, initialTurnState, reduceEvent, reduceEvents, type EdgeIndex, type TurnState } from './reducer'
import { load, rememberMessage, recallMessages, save } from './persist'

export interface ChatTurn {
  turnId: string
  userText?: string
  summary?: TurnSummary
  /** Reduced events, when this browser has streamed or loaded them. */
  state?: TurnState
  createdAt: number
}

export interface Notice {
  id: number
  level: 'info' | 'error'
  text: string
}

export interface ReplayState {
  turnId: string
  events: HarnessEvent[]
  cursor: number
  playing: boolean
}

export type Theme = 'light' | 'dark' | 'system'
export type Connection = 'idle' | 'connecting' | 'open' | 'reconnecting'

export interface AppState {
  api: Api | null
  validate?: (ev: unknown) => string[] | null
  ready: boolean
  bootError: string | null
  capabilities: Capabilities | null
  diagram: Diagram | null
  edgeIndex: EdgeIndex
  mock: boolean

  view: View
  collapsed: string[]
  stepMode: boolean
  theme: Theme
  provider: string | null
  fixture: string | null
  disabled: string[]

  sessionId: string | null
  turns: Record<string, ChatTurn>
  order: string[]
  liveTurnId: string | null
  /** The turn a Stop was sent for, until that turn ends. */
  stopRequested: string | null
  connection: Connection
  sending: boolean
  invalidEvents: number

  replay: ReplayState | null
  selectedNode: string | null
  notices: Notice[]

  boot: (t: Transport, opts?: { mock?: boolean; validate?: AppState['validate']; seedMessages?: Record<string, string>; sessionId?: string }) => Promise<void>
  send: (text: string) => Promise<void>
  stop: () => Promise<void>
  resume: (mode: 'step' | 'continue') => Promise<void>
  decide: (decision: 'approve' | 'deny') => Promise<void>
  newSession: () => void
  enterReplay: (turnId: string) => Promise<void>
  exitReplay: () => void
  setCursor: (cursor: number) => void
  stepReplay: (delta: number) => void
  setPlaying: (playing: boolean) => void
  selectNode: (id: string | null) => void
  setView: (view: View) => void
  toggleCollapsed: (id: string) => void
  setStepMode: (on: boolean) => void
  setTheme: (t: Theme) => void
  setProvider: (p: string | null) => void
  setFixture: (f: string | null) => void
  toggleDisabled: (id: string) => void
  notify: (level: Notice['level'], text: string) => void
  dismissNotice: (id: number) => void
}

let stream: TurnStream | null = null
let noticeId = 0

/**
 * A fresh session id in the server's own format. The UI always names its session, because a
 * request without one continues the session in the API's cookie: "New session" would then
 * quietly append to the old one.
 */
export function newSessionId(): string {
  const bytes = new Uint8Array(10)
  globalThis.crypto.getRandomValues(bytes)
  return 'sess_' + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

function describe(err: unknown): string {
  if (err instanceof ApiError) return `${err.message} (${err.status} ${err.code})`
  if (err instanceof Error) return err.message
  return String(err)
}

export const useApp = create<AppState>()((set, get) => {
  const updateTurn = (turnId: string, fn: (t: ChatTurn) => ChatTurn) =>
    set((s) => {
      const prev = s.turns[turnId]
      if (!prev) return {}
      return { turns: { ...s.turns, [turnId]: fn(prev) } }
    })

  const addTurn = (turn: ChatTurn) =>
    set((s) => ({
      turns: { ...s.turns, [turn.turnId]: turn },
      order: s.order.includes(turn.turnId) ? s.order : [...s.order, turn.turnId],
    }))

  const follow = (turnId: string, after?: number) => {
    const { api, validate } = get()
    if (!api) return
    stream?.close()
    set({ liveTurnId: turnId, connection: 'connecting' })
    stream = openTurnStream(
      api.transport,
      turnId,
      { after, validate },
      {
        onEvent: (ev) => {
          updateTurn(turnId, (t) => ({ ...t, state: reduceEvent(t.state ?? initialTurnState(), ev, get().edgeIndex) }))
          // Step mode keeps the inspector on the node the walk stopped at.
          if (ev.type === 'node_paused' && ev.summary?.reason === 'breakpoint' && get().selectedNode && ev.node_id) {
            set({ selectedNode: ev.node_id })
          }
        },
        onStatus: (status) => set({ connection: status }),
        onInvalid: (_ev, problems) => {
          set((s) => ({ invalidEvents: s.invalidEvents + 1 }))
          console.warn('[harnesswalk] event failed schema validation', problems)
        },
        onEnd: (why) => {
          set({ connection: 'idle' })
          if (why === 'failed') {
            // Either the network is gone or the API no longer runs this turn (a restart cut it
            // off, so it never ends). Release the composer instead of waiting forever.
            if (get().liveTurnId === turnId) set({ liveTurnId: null })
            get().notify('error', 'Lost the event stream for this turn. Reload to reconnect.')
          }
          if (why === 'turn_end') void refreshSummary(turnId)
        },
      },
    )
  }

  const refreshSummary = async (turnId: string) => {
    const api = get().api
    if (!api) return
    try {
      const summary = await api.turn(turnId)
      updateTurn(turnId, (t) => ({ ...t, summary }))
    } catch {
      // The stream already carried everything the chat needs.
    }
  }

  const loadHistory = async (sessionId: string) => {
    const api = get().api!
    let list: TurnSummary[]
    try {
      list = await api.sessionTurns(sessionId)
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        // Expired or unknown session: start fresh.
        set({ sessionId: null })
        save('session', null)
        return
      }
      throw err
    }
    const messages = recallMessages()
    const oldestFirst = [...list].reverse()
    set({
      turns: Object.fromEntries(
        oldestFirst.map((summary) => [
          summary.turn_id,
          { turnId: summary.turn_id, summary, userText: summary.message ?? messages[summary.turn_id], createdAt: Date.parse(summary.started_at) || 0 },
        ]),
      ),
      order: oldestFirst.map((t) => t.turn_id),
    })
    // A turn that is still running (a reload mid-turn) is picked up from the start of its stream.
    const running = oldestFirst.find((t) => t.status !== 'done')
    if (running) {
      follow(running.turn_id)
      return
    }
    // Show the most recent finished turn on the diagram, as it ended.
    const latest = oldestFirst[oldestFirst.length - 1]
    if (latest) {
      try {
        const events = await loadTurnEvents(api.transport, latest.turn_id, get().validate)
        if (events.length) updateTurn(latest.turn_id, (t) => ({ ...t, state: t.state ?? reduceEvents(events, get().edgeIndex) }))
      } catch {
        // History still shows the reply; the diagram just starts idle.
      }
    }
  }

  return {
    api: null,
    ready: false,
    bootError: null,
    capabilities: null,
    diagram: null,
    edgeIndex: new Map(),
    mock: false,

    view: load<View>('view', 'simple'),
    collapsed: load<string[]>('collapsed', []),
    stepMode: false,
    theme: load<Theme>('theme', 'system'),
    provider: null,
    fixture: null,
    disabled: [],

    sessionId: null,
    turns: {},
    order: [],
    liveTurnId: null,
    stopRequested: null,
    connection: 'idle',
    sending: false,
    invalidEvents: 0,

    replay: null,
    selectedNode: null,
    notices: [],

    async boot(t, opts = {}) {
      const api = createApi(t)
      if (opts.seedMessages) for (const [id, text] of Object.entries(opts.seedMessages)) rememberMessage(id, text)
      const sessionId = opts.sessionId ?? load<string | null>('session', null)
      set({ api, validate: opts.validate, mock: opts.mock ?? false, sessionId })
      try {
        const [capabilities, diagram] = await Promise.all([api.capabilities(), api.diagram()])
        set({ capabilities, diagram, edgeIndex: buildEdgeIndex(diagram.edges), ready: true, bootError: null })
        if (sessionId) await loadHistory(sessionId)
      } catch (err) {
        set({ bootError: describe(err), ready: false })
      }
    },

    async send(text) {
      const { api, sessionId, stepMode, provider, fixture, disabled, capabilities, liveTurnId, turns } = get()
      const message = text.trim()
      if (!api || !message) return
      const live = liveTurnId ? turns[liveTurnId]?.state : undefined
      if (live && (live.phase === 'running' || live.phase === 'paused')) {
        get().notify('info', 'A turn is still running. Stop it or wait for it to finish.')
        return
      }
      get().exitReplay()
      set({ sending: true })
      const dev = capabilities?.mode === 'dev'
      try {
        const res = await api.startTurn({
          message,
          session_id: sessionId ?? newSessionId(),
          ...(stepMode ? { step: true } : {}),
          ...(dev && provider ? { provider } : {}),
          ...(dev && fixture ? { fixture } : {}),
          ...(capabilities?.mode !== 'public' && capabilities?.features.disable_nodes !== false && disabled.length ? { disable: disabled } : {}),
        })
        rememberMessage(res.turn_id, message)
        if (res.session_id !== sessionId) {
          set({ sessionId: res.session_id })
          save('session', res.session_id)
        }
        addTurn({ turnId: res.turn_id, userText: message, state: initialTurnState(), createdAt: Date.now() })
        follow(res.turn_id)
      } catch (err) {
        get().notify('error', `Could not start the turn: ${describe(err)}`)
      } finally {
        set({ sending: false })
      }
    },

    async stop() {
      const { api, liveTurnId } = get()
      if (!api || !liveTurnId) return
      set({ stopRequested: liveTurnId })
      try {
        await api.cancel(liveTurnId)
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 409)) {
          set({ stopRequested: null })
          get().notify('error', `Could not stop the turn: ${describe(err)}`)
        }
      }
    },

    async resume(mode) {
      const { api, liveTurnId } = get()
      if (!api || !liveTurnId) return
      try {
        await api.resume(liveTurnId, mode)
      } catch (err) {
        get().notify('error', `Could not resume: ${describe(err)}`)
      }
    },

    async decide(decision) {
      const { api, liveTurnId, turns } = get()
      const approval = liveTurnId ? turns[liveTurnId]?.state?.approval : null
      if (!api || !approval) return
      try {
        await api.decide(approval.approval_id, decision)
      } catch (err) {
        get().notify('error', `Could not record the decision: ${describe(err)}`)
      }
    },

    newSession() {
      stream?.close()
      stream = null
      save('session', null)
      set({ sessionId: null, turns: {}, order: [], liveTurnId: null, replay: null, connection: 'idle' })
    },

    async enterReplay(turnId) {
      const { api, turns, validate, liveTurnId } = get()
      if (!api) return
      const turn = turns[turnId]
      const live = turn?.state
      if (liveTurnId === turnId && live && (live.phase === 'running' || live.phase === 'paused')) return
      let events = live && (live.phase === 'done' || live.phase === 'error') ? live.events : null
      if (!events) {
        try {
          events = await loadTurnEvents(api.transport, turnId, validate)
        } catch (err) {
          get().notify('error', `Could not load the turn: ${describe(err)}`)
          return
        }
        if (events.length === 0) {
          get().notify('error', 'That turn has no stored events to replay.')
          return
        }
        const loaded = reduceEvents(events, get().edgeIndex)
        updateTurn(turnId, (t) => ({ ...t, state: loaded }))
      }
      set({ replay: { turnId, events, cursor: events.length - 1, playing: false } })
    },

    exitReplay() {
      if (get().replay) set({ replay: null })
    },

    setCursor(cursor) {
      const r = get().replay
      if (!r) return
      set({ replay: { ...r, cursor: Math.max(0, Math.min(r.events.length - 1, cursor)) } })
    },

    stepReplay(delta) {
      const r = get().replay
      if (!r) return
      get().setCursor(r.cursor + delta)
    },

    setPlaying(playing) {
      const r = get().replay
      if (!r) return
      set({ replay: { ...r, playing, cursor: playing && r.cursor >= r.events.length - 1 ? 0 : r.cursor } })
    },

    selectNode(id) {
      set({ selectedNode: id })
    },

    setView(view) {
      save('view', view)
      set({ view })
    },

    toggleCollapsed(id) {
      const collapsed = get().collapsed.includes(id) ? get().collapsed.filter((c) => c !== id) : [...get().collapsed, id]
      save('collapsed', collapsed)
      set({ collapsed })
    },

    setStepMode(on) {
      set({ stepMode: on })
    },

    setTheme(theme) {
      save('theme', theme)
      set({ theme })
    },

    setProvider(provider) {
      set({ provider })
    },

    setFixture(fixture) {
      set({ fixture })
    },

    toggleDisabled(id) {
      const d = get().disabled
      set({ disabled: d.includes(id) ? d.filter((x) => x !== id) : [...d, id] })
    },

    notify(level, text) {
      const id = ++noticeId
      set((s) => ({ notices: [...s.notices.slice(-3), { id, level, text }] }))
      setTimeout(() => get().dismissNotice(id), 6000)
    },

    dismissNotice(id) {
      set((s) => ({ notices: s.notices.filter((n) => n.id !== id) }))
    },
  }
})

/** The turn the diagram shows: the replay cursor, else the live or most recent turn. */
export function selectDisplayTurnId(s: AppState): string | null {
  if (s.replay) return s.replay.turnId
  if (s.liveTurnId) return s.liveTurnId
  return s.order[s.order.length - 1] ?? null
}
