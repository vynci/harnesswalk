// Application state. Stream state per turn comes from the pure reducer; this store wires it to
// the API, the stream client, the chat, replay and the view toggles.
import { create } from 'zustand'
import { ApiError, createApi, type Api, type CreateReleaseResult, type ResumeMode, type Transport } from '@/api/client'
import { loadTurnEvents, openEvalStream, openOpsStream, openTurnStream, type TurnStream } from '@/api/stream'
import type {
  ArtifactSaved,
  Capabilities,
  Diagnosis,
  Diagram,
  EvalRunSummary,
  FeedbackRating,
  HarnessEvent,
  OpsEvent,
  PackSummary,
  StartEvalRequest,
  TurnFeedback,
  TurnSummary,
  View,
} from '@/api/types'
import { byoKeyOffered, effectiveModel, fixtureCatalog, fixtureGroups, fixtureVisible, modelReasonText, pickerModels, resetPhrase, scriptedModel, selectedModel } from './features'
import { artifactLabel, releaseMark, savedMark, type OpsMark } from './ops'
import { buildEdgeIndex, initialTurnState, reduceEvent, reduceEvents, type EdgeIndex, type TurnState } from './reducer'
import { packTitle } from './packs'
import { load, rememberMessage, recallMessages, save } from './persist'
import { edgePayloadNode, payloadEvent } from '@/inspector/payload'
import { suiteName } from '@/inspector/opsView'

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
  /** P5: a diagnosis asked for from this browser, before the summary is read back. */
  diagnosis?: Diagnosis | null
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
  /** P6: the details line of an error toast: the API's request id, for a bug report. */
  detail?: string
}

/** The details line for an error the API answered: "Request ID req_…" when it sent one. */
export function noticeDetail(cause: unknown): string | undefined {
  return cause instanceof ApiError && cause.requestId ? `Request ID ${cause.requestId}` : undefined
}

export interface ReplayState {
  turnId: string
  events: HarnessEvent[]
  cursor: number
  playing: boolean
  /** A turn from outside this chat (an eval case), named for the chat header. */
  label?: string
}

/** An eval run seen on the tenant's ops stream, started here or anywhere else (the CLI). */
export interface OpsRun {
  runId: string
  suite: string
  pack: string
  done: number
  total: number | null
  /** P5: `release_gate` when a release's eval gate started the run. */
  purpose?: string | null
}

/** 'system' is the default until the viewer picks one: it follows prefers-color-scheme. */
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
  /** P5: the packs GET /v1/packs lists (null until loaded, or without features.packs). */
  packs: PackSummary[] | null
  /**
   * P5: the session's pack (from the session, its turn summaries or `turn_start`), or the pack
   * the next new session starts on. Every pack-scoped call names it.
   */
  pack: string | null
  /** P5: a new session on another pack is being made. */
  switchingPack: boolean
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
  /** The inspector tab the reader picked, for the node it was picked on. */
  inspectorTab: { node: string | null; tab: string }
  /** The composer's text, so a lesson's "try it" can fill it. */
  draft: string
  /** Bumped to move focus into the composer. */
  composerFocus: number
  notices: Notice[]
  /** The knowledge document open in the reader, at a chunk when a citation opened it. */
  doc: { docId: string; chunk?: number } | null
  /** Bumped after a write the inspector should show (a memory proposal decided). */
  inspectorVersion: number
  /** Dev mode composer option: a JSON Schema for the answer. */
  outputSchema: string
  /**
   * The model the reader picked in the composer, every mode; null is Auto (the router decides,
   * so the turn names no model).
   */
  pickedModel: string | null
  /**
   * When a turn of this tab fell through to the fake model (a `budget_exhausted` notice), until
   * a later capabilities read says the shared budget is fine. The budget banner shows meanwhile.
   */
  budgetNoticeAt: number | null
  /** The budget banner the reader closed, by its reset time (this tab's session only). */
  budgetBannerDismissed: string | null
  /**
   * P6: the reader's own provider key. Held in this tab's memory only (never localStorage or a
   * cookie), sent as the provider key header on every turn, gone on reload.
   */
  byoKey: string | null
  /** P6: the sandbox session's expiry this tab has already acted on (warned, reset). */
  sandbox: { warnedFor: string | null; expiredFor: string | null }
  /** P4: saves and releases in the chat timeline (Checkpoint markers). */
  marks: OpsMark[]
  /** P4: the eval run in progress, started here. */
  liveEval: LiveEval | null
  /** P4: the newest eval run, for the regression badge on the diagram. */
  latestEval: EvalRunSummary | null
  /** P4: the tenant's ops stream (GET /v1/events) is open, so it is the source of the markers. */
  opsLive: boolean
  /** P4: an eval run in progress anywhere for this tenant, from the ops stream. */
  opsRun: OpsRun | null
  /** P4: the node an ops event just touched (eval, release), for a short pulse on the diagram. */
  opsPulse: { nodeId: string; at: number } | null

  boot: (t: Transport, opts?: { mock?: boolean; validate?: AppState['validate']; seedMessages?: Record<string, string>; sessionId?: string }) => Promise<void>
  send: (text: string) => Promise<void>
  stop: () => Promise<void>
  resume: (mode: ResumeMode) => Promise<void>
  decide: (decision: 'approve' | 'deny') => Promise<void>
  newSession: () => void
  enterReplay: (turnId: string, from?: { eventsUrl: string; label: string }) => Promise<void>
  exitReplay: () => void
  setCursor: (cursor: number) => void
  stepReplay: (delta: number) => void
  setPlaying: (playing: boolean) => void
  selectNode: (id: string | null) => void
  setInspectorTab: (node: string | null, tab: string) => void
  /** Open the inspector of a node on its Payload tab. */
  openPayload: (node: string) => void
  /** An edge label's click: the end of the edge whose payload shows what it carries (payload.ts). */
  openEdgePayload: (source: string, target: string) => void
  /** Select a node and open its inspector on a tab (a lesson from the drawer, say). */
  openInspector: (node: string, tab: string) => void
  setDraft: (text: string) => void
  focusComposer: () => void
  setView: (view: View) => void
  toggleCollapsed: (id: string) => void
  setStepMode: (on: boolean) => void
  setTheme: (t: Theme) => void
  setProvider: (p: string | null) => void
  setFixture: (f: string | null) => void
  toggleDisabled: (id: string) => void
  /** `cause`: the error behind an error notice; an API error's request id goes in the details. */
  notify: (level: Notice['level'], text: string, cause?: unknown) => void
  dismissNotice: (id: number) => void
  openDoc: (docId: string, chunk?: number) => void
  closeDoc: () => void
  decideProposal: (proposalId: string, decision: 'accept' | 'reject') => Promise<boolean>
  setOutputSchema: (text: string) => void
  /** Pick a model in the composer; null is Auto. */
  pickModel: (alias: string | null) => void
  /** Close the budget banner for this budget day, in this tab's session. */
  dismissBudgetBanner: () => void
  setByoKey: (key: string | null) => void
  /** P6: the 5-minute warning before the sandbox session ends (once per expiry). */
  warnSandbox: (expiresAt: string) => void
  /** P6: the sandbox session ended: everything in it is gone, so the chat starts over. */
  expireSandbox: (expiresAt: string) => void
  refreshCapabilities: () => Promise<void>
  refreshLatestEval: () => Promise<void>
  startEval: (req: StartEvalRequest) => Promise<string | null>
  saveArtifact: (kind: string, name: string, body: string, note?: string) => Promise<ArtifactSaved | null>
  restoreArtifact: (kind: string, name: string, version: number) => Promise<boolean>
  /** POST /v1/releases on the session's pack; the gate's refusal comes back as `gateFailed`. */
  createRelease: (note?: string, opts?: { force?: boolean }) => Promise<CreateReleaseResult | null>
  rollback: (releaseId: string) => Promise<boolean>
  sendFeedback: (turnId: string, rating: FeedbackRating, note?: string) => Promise<boolean>
  /** P5: start a new session on a pack (POST /v1/sessions). Resolves false when it could not. */
  choosePack: (packId: string) => Promise<boolean>
  /** P5: ask which layer is at fault for a failed eval case or a turn with bad feedback. */
  diagnose: (target: { run_id?: string; case_id: string } | { turn_id: string }) => Promise<Diagnosis | null>
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
let opsStream: { close: () => void } | null = null
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

/** How long to wait, for a 429: "12 min", "40 s". */
export function waitText(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return 'a little while'
  if (seconds >= 3600) return `${Math.round(seconds / 360) / 10} h`
  if (seconds >= 90) return `${Math.ceil(seconds / 60)} min`
  return `${Math.max(1, Math.round(seconds))} s`
}

/** The toast for a turn the API refused: the P6 public-mode refusals in words, the rest as is. */
export function turnRefusal(err: unknown, capabilities?: Capabilities | null): string {
  const ctx = { mode: capabilities?.mode, byo: byoKeyOffered(capabilities) }
  if (err instanceof ApiError && err.status === 429) {
    // P6 review: a 429 also guards how many Claude turns run at once; its body names the limit.
    const limit = (err.body as { error?: { limit?: string } } | undefined)?.error?.limit
    if (limit === 'running_turns') return `Too many Claude turns are running right now. Try again in ${waitText(err.retryAfter)}, or use the fake model.`
    return `Too many requests from this address this hour. Try again in ${waitText(err.retryAfter)}.`
  }
  if (err instanceof ApiError && err.code === 'budget_exhausted') {
    const resets = (err.body as { error?: { resets_at?: string } } | undefined)?.error?.resets_at
    return modelReasonText('budget_exhausted', { ...ctx, resetsAt: resets })
  }
  // Public mode never suggests a key of the reader's own (the key button arrives with accounts).
  if (err instanceof ApiError && err.code === 'provider_not_configured' && capabilities?.mode !== 'dev') return modelReasonText('provider_unconfigured', ctx)
  return `Could not start the turn: ${describe(err)}`
}

function hasBudgetNotice(notices: unknown): boolean {
  return Array.isArray(notices) && notices.some((n) => (n as { reason?: unknown } | null)?.reason === 'budget_exhausted')
}

/** Which budget day a dismissed banner belongs to: its reset time. */
export function budgetBannerKey(capabilities: Capabilities | null | undefined): string {
  return capabilities?.public?.budget?.resets_at ?? 'today'
}

/**
 * The budget banner above the composer (public mode): shown while the shared budget is spent or
 * after a turn fell through, until closed. Its reset time ('' when unknown), or null when hidden.
 */
export function selectBudgetBanner(s: Pick<AppState, 'capabilities' | 'budgetNoticeAt' | 'budgetBannerDismissed'>): string | null {
  const caps = s.capabilities
  if (caps?.mode !== 'public' || !caps.public) return null
  const spent = caps.public.budget?.status === 'exhausted' || s.budgetNoticeAt !== null
  if (!spent || s.budgetBannerDismissed === budgetBannerKey(caps)) return null
  return caps.public.budget?.resets_at ?? ''
}

/** This tab's session only (sessionStorage), guarded like persist.ts: storage may be missing. */
function loadSession(key: string): string | null {
  try {
    return globalThis.sessionStorage?.getItem(`harnesswalk.${key}`) ?? null
  } catch {
    return null
  }
}

function saveSession(key: string, value: string): void {
  try {
    globalThis.sessionStorage?.setItem(`harnesswalk.${key}`, value)
  } catch {
    // A convenience; ignore failures.
  }
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
          // The turn fell through to the fake model: the budget banner shows (P6 picker fixes).
          if (ev.type === 'turn_end' && hasBudgetNotice(ev.summary?.notices)) set({ budgetNoticeAt: Date.now() })
          // The pack the turn runs on, as the harness says (P5): the session's pack from now on.
          if (ev.type === 'turn_start' && typeof ev.summary?.pack === 'string' && ev.summary.pack !== get().pack) setPack(ev.summary.pack)
          // Step mode keeps the inspector on the node the walk stopped at.
          // A node with a payload opens on it, so the reader sees what is about to be sent.
          if (ev.type === 'node_paused' && ev.summary?.reason === 'breakpoint' && get().selectedNode && ev.node_id) {
            const held = payloadEvent(get().turns[turnId]?.state?.events ?? [], ev.node_id)
            set(held ? { selectedNode: ev.node_id, inspectorTab: { node: ev.node_id, tab: 'payload' } } : { selectedNode: ev.node_id })
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
            // After every turn: a release made elsewhere (the CLI, another browser), and in public
            // mode the requests left this hour and the shared budget, show up in the capabilities.
            void get().refreshCapabilities()
          }
        },
      },
    )
  }

  /**
   * The shared budget ran out: a Claude pick moves to the fake model, once, with a toast. Auto
   * stays Auto (it follows the API's default), and nothing moves back when the budget returns.
   */
  const followBudget = () => {
    const { capabilities, byoKey, pickedModel } = get()
    const pub = capabilities?.public
    if (!pickedModel || pub?.budget?.status !== 'exhausted') return
    const models = pickerModels(capabilities, byoKey)
    const picked = models.find((m) => m.alias === pickedModel)
    if (!picked || picked.available || picked.reason !== 'budget_exhausted') return
    const fake = scriptedModel(models)
    if (!fake) return
    set({ pickedModel: fake.alias })
    get().notify('info', `Today's shared Claude budget is used up. You're on the scripted fake model until ${resetPhrase(pub.budget.resets_at)}.`)
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
   * The session's pack: the client names it on every pack-scoped call, the inspectors read again,
   * and this browser remembers it with the session, so a reload comes back on it.
   */
  const setPack = (pack: string | null, sessionId = get().sessionId) => {
    get().api?.setPack(pack)
    if (pack !== get().pack) set((s) => ({ pack, inspectorVersion: s.inspectorVersion + 1 }))
    save('pack', pack ? { sessionId, pack } : null)
  }

  /** An ops event about another pack than the session's (its summary names the pack). */
  const otherPack = (summary: Record<string, unknown>) => {
    const pack = get().pack
    return typeof summary.pack === 'string' && !!pack && summary.pack !== pack
  }

  const addMark = (mark: OpsMark) => {
    const s = get()
    // One marker per change: the ops stream and this browser's own response both report it.
    if (mark.key && s.marks.some((m) => m.key === mark.key)) return
    const marks = [...s.marks, mark]
    set({ marks })
    save('marks', { sessionId: s.sessionId, marks: marks.slice(-50) })
  }

  // The node an ops event names (`node_id`), so the diagram shows where it happened.
  const pulse = (nodeId: string | undefined) => {
    if (!nodeId) return
    const at = Date.now()
    set({ opsPulse: { nodeId, at } })
    setTimeout(() => get().opsPulse?.at === at && set({ opsPulse: null }), 1500)
  }

  /**
   * One ops event from GET /v1/events: whoever made the change (this browser, another one, the
   * CLI), the chat gets its marker, the inspector and the status bar read again, and the
   * diagram's eval and release nodes show it.
   */
  const onOpsEvent = (ev: OpsEvent) => {
    const summary = ev.summary ?? {}
    const bump = () => set((s) => ({ inspectorVersion: s.inspectorVersion + 1 }))
    if (ev.type === 'artifact_changed') {
      const kind = String(summary.kind ?? '')
      const name = String(summary.name ?? '')
      const version = Number(summary.version)
      if (kind && name && Number.isFinite(version) && !otherPack(summary)) addMark(savedMark({ kind, name, version }, afterTurn(), ev.ts))
      pulse(ev.node_id)
      bump()
      void get().refreshCapabilities()
    } else if (ev.type === 'artifact_released') {
      const releaseId = String(summary.release_id ?? '')
      const rollbackOf = typeof summary.rollback_of === 'string' ? summary.rollback_of : null
      const before = typeof summary.previous_active === 'string' ? summary.previous_active : null
      const previous = rollbackOf ?? before
      if (releaseId && !otherPack(summary)) {
        addMark(releaseMark(releaseId, previous, rollbackOf !== null, afterTurn(), ev.ts, ev.event_id))
        setActiveRelease(releaseId)
      }
      pulse(ev.node_id)
      bump()
      void get().refreshCapabilities()
    } else if (ev.type === 'feedback_recorded') {
      // Feedback from another tab or browser on a turn of this chat: the stored rating wins over
      // what this tab pressed last.
      const turnId = ev.turn_id
      if (turnId && get().turns[turnId]) {
        void refreshSummary(turnId).then(() => updateTurn(turnId, (t) => ({ ...t, feedback: t.summary?.feedback ?? t.feedback })))
      }
    } else if (ev.type === 'eval_run_started') {
      const runId = String(summary.run_id ?? ev.run_id ?? '')
      const purpose = typeof summary.purpose === 'string' ? summary.purpose : null
      if (runId) set({ opsRun: { runId, suite: String(summary.suite ?? ''), pack: String(summary.pack ?? ''), done: 0, total: typeof summary.total === 'number' ? summary.total : null, purpose } })
      pulse(ev.node_id)
      bump()
    } else if (ev.type === 'eval_case_finished') {
      const runId = String(summary.run_id ?? '')
      set((s) => (s.opsRun && s.opsRun.runId === runId ? { opsRun: { ...s.opsRun, done: s.opsRun.done + 1 } } : {}))
    } else if (ev.type === 'eval_run_finished') {
      set({ opsRun: null })
      pulse(ev.node_id)
      bump()
      void get().refreshLatestEval()
    }
  }

  const openOps = () => {
    const { api, mock } = get()
    if (!api || opsStream) return
    opsStream = openOpsStream(api.transport, {
      onEvent: onOpsEvent,
      onStatus: (open) => {
        if (mock && !open) return
        set({ opsLive: open })
      },
    })
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
    const ranOn = oldestFirst.find((t) => typeof t.pack === 'string')?.pack
    if (ranOn && ranOn !== get().pack) setPack(ranOn)
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

  /**
   * The packs and the session's pack at start-up: the stored session's own pack when the API
   * knows the session, else the pack this browser picked last, else the configured one.
   */
  const bootPack = async (api: Api, capabilities: Capabilities, sessionId: string | null) => {
    let packs: PackSummary[] | null = null
    try {
      packs = await api.packs()
    } catch {
      // The picker stays empty; turns run on the configured pack.
    }
    const stored = load<{ sessionId: string | null; pack: string } | null>('pack', null)
    let pack = stored && typeof stored.pack === 'string' && (stored.sessionId === sessionId || !stored.sessionId) ? stored.pack : null
    if (sessionId) {
      try {
        pack = (await api.session(sessionId)).pack
      } catch {
        // An unknown session: loadHistory starts fresh, on the stored pack.
      }
    }
    const known = (id: string | null) => !!id && (!packs || packs.some((p) => p.id === id))
    if (!known(pack)) pack = capabilities.packs?.default ?? packs?.find((p) => p.default)?.id ?? null
    set({ packs })
    setPack(pack, sessionId)
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
    packs: null,
    pack: null,
    switchingPack: false,
    turns: {},
    order: [],
    liveTurnId: null,
    stopRequested: null,
    connection: 'idle',
    sending: false,
    invalidEvents: 0,

    replay: null,
    selectedNode: null,
    inspectorTab: { node: null, tab: 'overview' },
    draft: '',
    composerFocus: 0,
    notices: [],
    doc: null,
    inspectorVersion: 0,
    outputSchema: '',
    pickedModel: null,
    budgetNoticeAt: null,
    budgetBannerDismissed: loadSession('budgetBanner'),
    byoKey: null,
    sandbox: { warnedFor: null, expiredFor: null },
    marks: [],
    liveEval: null,
    latestEval: null,
    opsLive: false,
    opsRun: null,
    opsPulse: null,

    async boot(t, opts = {}) {
      const api = createApi(t)
      if (opts.seedMessages) for (const [id, text] of Object.entries(opts.seedMessages)) rememberMessage(id, text)
      const sessionId = opts.sessionId ?? load<string | null>('session', null)
      const stored = load<{ sessionId: string | null; marks: OpsMark[] } | null>('marks', null)
      const marks = stored && stored.sessionId === sessionId && Array.isArray(stored.marks) ? stored.marks : []
      set({ api, validate: opts.validate, mock: opts.mock ?? false, sessionId, marks })
      try {
        const [capabilities, diagram] = await Promise.all([api.capabilities(), api.diagram()])
        if (capabilities.features.packs) await bootPack(api, capabilities, sessionId)
        set({ capabilities, diagram, edgeIndex: buildEdgeIndex(diagram.edges), ready: true, bootError: null })
        followBudget()
        if (get().pack && get().pack !== capabilities.packs?.default) void get().refreshCapabilities()
        if (capabilities.features.evals) void get().refreshLatestEval()
        if (capabilities.features.release || capabilities.features.evals || capabilities.features.feedback) openOps()
        if (sessionId) await loadHistory(sessionId)
      } catch (err) {
        set({ bootError: describe(err), ready: false })
      }
    },

    async send(text) {
      const { api, sessionId, stepMode, provider, fixture, disabled, capabilities, liveTurnId, turns, outputSchema, pack, byoKey, pickedModel } = get()
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
      // The turn names the picked model; Auto names none, so the router decides (every mode).
      const models = pickerModels(capabilities, byoKey)
      const pick = selectedModel(models, pickedModel)
      // A fixture goes with a turn on the scripted model only (dev, and public since the picker fixes).
      const withFixture = !!fixture && fixtureVisible(capabilities, effectiveModel(models, pickedModel), provider) && fixtureCatalog(capabilities).names.includes(fixture)
      const apiKey = byoKeyOffered(capabilities) && byoKey ? byoKey : null
      get().exitReplay()
      set({ sending: true })
      const start = (session: string | null) =>
        api.startTurn({
          message,
          session_id: session ?? newSessionId(),
          ...(stepMode ? { step: true } : {}),
          ...(dev && provider ? { provider } : {}),
          ...(withFixture ? { fixture } : {}),
          ...(capabilities?.mode !== 'public' && capabilities?.features.disable_nodes !== false && disabled.length ? { disable: disabled } : {}),
          ...(parsed.schema ? { output_schema: parsed.schema } : {}),
          ...(pick ? { model: pick.alias } : {}),
          // A new session starts on the chosen pack (P5); a session's pack is fixed after that.
          ...(!session && pack && capabilities?.features.packs ? { options: { pack } } : {}),
        }, { apiKey })
      try {
        let res
        try {
          res = await start(sessionId)
        } catch (err) {
          // P6: the stored session is gone for this browser's tenant (the sandbox session ended and
          // the cookie names a fresh tenant, or the sweeper deleted it): start a fresh session and
          // send the message there, once, instead of refusing it.
          if (!(sessionId && err instanceof ApiError && err.status === 404 && err.code === 'session_not_found')) throw err
          get().newSession()
          get().notify('info', 'That session has ended and its data was deleted, so this message starts a fresh one.')
          void get().refreshCapabilities()
          res = await start(null)
        }
        rememberMessage(res.turn_id, message)
        if (res.session_id !== get().sessionId) {
          set({ sessionId: res.session_id })
          save('session', res.session_id)
          if (pack) save('pack', { sessionId: res.session_id, pack })
          // Markers made before the first message belong to this session now.
          if (get().marks.length) save('marks', { sessionId: res.session_id, marks: get().marks })
        }
        addTurn({ turnId: res.turn_id, userText: message, state: initialTurnState(), createdAt: Date.now() })
        follow(res.turn_id)
      } catch (err) {
        get().notify('error', turnRefusal(err, get().capabilities), err)
        // A refusal about models or limits changes what the picker and the limits card show.
        if (err instanceof ApiError && (err.status === 409 || err.status === 429 || err.code === 'invalid_request')) void get().refreshCapabilities()
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
          get().notify('error', `Could not stop the turn: ${describe(err)}`, err)
        }
      }
    },

    async resume(mode) {
      const { api, liveTurnId } = get()
      if (!api || !liveTurnId) return
      try {
        await api.resume(liveTurnId, mode)
      } catch (err) {
        get().notify('error', `Could not resume: ${describe(err)}`, err)
      }
    },

    async decide(decision) {
      const { api, liveTurnId, turns } = get()
      const approval = liveTurnId ? turns[liveTurnId]?.state?.approval : null
      if (!api || !approval) return
      try {
        await api.decide(approval.approval_id, decision)
      } catch (err) {
        get().notify('error', `Could not record the decision: ${describe(err)}`, err)
      }
    },

    newSession() {
      stream?.close()
      stream = null
      save('session', null)
      save('marks', null)
      // The chosen pack stays: the next first message starts the new session on it.
      const pack = get().pack
      save('pack', pack ? { sessionId: null, pack } : null)
      set({ sessionId: null, turns: {}, order: [], liveTurnId: null, replay: null, connection: 'idle', marks: [] })
    },

    async enterReplay(turnId, from) {
      const { api, turns, validate, liveTurnId } = get()
      if (!api) return
      const turn = turns[turnId]
      const live = turn?.state
      if (liveTurnId === turnId && live && (live.phase === 'running' || live.phase === 'paused')) return
      let events = live && (live.phase === 'done' || live.phase === 'error') ? live.events : null
      if (!events) {
        try {
          // An eval case turn lives in its run's scratch tenant: its stream is the case's events_url.
          events = await loadTurnEvents(api.transport, turnId, validate, from?.eventsUrl)
        } catch (err) {
          get().notify('error', `Could not load the turn: ${describe(err)}`, err)
          return
        }
        if (events.length === 0) {
          get().notify('error', 'That turn has no stored events to replay.')
          return
        }
        const loaded = reduceEvents(events, get().edgeIndex)
        updateTurn(turnId, (t) => ({ ...t, state: loaded }))
      }
      set({ replay: { turnId, events, cursor: events.length - 1, playing: false, ...(from ? { label: from.label } : {}) } })
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

    setInspectorTab(node, tab) {
      set({ inspectorTab: { node, tab } })
    },

    openInspector(node, tab) {
      set({ selectedNode: node, inspectorTab: { node, tab } })
    },

    openPayload(node) {
      set({ selectedNode: node, inspectorTab: { node, tab: 'payload' } })
    },

    openEdgePayload(source, target) {
      const s = get()
      const id = selectDisplayTurnId(s)
      const events = s.replay ? s.replay.events.slice(0, s.replay.cursor + 1) : id ? (s.turns[id]?.state?.events ?? []) : []
      const node = edgePayloadNode(events, source, target)
      if (node) get().openPayload(node)
      else set({ selectedNode: target })
    },

    setDraft(draft) {
      set({ draft })
    },

    focusComposer() {
      set((s) => ({ composerFocus: s.composerFocus + 1 }))
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

    notify(level, text, cause) {
      const id = ++noticeId
      const detail = noticeDetail(cause)
      set((s) => ({ notices: [...s.notices.slice(-3), { id, level, text, ...(detail ? { detail } : {}) }] }))
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
        get().notify('error', `Could not decide the proposal: ${describe(err)}`, err)
        return false
      } finally {
        set((s) => ({ inspectorVersion: s.inspectorVersion + 1 }))
      }
    },

    setOutputSchema(outputSchema) {
      set({ outputSchema })
    },

    pickModel(pickedModel) {
      set({ pickedModel })
    },

    dismissBudgetBanner() {
      const key = budgetBannerKey(get().capabilities)
      saveSession('budgetBanner', key)
      set({ budgetBannerDismissed: key })
    },

    setByoKey(key) {
      const byoKey = key && key.trim() ? key.trim() : null
      set({ byoKey })
    },

    warnSandbox(expiresAt) {
      if (get().sandbox.warnedFor === expiresAt) return
      set((s) => ({ sandbox: { ...s.sandbox, warnedFor: expiresAt } }))
      get().notify('info', 'Sandbox session: 5 minutes left. Then everything here is deleted and the chat starts over.')
    },

    expireSandbox(expiresAt) {
      if (get().sandbox.expiredFor === expiresAt) return
      set((s) => ({ sandbox: { ...s.sandbox, expiredFor: expiresAt } }))
      // The API deleted the tenant's sessions, turns and memory; the next turn starts a fresh one.
      get().newSession()
      get().notify('info', 'The sandbox session ended and everything in it was deleted. Your next message starts a fresh one.')
      void get().refreshCapabilities()
    },

    async refreshCapabilities() {
      const api = get().api
      if (!api) return
      const before = get().capabilities?.release?.active_release_id ?? null
      const asked = Date.now()
      try {
        let capabilities = await api.capabilities()
        // Releases are per pack: the capabilities describe the configured one, so a session on
        // another pack reads its own release state from GET /v1/artifacts?pack=.
        const pack = get().pack
        if (capabilities.features.packs && pack && pack !== capabilities.packs?.default && capabilities.features.release) {
          try {
            capabilities = { ...capabilities, release: (await api.artifacts()).release }
          } catch {
            // Keep the configured pack's state.
          }
        }
        set({ capabilities })
        // The budget came back (or never ran out: other turns held it): the banner goes, the pick stays.
        const notice = get().budgetNoticeAt
        if (notice !== null && notice < asked && capabilities.public?.budget?.status !== 'exhausted') set({ budgetNoticeAt: null })
        followBudget()
        const now = capabilities.release?.active_release_id ?? null
        // Without the ops stream (an older API), a release made elsewhere shows up here first.
        if (!get().opsLive && now && before && now !== before) addMark(releaseMark(now, before, false, afterTurn()))
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
        get().notify('error', text, err)
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
            `${suiteName(run.suite)} run finished: ${t.passed} passed, ${t.failed} failed, ${t.skipped} skipped${run.regressions ? `, ${run.regressions} regression${run.regressions === 1 ? '' : 's'}` : ''}.`,
          )
        } catch (err) {
          get().notify('error', `Could not read the eval run: ${describe(err)}`, err)
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
        // With the ops stream open, its artifact_changed event places the marker.
        if (!get().opsLive && !saved.unchanged) addMark(savedMark(saved, afterTurn()))
        set((s) => ({ inspectorVersion: s.inspectorVersion + 1 }))
        void get().refreshCapabilities()
        return saved
      } catch (err) {
        const text =
          err instanceof ApiError && err.code === 'turn_running'
            ? 'A turn is running, so the edit was not saved. Try again when it ends.'
            : `Could not save ${artifactLabel(kind, name)}: ${describe(err)}`
        get().notify('error', text, err)
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
        get().notify('error', `Could not read ${artifactLabel(kind, name)} v${version}: ${describe(err)}`, err)
        return false
      }
    },

    async createRelease(note, opts = {}) {
      const api = get().api
      if (!api) return null
      const previous = get().capabilities?.release?.active_release_id ?? null
      try {
        const out = await api.createRelease(note, opts)
        set((s) => ({ inspectorVersion: s.inspectorVersion + 1 }))
        if (out.gateFailed) {
          const n = out.gateFailed.cases.length || out.gateFailed.gate.failed_cases?.length || 0
          get().notify('error', `The eval gate refused the release: ${n} golden case${n === 1 ? '' : 's'} failed. Nothing was released.`)
          return out
        }
        const release = out.release
        get().notify(
          'info',
          release.gate?.forced
            ? `Release ${release.release_id} created without the gate. The audit log records it as release_forced.`
            : `Release ${release.release_id} created and active.`,
        )
        if (!get().opsLive) addMark(releaseMark(release.release_id, previous, false, afterTurn()))
        setActiveRelease(release.release_id)
        await get().refreshCapabilities()
        return out
      } catch (err) {
        const text =
          err instanceof ApiError && err.code === 'turn_running'
            ? 'A turn is running, so nothing was released. Try again when it ends.'
            : `Could not create the release: ${describe(err)}`
        get().notify('error', text, err)
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
        if (!get().opsLive) addMark(releaseMark(releaseId, previous, true, afterTurn()))
        setActiveRelease(releaseId)
        set((s) => ({ inspectorVersion: s.inspectorVersion + 1 }))
        await get().refreshCapabilities()
        return true
      } catch (err) {
        get().notify('error', `Could not roll back to ${releaseId}: ${describe(err)}`, err)
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
        get().notify('error', `Could not record the feedback: ${describe(err)}`, err)
        return false
      }
    },

    async choosePack(packId) {
      const api = get().api
      if (!api) return false
      if (selectTurnRunning(get())) {
        get().notify('info', 'A turn is still running. Stop it or wait for it to finish, then pick a pack.')
        return false
      }
      set({ switchingPack: true })
      try {
        const info = await api.createSession(packId)
        stream?.close()
        stream = null
        save('session', info.session_id)
        save('marks', null)
        set({ sessionId: info.session_id, turns: {}, order: [], liveTurnId: null, replay: null, connection: 'idle', marks: [], stopRequested: null })
        setPack(info.pack, info.session_id)
        // A fixture of the old pack would not be found on this one.
        const fixture = get().fixture
        if (fixture && !fixtureGroups(fixtureCatalog(get().capabilities), get().packs, info.pack).all.includes(fixture)) set({ fixture: null })
        set((s) => ({ packs: s.packs?.map((p) => (p.id === info.pack ? { ...p, loaded: true } : p)) ?? null }))
        void get().refreshCapabilities()
        void get().refreshLatestEval()
        get().notify('info', `New session on ${packTitle(get().packs, info.pack)}.`)
        return true
      } catch (err) {
        get().notify('error', `Could not start a session on ${packTitle(get().packs, packId)}: ${describe(err)}`, err)
        return false
      } finally {
        set({ switchingPack: false })
      }
    },

    async diagnose(target) {
      const api = get().api
      if (!api) return null
      try {
        const diagnosis = await api.diagnose(target)
        if ('turn_id' in target && !('case_id' in target)) updateTurn(target.turn_id, (t) => ({ ...t, diagnosis }))
        set((s) => ({ inspectorVersion: s.inspectorVersion + 1 }))
        return diagnosis
      } catch (err) {
        const text =
          err instanceof ApiError && err.code === 'nothing_to_diagnose'
            ? `Nothing to diagnose: ${err.message}`
            : `Could not diagnose: ${describe(err)}`
        get().notify(err instanceof ApiError && err.code === 'nothing_to_diagnose' ? 'info' : 'error', text, err)
        return null
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
