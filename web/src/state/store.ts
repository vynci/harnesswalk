// Application state. Stream state per turn comes from the pure reducer; this store wires it to
// the API, the stream client, the chat, replay and the view toggles.
import { create } from 'zustand'
import { ApiError, createApi, type Api, type ResumeMode, type Transport } from '@/api/client'
import { loadTurnEvents, openEvalStream, openTurnStream, type TurnStream } from '@/api/stream'
import type {
  ArtifactSaved,
  Capabilities,
  Diagram,
  EvalRunSummary,
  FeedbackRating,
  HarnessEvent,
  ReleaseSummary,
  StartEvalRequest,
  TurnFeedback,
  TurnSummary,
  View,
} from '@/api/types'
import { artifactLabel, releaseMark, savedMark, type OpsMark } from './ops'
import { buildEdgeIndex, initialTurnState, reduceEvent, reduceEvents, type EdgeIndex, type TurnState } from './reducer'
import { load, rememberMessage, recallMessages, save } from './persist'

export interface ChatTurn {
  turnId: string
  userText?: string
  summary?: TurnSummary
  /** Reduced events, when this browser has streamed or loaded them. */
  state?: TurnState
  createdAt: number
  /** The event stream dropped and came back while the turn was still going (a restart, say). */
  reconnected?: boolean
  /** P4: the Good or Bad sent from this browser, before the summary is read back. */
  feedback?: TurnFeedback | null
}

/** An eval run this browser started, while its progress stream is open. */
export interface LiveEval {
  runId: string
  suite: string
  pack: string
  /** Cases finished so far, counted from `eval_case_finished`. */
  done: number
  total: number | null
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
  /** Simple view: draw the LLM Ops and telemetry edges instead of one telemetry bus. */
  showTelemetry: boolean
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
  /** The knowledge document open in the reader, at a chunk when a citation opened it. */
  doc: { docId: string; chunk?: number } | null
  /** Bumped after a write the inspector should show (a memory proposal decided). */
  inspectorVersion: number
  /** Dev mode composer options: a JSON Schema for the answer, and a model alias hint. */
  outputSchema: string
  modelHint: string | null
  /** P4: saves and releases in the chat timeline (Checkpoint markers). */
  marks: OpsMark[]
  /** P4: the eval run in progress, started here. */
  liveEval: LiveEval | null
  /** P4: the newest eval run, for the regression badge on the diagram. */
  latestEval: EvalRunSummary | null

  boot: (t: Transport, opts?: { mock?: boolean; validate?: AppState['validate']; seedMessages?: Record<string, string>; sessionId?: string }) => Promise<void>
  send: (text: string) => Promise<void>
  stop: () => Promise<void>
  resume: (mode: ResumeMode) => Promise<void>
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
  setShowTelemetry: (on: boolean) => void
  setStepMode: (on: boolean) => void
  setTheme: (t: Theme) => void
  setProvider: (p: string | null) => void
  setFixture: (f: string | null) => void
  toggleDisabled: (id: string) => void
  notify: (level: Notice['level'], text: string) => void
  dismissNotice: (id: number) => void
  openDoc: (docId: string, chunk?: number) => void
  closeDoc: () => void
  decideProposal: (proposalId: string, decision: 'accept' | 'reject') => Promise<boolean>
  setOutputSchema: (text: string) => void
  setModelHint: (alias: string | null) => void
  refreshCapabilities: () => Promise<void>
  refreshLatestEval: () => Promise<void>
  startEval: (req: StartEvalRequest) => Promise<string | null>
  saveArtifact: (kind: string, name: string, body: string, note?: string) => Promise<ArtifactSaved | null>
  restoreArtifact: (kind: string, name: string, version: number) => Promise<boolean>
  createRelease: (note?: string) => Promise<ReleaseSummary | null>
  rollback: (releaseId: string) => Promise<boolean>
  sendFeedback: (turnId: string, rating: FeedbackRating, note?: string) => Promise<boolean>
}

/** A turn is running or paused: edits wait for it (the API answers 409 turn_running). */
export function selectTurnRunning(s: Pick<AppState, 'liveTurnId' | 'turns'>): boolean {
  const live = s.liveTurnId ? s.turns[s.liveTurnId]?.state : undefined
  return !!live && (live.phase === 'running' || live.phase === 'paused')
}

/** The composer's schema text as a JSON Schema object, or an error message. Empty is none. */
export function parseOutputSchema(text: string): { schema?: Record<string, unknown>; error?: string } {
  if (!text.trim()) return {}
  try {
    const value: unknown = JSON.parse(text)
    if (typeof value !== 'object' || value === null || Array.isArray(value)) return { error: 'The schema must be a JSON object.' }
    return { schema: value as Record<string, unknown> }
  } catch (err) {
    return { error: `Not valid JSON: ${err instanceof Error ? err.message : String(err)}` }
  }
}

let stream: TurnStream | null = null
let evalStream: { close: () => void } | null = null
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
    let dropped = false
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
        onStatus: (status) => {
          // A drop and a successful reconnect, while the turn is still going: the chat says so,
          // because a restart re-attaches a turn waiting on an approval (approval_queue.md).
          if (status === 'reconnecting') dropped = true
          if (status === 'open' && dropped) {
            dropped = false
            updateTurn(turnId, (t) => ({ ...t, reconnected: true }))
          }
          set({ connection: status })
        },
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
          if (why === 'turn_end') {
            void refreshSummary(turnId)
            // A release made elsewhere (the CLI, another browser) shows up in the capabilities.
            if (get().capabilities?.features.release) void get().refreshCapabilities()
          }
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

  /**
   * The session listing may not carry P4's `feedback` and `scores` (the contract adds them to
   * GET /v1/turns/{id}); read those turns one by one, newest first, so the Good and Bad buttons
   * show what was pressed before the reload.
   */
  const hydrateFeedback = async (list: TurnSummary[]) => {
    const { api, capabilities } = get()
    if (!api || !capabilities?.features.feedback) return
    const missing = list.filter((t) => t.status === 'done' && !('feedback' in t)).slice(-20).reverse()
    for (const t of missing) await refreshSummary(t.turn_id)
  }

  const addMark = (mark: OpsMark) => {
    const s = get()
    const marks = [...s.marks, mark]
    set({ marks })
    save('marks', { sessionId: s.sessionId, marks: marks.slice(-50) })
  }

  /** Record the release this browser just made active, so a refresh does not mark it twice. */
  const setActiveRelease = (releaseId: string) => {
    const c = get().capabilities
    if (c) set({ capabilities: { ...c, release: { working_set_dirty: false, ...c.release, active_release_id: releaseId } } })
  }

  const afterTurn = () => {
    const o = get().order
    return o[o.length - 1] ?? null
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
    void hydrateFeedback(oldestFirst)
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
    showTelemetry: load<boolean>('telemetry', false) === true,
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
    doc: null,
    inspectorVersion: 0,
    outputSchema: '',
    modelHint: null,
    marks: [],
    liveEval: null,
    latestEval: null,

    async boot(t, opts = {}) {
      const api = createApi(t)
      if (opts.seedMessages) for (const [id, text] of Object.entries(opts.seedMessages)) rememberMessage(id, text)
      const sessionId = opts.sessionId ?? load<string | null>('session', null)
      const stored = load<{ sessionId: string | null; marks: OpsMark[] } | null>('marks', null)
      const marks = stored && stored.sessionId === sessionId && Array.isArray(stored.marks) ? stored.marks : []
      set({ api, validate: opts.validate, mock: opts.mock ?? false, sessionId, marks })
      try {
        const [capabilities, diagram] = await Promise.all([api.capabilities(), api.diagram()])
        set({ capabilities, diagram, edgeIndex: buildEdgeIndex(diagram.edges), ready: true, bootError: null })
        if (capabilities.features.evals) void get().refreshLatestEval()
        if (sessionId) await loadHistory(sessionId)
      } catch (err) {
        set({ bootError: describe(err), ready: false })
      }
    },

    async send(text) {
      const { api, sessionId, stepMode, provider, fixture, disabled, capabilities, liveTurnId, turns, outputSchema, modelHint } = get()
      const message = text.trim()
      if (!api || !message) return
      const dev = capabilities?.mode === 'dev'
      const parsed = dev ? parseOutputSchema(outputSchema) : {}
      if (parsed.error) {
        get().notify('error', `Output schema: ${parsed.error}`)
        return
      }
      const live = liveTurnId ? turns[liveTurnId]?.state : undefined
      if (live && (live.phase === 'running' || live.phase === 'paused')) {
        get().notify('info', 'A turn is still running. Stop it or wait for it to finish.')
        return
      }
      get().exitReplay()
      set({ sending: true })
      try {
        const res = await api.startTurn({
          message,
          session_id: sessionId ?? newSessionId(),
          ...(stepMode ? { step: true } : {}),
          ...(dev && provider ? { provider } : {}),
          ...(dev && fixture ? { fixture } : {}),
          ...(capabilities?.mode !== 'public' && capabilities?.features.disable_nodes !== false && disabled.length ? { disable: disabled } : {}),
          ...(parsed.schema ? { output_schema: parsed.schema } : {}),
          ...(dev && modelHint ? { model: modelHint } : {}),
        })
        rememberMessage(res.turn_id, message)
        if (res.session_id !== sessionId) {
          set({ sessionId: res.session_id })
          save('session', res.session_id)
          // Markers made before the first message belong to this session now.
          if (get().marks.length) save('marks', { sessionId: res.session_id, marks: get().marks })
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
      save('marks', null)
      set({ sessionId: null, turns: {}, order: [], liveTurnId: null, replay: null, connection: 'idle', marks: [] })
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

    setShowTelemetry(on) {
      save('telemetry', on)
      set({ showTelemetry: on })
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

    openDoc(docId, chunk) {
      set({ doc: { docId, chunk } })
    },

    closeDoc() {
      set({ doc: null })
    },

    async decideProposal(proposalId, decision) {
      const api = get().api
      if (!api) return false
      try {
        await api.decideProposal(proposalId, decision)
        get().notify('info', decision === 'accept' ? 'Proposal accepted: the memory writer wrote it.' : 'Proposal rejected: nothing was written.')
        return true
      } catch (err) {
        get().notify('error', `Could not decide the proposal: ${describe(err)}`)
        return false
      } finally {
        set((s) => ({ inspectorVersion: s.inspectorVersion + 1 }))
      }
    },

    setOutputSchema(outputSchema) {
      set({ outputSchema })
    },

    setModelHint(modelHint) {
      set({ modelHint })
    },

    async refreshCapabilities() {
      const api = get().api
      if (!api) return
      const before = get().capabilities?.release?.active_release_id ?? null
      try {
        const capabilities = await api.capabilities()
        set({ capabilities })
        const now = capabilities.release?.active_release_id ?? null
        // A release this browser did not make: its own actions set the active id first.
        if (now && before && now !== before) addMark(releaseMark(now, before, false, afterTurn()))
      } catch {
        // Keep the capabilities we have.
      }
    },

    async refreshLatestEval() {
      const api = get().api
      if (!api) return
      try {
        const [latest] = await api.evalRuns({ limit: 1 })
        set({ latestEval: latest ?? null })
      } catch {
        // The badge just stays as it was.
      }
    },

    async startEval(req) {
      const api = get().api
      if (!api) return null
      let runId: string
      try {
        runId = (await api.startEval(req)).run_id
      } catch (err) {
        const text =
          err instanceof ApiError && err.code === 'judge_unavailable'
            ? 'The judge suite needs a configured model other than fake. Pick one, or run the golden suite.'
            : `Could not start the eval run: ${describe(err)}`
        get().notify('error', text)
        return null
      }
      evalStream?.close()
      set({ liveEval: { runId, suite: req.suite, pack: req.pack, done: 0, total: req.cases?.length ?? null }, inspectorVersion: get().inspectorVersion + 1 })
      const finish = async () => {
        evalStream = null
        try {
          const run = await api.evalRun(runId)
          set({ latestEval: run })
          const t = run.totals
          // A failing case is a result, not an error (evaluator.md): an info notice either way.
          get().notify(
            'info',
            `${run.suite} run finished: ${t.passed} passed, ${t.failed} failed, ${t.skipped} skipped${run.regressions ? `, ${run.regressions} regression${run.regressions === 1 ? '' : 's'}` : ''}.`,
          )
        } catch (err) {
          get().notify('error', `Could not read the eval run: ${describe(err)}`)
        }
        set((s) => ({ liveEval: s.liveEval?.runId === runId ? null : s.liveEval, inspectorVersion: s.inspectorVersion + 1 }))
      }
      evalStream = openEvalStream(api.transport, runId, {
        onEvent: (ev) => {
          const summary = ev.summary ?? {}
          set((s) => {
            if (s.liveEval?.runId !== runId) return {}
            const total = typeof summary.total === 'number' ? summary.total : typeof summary.cases === 'number' ? summary.cases : s.liveEval.total
            return { liveEval: { ...s.liveEval, total, done: s.liveEval.done + (ev.type === 'eval_case_finished' ? 1 : 0) } }
          })
        },
        onEnd: (why) => {
          if (why !== 'closed') void finish()
        },
      })
      return runId
    },

    async saveArtifact(kind, name, body, note) {
      const api = get().api
      if (!api) return null
      try {
        const saved = await api.saveArtifact(kind, name, body, note)
        get().notify('info', `${artifactLabel(saved.kind, saved.name)} v${saved.version} saved to the working set.`)
        addMark(savedMark(saved, afterTurn()))
        set((s) => ({ inspectorVersion: s.inspectorVersion + 1 }))
        void get().refreshCapabilities()
        return saved
      } catch (err) {
        const text =
          err instanceof ApiError && err.code === 'turn_running'
            ? 'A turn is running, so the edit was not saved. Try again when it ends.'
            : `Could not save ${artifactLabel(kind, name)}: ${describe(err)}`
        get().notify('error', text)
        return null
      }
    },

    async restoreArtifact(kind, name, version) {
      const api = get().api
      if (!api) return false
      try {
        const old = await api.artifact(kind, name, version)
        const saved = await get().saveArtifact(kind, name, old.body, `restore v${version}`)
        return saved !== null
      } catch (err) {
        get().notify('error', `Could not read ${artifactLabel(kind, name)} v${version}: ${describe(err)}`)
        return false
      }
    },

    async createRelease(note) {
      const api = get().api
      if (!api) return null
      const previous = get().capabilities?.release?.active_release_id ?? null
      try {
        const release = await api.createRelease(note)
        get().notify('info', `Release ${release.release_id} created and active.`)
        addMark(releaseMark(release.release_id, previous, false, afterTurn()))
        setActiveRelease(release.release_id)
        set((s) => ({ inspectorVersion: s.inspectorVersion + 1 }))
        await get().refreshCapabilities()
        return release
      } catch (err) {
        get().notify('error', `Could not create the release: ${describe(err)}`)
        return null
      }
    },

    async rollback(releaseId) {
      const api = get().api
      if (!api) return false
      const previous = get().capabilities?.release?.active_release_id ?? null
      try {
        await api.rollback(releaseId)
        get().notify('info', `Rolled back: release ${releaseId} is active and the working set matches it.`)
        addMark(releaseMark(releaseId, previous, true, afterTurn()))
        setActiveRelease(releaseId)
        set((s) => ({ inspectorVersion: s.inspectorVersion + 1 }))
        await get().refreshCapabilities()
        return true
      } catch (err) {
        get().notify('error', `Could not roll back to ${releaseId}: ${describe(err)}`)
        return false
      }
    },

    async sendFeedback(turnId, rating, note) {
      const api = get().api
      if (!api) return false
      try {
        const stored = await api.feedback(turnId, rating, note)
        const feedback: TurnFeedback = { rating: stored?.rating ?? rating, note: stored?.note ?? note ?? null, at: stored?.at }
        updateTurn(turnId, (t) => ({ ...t, feedback, summary: t.summary ? { ...t.summary, feedback } : t.summary }))
        return true
      } catch (err) {
        get().notify('error', `Could not record the feedback: ${describe(err)}`)
        return false
      }
    },
  }
})

/** The turn the diagram shows: the replay cursor, else the live or most recent turn. */
export function selectDisplayTurnId(s: AppState): string | null {
  if (s.replay) return s.replay.turnId
  if (s.liveTurnId) return s.liveTurnId
  return s.order[s.order.length - 1] ?? null
}
