// A tiny in-browser stand-in for the harnesswalk API, used when VITE_HARNESSWALK_MOCK=1 and by
// the tests. It answers the HTTP contract (spec/contracts/http_api.md and the P2 additions) with
// a fetch function and an EventSource class, and plays the recorded spec/examples streams with
// fresh ids and timestamps. It supports approvals (approve and deny), step mode, cancel,
// cursor replay with after and follow, and dropped connections for reconnect tests.
import type { EventSourceCtor, EventSourceLike, Transport } from '@/api/client'
import type {
  Capabilities,
  Cost,
  Diagram,
  HarnessEvent,
  NodeInfo,
  NodeSource,
  StartTurnRequest,
  TurnSummary,
} from '@/api/types'
import { EXAMPLE_MESSAGES, EXAMPLE_NAMES, EXAMPLES, servedDiagram, type ExampleName } from './examples'
import { MockLessons } from './lessons'
import { MARKDOWN_PROMPT, MARKDOWN_REPLY } from './markdown'
import { MockEvalEventSource, MockOps, MockOpsEventSource } from './ops'
import { allFixtures, DEFAULT_PACK, fixtureInfo, PACK_IDS, packDetail, packFiles, packSummary } from './packs'
import { payloadFor } from './payloads'
import { repoFile, sourceFor } from './repo'

export interface MockOptions {
  /** Milliseconds between streamed events. 0 plays as fast as timers allow. */
  stepMs?: number
  /** Seed session `sess_demo` with the three recorded turns. */
  seed?: boolean
  /** Split the reply into message_delta events inside the reply gateway span. */
  messageDelta?: boolean
  /** P6: serve public mode (the site): `capabilities.public`, the model allowlist, limits. */
  public?: MockPublicOptions | null
}

/** The public-mode fixture's knobs (bootstrap reads them from `?mode=public&budget=spent...`). */
export interface MockPublicOptions {
  /**
   * `spent`: both Claude models are out of today's shared budget. Default: only Sonnet is.
   * `midturn`: as the default, but the first Claude turn runs out between two calls, so its reply
   * falls through to the fake model and `turn_end.summary.notices` says so; from then on the
   * budget reads as spent, as on the real server.
   */
  budget?: 'ok' | 'spent' | 'midturn'
  /** Sonnet's provider is marked down (`provider_unhealthy`) instead of out of budget. */
  unhealthy?: boolean
  /** Requests left this hour before a 429 (default 27 of 30). */
  remaining?: number
  /** Sandbox session lifetime in seconds (default 7200). */
  ttlS?: number
}

/** The public site's models (phase-6 CONTRACT section 2), the default model first. */
const PUBLIC_MODELS = [
  { alias: 'hosted-haiku', label: 'Claude Haiku 5.5', provider: 'anthropic' },
  { alias: 'hosted-sonnet', label: 'Claude Sonnet 5.5', provider: 'anthropic' },
  { alias: 'fake-small', label: 'Fake model (scripted)', provider: 'fake' },
] as const
const DEV_ONLY = 'provider and fixture can only be chosen in dev mode'
const PER_HOUR = 30

type Template = Omit<HarnessEvent, 'seq' | 'ts' | 'event_id' | 'trace_id' | 'turn_id' | 'session_id' | 'tenant_id'>

interface MockTurn {
  id: string
  message: string | null
  sessionId: string
  traceId: string
  tenantId: string
  createdAt: number
  events: HarnessEvent[]
  queue: Template[]
  denyQueue: Template[] | null
  status: 'running' | 'paused' | 'done'
  exit: TurnSummary['exit']
  reply: string | null
  stepping: boolean
  waiting: null | { kind: 'breakpoint'; nodeId: string } | { kind: 'approval'; approvalId: string }
  timer: ReturnType<typeof setTimeout> | null
  sources: Set<MockEventSource>
  disabled: Set<string>
  deltasSent: boolean
  /** Step-over: the next breakpoints on expert children are skipped. */
  stepOver?: boolean
  /** P5: the pack of the turn's session. */
  pack: string
  /** Payload detail records by event id (GET /v1/trace/turns/{id}/details/{event_id}). */
  details?: Map<string, unknown>
}

const TENANT = 'dev'
const DENY_REPLY = 'I did not cancel order A-1002: the request was denied, so nothing was changed.'

function hex(n: number): string {
  let out = ''
  while (out.length < n) out += Math.floor(Math.random() * 16).toString(16)
  return out === '0'.repeat(n) ? hex(n) : out
}

/** A request header from a fetch init, case-insensitively. */
function header(init: RequestInit | undefined, name: string): string | null {
  const h = init?.headers
  if (!h) return null
  if (h instanceof Headers) return h.get(name)
  const entries = Array.isArray(h) ? h : Object.entries(h)
  const found = entries.find(([k]) => k.toLowerCase() === name)
  return found ? String(found[1]) : null
}

function json(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

let requestCounter = 0

/** An error as the API sends it, with the `X-Request-Id` every P6 response carries. */
function apiError(status: number, code: string, message: string): Response {
  return errorResponse(status, { error: { code, message } })
}

function errorResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'X-Request-Id': `req_mock${String(++requestCounter).padStart(4, '0')}`, ...headers },
  })
}

/** The words the API's loop.notices config gives a turn that fell through to the fake model. */
const BUDGET_NOTICE = "Today's shared budget for Claude models is spent, so the rest of this turn ran on the fake model. It resets at 00:00 UTC."
const HOSTED = new Set(['hosted-haiku', 'hosted-sonnet'])

function toTemplate(e: HarnessEvent): Template {
  const { seq: _s, ts: _t, event_id: _e, trace_id: _tr, turn_id: _tu, session_id: _se, tenant_id: _te, ...rest } = e
  return structuredClone(rest)
}

function pickExample(req: StartTurnRequest): ExampleName {
  if (req.fixture && (EXAMPLE_NAMES as string[]).includes(req.fixture)) return req.fixture as ExampleName
  const m = req.message.toLowerCase()
  if (/\bcancel\b/.test(m)) return 'approval_pause'
  if (/\b(where|track|order|status|lookup|look up)\b/.test(m)) return 'tool_call'
  return 'plain_answer'
}

/**
 * The payload of each payload node's enter and exit in a recorded stream (mock/payloads.ts),
 * keyed by the event or template object, counted per node and type as the recording was.
 */
function payloadsOf(name: ExampleName, items: Template[], message: string, reply: string | null): Map<Template, unknown> {
  const out = new Map<Template, unknown>()
  const seen = new Map<string, number>()
  for (const t of items) {
    if ((t.type !== 'node_enter' && t.type !== 'node_exit') || !t.node_id) continue
    const key = `${t.node_id}:${t.type}`
    const n = seen.get(key) ?? 0
    seen.set(key, n + 1)
    const detail = payloadFor(name, t, n, message, reply)
    if (!detail) continue
    out.set(t, detail)
    t.summary = { ...t.summary, has_payload: true } // as the emitter flags it (tracer.md)
  }
  return out
}

/** Give a finished turn's events their payloads: a detail_ref each, and the records by event id. */
function withPayloads(name: ExampleName, events: HarnessEvent[], message: string): Map<string, unknown> {
  const reply = (events[events.length - 1]?.summary?.reply as string | undefined) ?? null
  const details = new Map<string, unknown>()
  for (const [ev, detail] of payloadsOf(name, events, message, reply)) {
    const e = ev as HarnessEvent
    e.detail_ref = `trace://${e.trace_id}/${e.event_id}`
    details.set(e.event_id, detail)
  }
  return details
}

/** The rest of an approval stream as it would go if the human denied. */
function denyVariant(rest: Template[]): Template[] {
  const out: Template[] = []
  for (const t of rest) {
    const e = structuredClone(t)
    if (e.node_id === 'sandbox') continue
    if (e.summary && e.summary.decision === 'approve') e.summary.decision = 'deny'
    if (e.type === 'node_exit' && e.node_id === 'tools') e.summary = { ...e.summary, outcome: 'denied', executed: false }
    if (e.type === 'node_exit' && e.node_id === 'gateway' && e.summary?.phase === 'reply') e.summary.reply_chars = DENY_REPLY.length
    if (e.type === 'turn_end') e.summary = { ...e.summary, reply: DENY_REPLY }
    out.push(e)
  }
  return out
}

/** Disabled nodes run but do nothing: their span closes as skipped and their children vanish. */
function applyDisabled(templates: Template[], disabled: Set<string>, diagram: Diagram): Template[] {
  if (disabled.size === 0) return templates
  const parentOf = new Map(diagram.nodes.map((n) => [n.id, n.parent]))
  return templates
    .filter((t) => !(t.node_id && disabled.has(parentOf.get(t.node_id) ?? '')))
    .map((t) => {
      if (t.type === 'node_exit' && t.node_id && disabled.has(t.node_id)) {
        return { ...t, status: 'skipped' as const, summary: { reason: 'disabled' } }
      }
      return t
    })
}

function words(text: string): string[] {
  const parts = text.match(/\S+\s*/g) ?? [text]
  const chunks: string[] = []
  for (let i = 0; i < parts.length; i += 3) chunks.push(parts.slice(i, i + 3).join(''))
  return chunks
}

function summarize(turn: MockTurn, index?: number): TurnSummary {
  const end = turn.events.find((e) => e.type === 'turn_end')
  const pausedNode = turn.waiting
    ? turn.waiting.kind === 'breakpoint'
      ? turn.waiting.nodeId
      : 'approval'
    : null
  return {
    turn_id: turn.id,
    session_id: turn.sessionId,
    tenant_id: turn.tenantId,
    trace_id: turn.traceId,
    ...(index !== undefined ? { turn_index: index } : {}),
    message: turn.message,
    status: turn.status,
    exit: turn.exit,
    reply: turn.reply,
    started_at: turn.events[0]?.ts ?? new Date(turn.createdAt).toISOString(),
    ended_at: end?.ts ?? null,
    cost: (end?.cost as Cost | undefined) ?? null,
    event_count: turn.events.length,
    paused_at_node: pausedNode,
    pending_approval_id: turn.waiting?.kind === 'approval' ? turn.waiting.approvalId : null,
  }
}

export class MockServer {
  readonly opts: Required<Omit<MockOptions, 'public'>> & { public: MockPublicOptions | null }
  /** P6, public mode: this browser's anonymous tenant, its requests left, and when it ends. */
  private sandbox: { startedAt: number; remaining: number } | null = null
  readonly diagram: Diagram = servedDiagram()
  private turns = new Map<string, MockTurn>()
  private counter = 0
  readonly ops: MockOps
  readonly lessons: MockLessons
  /** P5: every session's pack (fixed at its first turn or by POST /v1/sessions). */
  private sessions = new Map<string, { pack: string; createdAt: string }>()
  private loadedPacks = new Set<string>([DEFAULT_PACK])
  /** P6: sessions of a swept sandbox tenant: a turn naming one is another tenant's, so 404. */
  private sweptSessions = new Set<string>()
  /** Payload records of templates still in a turn's queue, stored by event id when emitted. */
  private pendingDetails = new WeakMap<Template, unknown>()
  /** `budget=midturn`: the first Claude turn spent the budget; it reads as spent from then on. */
  private spentMidTurn = false
  /** Every POST /v1/turns body, in order: the mock e2e tests read what the UI sent. */
  readonly turnRequests: StartTurnRequest[] = []

  constructor(opts: MockOptions = {}) {
    this.opts = { stepMs: opts.stepMs ?? 140, seed: opts.seed ?? true, messageDelta: opts.messageDelta ?? true, public: opts.public ?? null }
    if (this.opts.public) this.sandbox = { startedAt: Date.now(), remaining: this.opts.public.remaining ?? 27 }
    // P4: the LLM Ops nodes are served by the ops half of the mock.
    for (const n of this.diagram.nodes) if (n.group === 'ops') n.implemented = true
    this.lessons = new MockLessons(this.diagram)
    this.ops = new MockOps({
      stepMs: this.opts.stepMs,
      diagram: this.diagram,
      turns: () => [...this.turns.values()],
      anyRunning: () => [...this.turns.values()].some((t) => t.status !== 'done'),
      addFinishedTurn: (example, message, sessionId, reply, pack) => this.addFinishedTurn(example, message, sessionId, reply, pack),
      packOf: (turnId) => this.turns.get(turnId)?.pack ?? DEFAULT_PACK,
    })
    if (this.opts.seed) {
      this.seedHistory()
      this.ops.seed(EXAMPLES.plain_answer[0]!.turn_id)
    }
  }

  /** A finished turn played instantly from a recorded stream (an eval case), with fresh ids. */
  private addFinishedTurn(example: ExampleName, message: string, sessionId: string, reply?: string, pack = DEFAULT_PACK): string {
    const turnNo = ++this.counter
    const id = `turn_${String(turnNo).padStart(3, '0')}_${hex(6)}`
    const traceId = hex(32)
    const start = Date.now()
    const events = EXAMPLES[example].map((e, seq) => {
      const t = toTemplate(e)
      if (t.type === 'turn_end' && reply !== undefined) t.summary = { ...t.summary, reply }
      if (t.type === 'turn_start') t.summary = { ...t.summary, pack }
      return {
        ...t,
        event_id: `evt_${id}_${String(seq).padStart(3, '0')}`,
        seq,
        ts: new Date(start + (Date.parse(e.ts) - Date.parse(EXAMPLES[example][0]!.ts))).toISOString(),
        trace_id: traceId,
        turn_id: id,
        session_id: sessionId,
        tenant_id: TENANT,
      } as HarnessEvent
    })
    const end = events[events.length - 1]!
    const details = withPayloads(example, events, message)
    this.turns.set(id, {
      id,
      details,
      message,
      sessionId,
      traceId,
      tenantId: TENANT,
      createdAt: start,
      events,
      queue: [],
      denyQueue: null,
      status: 'done',
      exit: (end.summary?.exit as TurnSummary['exit']) ?? 'answered',
      reply: (end.summary?.reply as string | undefined) ?? null,
      stepping: false,
      waiting: null,
      timer: null,
      sources: new Set(),
      disabled: new Set(),
      deltasSent: true,
      pack,
    })
    return id
  }

  /** The user messages behind the seeded turns, so the chat can show both sides. */
  seededMessages(): Record<string, string> {
    const out: Record<string, string> = {}
    for (const name of EXAMPLE_NAMES) out[`turn_${name}`] = EXAMPLE_MESSAGES[name]
    return out
  }

  private seedHistory() {
    for (const name of EXAMPLE_NAMES) {
      const events = EXAMPLES[name]
      const first = events[0]!
      const end = events[events.length - 1]!
      const seeded = structuredClone(events)
      const details = withPayloads(name, seeded, EXAMPLE_MESSAGES[name])
      this.turns.set(first.turn_id, {
        id: first.turn_id,
        details,
        message: EXAMPLE_MESSAGES[name],
        sessionId: first.session_id,
        traceId: first.trace_id,
        tenantId: first.tenant_id,
        createdAt: Date.parse(first.ts),
        events: seeded,
        queue: [],
        denyQueue: null,
        status: 'done',
        exit: (end.summary?.exit as TurnSummary['exit']) ?? 'answered',
        reply: (end.summary?.reply as string | undefined) ?? null,
        stepping: false,
        waiting: null,
        timer: null,
        sources: new Set(),
        disabled: new Set(),
        deltasSent: true,
        pack: DEFAULT_PACK,
      })
      this.sessions.set(first.session_id, { pack: DEFAULT_PACK, createdAt: first.ts })
    }
  }

  // ---------- transport ----------

  transport(): Transport {
    return {
      baseUrl: '',
      fetch: ((input: RequestInfo | URL, init?: RequestInit) => this.handle(input, init)) as typeof fetch,
      EventSource: boundEventSource(this),
    }
  }

  /** Simulate a network drop on every open stream. */
  dropConnections() {
    for (const turn of this.turns.values()) for (const s of [...turn.sources]) s.serverClose(true)
  }

  getTurn(id: string): MockTurn | undefined {
    return this.turns.get(id)
  }

  async handle(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
    const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://mock.local')
    const method = (init?.method ?? 'GET').toUpperCase()
    const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {}
    const p = url.pathname
    await new Promise((r) => setTimeout(r, 5))
    let m: RegExpMatchArray | null

    if (method === 'GET' && p === '/healthz') return json(200, { status: 'ok', mode: 'dev', provider: 'fake' })
    if (method === 'GET' && p === '/v1/capabilities') return json(200, this.capabilities())
    if (method === 'GET' && p === '/v1/diagram') return json(200, this.diagram)
    if (method === 'POST' && p === '/v1/turns') {
      this.turnRequests.push(structuredClone(body) as unknown as StartTurnRequest)
      const refused = this.publicRefusal(body as unknown as StartTurnRequest, header(init, 'x-harnesswalk-provider-key'))
      if (refused) return refused
      return this.startTurn(body as unknown as StartTurnRequest)
    }
    if ((m = p.match(/^\/v1\/trace\/turns\/([^/]+)\/details\/([^/]+)$/)) && method === 'GET') {
      const turn = this.turns.get(decodeURIComponent(m[1]!))
      if (!turn || turn.tenantId !== TENANT) return apiError(404, 'turn_not_found', 'No such turn for this tenant.')
      const eventId = decodeURIComponent(m[2]!)
      const ev = turn.events.find((e) => e.event_id === eventId)
      const detail = turn.details?.get(eventId)
      if (!ev?.detail_ref || detail === undefined) return apiError(404, 'detail_not_found', `No detail for event ${eventId}.`)
      return json(200, { turn_id: turn.id, event_id: eventId, detail_ref: ev.detail_ref, detail })
    }
    if ((m = p.match(/^\/v1\/turns\/([^/]+)$/)) && method === 'GET') {
      const found = this.turns.get(decodeURIComponent(m[1]!))
      // Eval case turns live in their run's scratch tenant, so the caller cannot read them here.
      const turn = found && !found.sessionId.startsWith('sess_eval_') ? found : undefined
      const index = turn ? [...this.turns.values()].filter((t) => t.sessionId === turn.sessionId && t.createdAt <= turn.createdAt).length - 1 : 0
      return turn ? json(200, { ...summarize(turn, index), ...this.ops.turnExtras(turn.id) }) : apiError(404, 'turn_not_found', 'No such turn for this tenant.')
    }
    if ((m = p.match(/^\/v1\/turns\/([^/]+)\/cancel$/)) && method === 'POST') return this.cancel(decodeURIComponent(m[1]!))
    if ((m = p.match(/^\/v1\/turns\/([^/]+)\/resume$/)) && method === 'POST') {
      return this.resume(decodeURIComponent(m[1]!), body.mode === 'continue' ? 'continue' : body.mode === 'step_over' ? 'step_over' : 'step')
    }
    if ((m = p.match(/^\/v1\/approvals\/([^/]+)$/)) && method === 'POST') {
      return this.decide(decodeURIComponent(m[1]!), body.decision, body.reason)
    }
    if ((m = p.match(/^\/v1\/sessions\/([^/]+)\/turns$/)) && method === 'GET') {
      const sid = decodeURIComponent(m[1]!)
      const limit = Number(url.searchParams.get('limit') ?? 50)
      const list = [...this.turns.values()].filter((t) => t.sessionId === sid)
      if (list.length === 0 && !this.sessions.has(sid)) return apiError(404, 'session_not_found', 'No such session for this tenant.')
      list.sort((a, b) => a.createdAt - b.createdAt)
      const indexed = list.map((t, i) => ({ ...summarize(t, i), ...this.ops.turnExtras(t.id) }))
      return json(200, indexed.reverse().slice(0, limit))
    }
    // P5: the packs, sessions on a pack, and a pack's files.
    if (method === 'GET' && p === '/v1/packs') return json(200, { default: DEFAULT_PACK, packs: PACK_IDS.map((id) => packSummary(id, this.loadedPacks)) })
    if ((m = p.match(/^\/v1\/packs\/([^/]+)\/files\/(.+)$/)) && method === 'GET') {
      const id = decodeURIComponent(m[1]!)
      const path = m[2]!.split('/').map(decodeURIComponent).join('/')
      if (!PACK_IDS.includes(id)) return apiError(404, 'pack_not_found', `No dataset pack ${id}.`)
      const file = packFiles(id).find((f) => f.path === path)
      return file ? json(200, { pack: id, path: file.path, bytes: file.bytes, role: file.role, kind: file.kind, text: file.text }) : apiError(404, 'file_not_found', `No file ${path} in pack ${id}.`)
    }
    if ((m = p.match(/^\/v1\/packs\/([^/]+)$/)) && method === 'GET') {
      const detail = packDetail(decodeURIComponent(m[1]!), this.loadedPacks)
      return detail ? json(200, detail) : apiError(404, 'pack_not_found', `No dataset pack ${m[1]}.`)
    }
    if (method === 'POST' && p === '/v1/sessions') {
      const pack = typeof body.pack === 'string' ? body.pack : DEFAULT_PACK
      if (!PACK_IDS.includes(pack)) return apiError(422, 'invalid_request', `no dataset pack '${pack}'`)
      const sid = `sess_${hex(20)}`
      const createdAt = new Date().toISOString()
      this.sessions.set(sid, { pack, createdAt })
      this.loadedPacks.add(pack)
      this.ops.ensure(pack)
      return json(201, { session_id: sid, pack, tenant_id: TENANT, created_at: createdAt })
    }
    if ((m = p.match(/^\/v1\/sessions\/([^/]+)$/)) && method === 'GET') {
      const sid = decodeURIComponent(m[1]!)
      const info = this.sessions.get(sid)
      if (!info) return apiError(404, 'session_not_found', `No session ${sid} for this tenant.`)
      return json(200, { session_id: sid, pack: info.pack, tenant_id: TENANT, created_at: info.createdAt, turns: [...this.turns.values()].filter((t) => t.sessionId === sid).length })
    }
    if ((m = p.match(/^\/v1\/nodes\/([^/]+)\/source$/)) && method === 'GET') return this.nodeSource(decodeURIComponent(m[1]!))
    if ((m = p.match(/^\/v1\/nodes\/([^/]+)$/)) && method === 'GET') return this.node(decodeURIComponent(m[1]!), this.packParam(url))
    // P3 context and control plane routes, over the starter pack's documents. The mock never
    // writes memory, so there is nothing to review, and it keeps no audit chain.
    if (method === 'GET' && p === '/v1/knowledge/docs') {
      const pack = this.packParam(url)
      return json(200, { pack, docs: mockDocs(pack).map(({ text: _t, chunks, ...d }) => ({ ...d, chunks: chunks.length })) })
    }
    if ((m = p.match(/^\/v1\/knowledge\/docs\/([^/]+)$/)) && method === 'GET') {
      const doc = mockDocs(this.packParam(url)).find((d) => d.doc_id === decodeURIComponent(m![1]!))
      return doc ? json(200, doc) : apiError(404, 'doc_not_found', 'No such document.')
    }
    if (method === 'GET' && p === '/v1/memory/proposals') {
      const pack = url.searchParams.get('pack')
      return json(200, { proposals: [], ...(pack ? { pack } : {}) })
    }
    if ((m = p.match(/^\/v1\/memory\/proposals\/([^/]+)$/)) && method === 'POST') return apiError(404, 'proposal_not_found', 'The mock has no proposals.')
    if (method === 'GET' && p === '/v1/audit') {
      const records = this.ops.audit
      return json(200, { tenant_id: 'dev', verify: { ok: true, records: records.length }, records, next_since: null })
    }
    // P5: the lesson chapters, from the real files in lessons/.
    if (method === 'GET' && p === '/v1/lessons') return json(200, this.lessons.list())
    if ((m = p.match(/^\/v1\/lessons\/([^/]+)\/source$/)) && method === 'GET') {
      const found = this.lessons.source(decodeURIComponent(m[1]!), url.searchParams.get('path') ?? '')
      if (found === 'lesson') return apiError(404, 'lesson_not_found', `No lesson ${m[1]}.`)
      if (found === 'source') return apiError(404, 'source_not_found', `Lesson ${m[1]} does not link to that file.`)
      return json(200, found)
    }
    if ((m = p.match(/^\/v1\/lessons\/([^/]+)$/)) && method === 'GET') {
      const lesson = this.lessons.get(decodeURIComponent(m[1]!))
      return lesson ? json(200, lesson) : apiError(404, 'lesson_not_found', `No lesson ${m[1]}.`)
    }
    // P4: evals, artifacts, releases, feedback and the trace query.
    const ops = this.ops.handle(method, url, body)
    if (ops) return await ops
    return apiError(404, 'not_found', `No route ${method} ${p} in the mock.`)
  }

  // ---------- P6 public mode ----------

  /** The sandbox's end; past it, the tenant and everything in it is gone and a fresh one starts. */
  private sandboxNow(): { startedAt: number; remaining: number } | null {
    const pub = this.opts.public
    if (!pub || !this.sandbox) return null
    const ttl = (pub.ttlS ?? 7200) * 1000
    if (Date.now() >= this.sandbox.startedAt + ttl) {
      // Swept: every turn and session of the tenant is deleted; the next cookie is a new tenant.
      for (const t of this.turns.values()) this.sweptSessions.add(t.sessionId)
      for (const sid of this.sessions.keys()) this.sweptSessions.add(sid)
      this.turns.clear()
      this.sessions.clear()
      this.sandbox = { startedAt: Date.now(), remaining: pub.remaining ?? 27 }
    }
    return this.sandbox
  }

  /** Today's shared budget is spent: `budget=spent`, or after the `midturn` fall-through. */
  private budgetSpent(): boolean {
    return this.opts.public?.budget === 'spent' || this.spentMidTurn
  }

  private publicModels(): { alias: string; label: string; provider: string; default: boolean; available: boolean; reason: 'budget_exhausted' | 'provider_unhealthy' | null }[] {
    const spent = this.budgetSpent()
    const unhealthy = this.opts.public?.unhealthy === true
    const models = PUBLIC_MODELS.map((m) => {
      const out = (m.alias === 'hosted-sonnet' || spent) && m.provider === 'anthropic'
      const reason: 'budget_exhausted' | 'provider_unhealthy' | null = !out ? null : unhealthy && m.alias === 'hosted-sonnet' ? 'provider_unhealthy' : 'budget_exhausted'
      return { ...m, available: !out, reason }
    })
    // `default`: what a turn without a model runs on now (the fake model once the budget is spent).
    const auto = models.find((m) => m.available)
    return models.map((m) => ({ ...m, default: m === auto }))
  }

  private publicInfo(): NonNullable<Capabilities['public']> {
    const box = this.sandboxNow()!
    const ttl = this.opts.public?.ttlS ?? 7200
    const midnight = new Date()
    midnight.setUTCHours(24, 0, 0, 0)
    const spent = this.budgetSpent()
    return {
      session_expires_at: new Date(box.startedAt + ttl * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z'),
      ttl_s: ttl,
      limits: { per_ip_per_hour: PER_HOUR, remaining_this_hour: box.remaining, max_steps: 8, budget_tokens: 16000, max_message_chars: 8000 },
      models: this.publicModels(),
      budget: { status: spent ? 'exhausted' : 'ok', resets_at: midnight.toISOString().replace(/\.\d{3}Z$/, 'Z'), spent_fraction: spent ? 1 : 0.42 },
      byo_key_bypasses_budget: true,
      fixtures: fixtureInfo(true),
      default_fixture: 'public_demo',
    }
  }

  /** The public-mode checks before a turn starts: the model allowlist, the budget, the rate limit. */
  private publicRefusal(req: StartTurnRequest, key: string | null): Response | null {
    const box = this.sandboxNow()
    if (!box) return null
    const models = this.publicModels()
    const model = req.model ?? models.find((m) => m.default)!.alias
    const entry = models.find((m) => m.alias === model)
    if (!entry) return apiError(400, 'invalid_request', 'model not available in public mode')
    if (!entry.available && entry.reason === 'provider_unhealthy') return apiError(503, 'provider_unavailable', 'The provider for this model is not answering.')
    if (!entry.available && !key) {
      return errorResponse(409, { error: { code: 'budget_exhausted', message: "Today's shared budget for Claude models is spent.", resets_at: this.publicInfo().budget.resets_at } })
    }
    // A fixture only for a turn on the scripted model (named, or what Auto runs on now).
    if (req.provider !== undefined) return apiError(400, 'invalid_request', DEV_ONLY)
    if (req.fixture !== undefined) {
      if (entry.provider !== 'fake') return apiError(400, 'invalid_request', DEV_ONLY)
      if (!fixtureInfo(true).some((f) => f.name === req.fixture)) return apiError(400, 'invalid_request', `no fixture named '${req.fixture}'`)
    }
    if (box.remaining <= 0) {
      return errorResponse(429, { error: { code: 'rate_limited', message: 'Too many requests from this address this hour.', retry_after: 720 } }, { 'Retry-After': '720' })
    }
    if (req.session_id && this.sweptSessions.has(req.session_id)) return apiError(404, 'session_not_found', `No session ${req.session_id} for this tenant.`)
    box.remaining -= 1
    return null
  }

  capabilities(): Capabilities {
    if (this.opts.public) return this.publicCapabilities()
    return {
      mode: 'dev',
      version: '0.2.0-mock',
      spec_version: '0.1.0',
      providers: [
        { name: 'fake', configured: true, default: true, models: ['fake-small'] },
        { name: 'ollama', configured: false, default: false, models: ['llama3.1:8b'] },
      ],
      // P5: the shared fixture and every pack's own (GET /v1/packs says which are whose).
      fixtures: allFixtures(),
      // Every mode has `public` (as the real API): every model, an ok budget, no session clock.
      public: {
        session_expires_at: null,
        ttl_s: 7200,
        limits: { per_ip_per_hour: PER_HOUR, remaining_this_hour: PER_HOUR, max_steps: 8, budget_tokens: 16000, max_message_chars: 4000 },
        models: [
          { alias: 'fake-small', label: 'fake-small', provider: 'fake', default: true, available: true, reason: null },
          { alias: 'llama3.1:8b', label: 'llama3.1:8b', provider: 'ollama', default: false, available: false, reason: 'provider_unconfigured' },
        ],
        budget: { status: 'ok', resets_at: null, spent_fraction: 0 },
        byo_key_bypasses_budget: true,
        fixtures: fixtureInfo(false),
        // The mock picks its recorded stream by the message when no fixture is named.
        default_fixture: null,
      },
      features: {
        step_mode: true,
        approvals: true,
        message_delta: this.opts.messageDelta,
        cancel: true,
        byo_key: false,
        disable_nodes: true,
        knowledge: true,
        memory_review: true,
        evals: true,
        judge: false,
        release: true,
        artifact_edit: true,
        feedback: true,
        step_over: true,
        packs: true,
        diagnose: true,
      },
      packs: { default: DEFAULT_PACK, ids: [...PACK_IDS] },
      repo_url: 'https://github.com/vynci/harnesswalk',
      limits: { max_message_chars: 4000, max_steps: 8, session_ttl_seconds: null },
      release: this.ops.releaseState(),
    }
  }

  /** Public mode: the site's capabilities, private-only features off, the `public` block. */
  private publicCapabilities(): Capabilities {
    const pub = this.publicInfo()
    return {
      mode: 'public',
      version: '0.2.0-mock',
      spec_version: '0.1.0',
      providers: [
        { name: 'anthropic', configured: true, default: true, models: ['hosted-haiku', 'hosted-sonnet'] },
        { name: 'fake', configured: true, default: false, models: ['fake-small'] },
      ],
      fixtures: [],
      features: {
        step_mode: true,
        approvals: true,
        message_delta: this.opts.messageDelta,
        cancel: true,
        byo_key: true,
        disable_nodes: false,
        knowledge: true,
        memory_review: false,
        evals: false,
        judge: false,
        release: true,
        artifact_edit: false,
        feedback: true,
        step_over: true,
        packs: true,
        diagnose: false,
      },
      packs: { default: DEFAULT_PACK, ids: [...PACK_IDS] },
      repo_url: 'https://github.com/vynci/harnesswalk',
      limits: { max_message_chars: pub.limits.max_message_chars, max_steps: pub.limits.max_steps, session_ttl_seconds: pub.ttl_s },
      release: this.ops.releaseState(),
      public: pub,
    }
  }

  // ---------- turns ----------

  private startTurn(req: StartTurnRequest): Response {
    if (typeof req.message !== 'string' || req.message.trim() === '') {
      return apiError(400, 'invalid_request', 'message is required')
    }
    const sessionId = req.session_id ?? `sess_${hex(10)}`
    const busy = [...this.turns.values()].find((t) => t.sessionId === sessionId && t.status !== 'done')
    if (busy) return apiError(409, 'turn_in_progress', `Turn ${busy.id} is still running in this session.`)
    // P5: a session's pack is fixed; a new session starts on the pack its options name.
    const asked = req.options?.pack
    const known = this.sessions.get(sessionId)
    if (asked !== undefined && !PACK_IDS.includes(asked)) return apiError(422, 'invalid_request', `no dataset pack '${asked}'`)
    if (known && asked && asked !== known.pack) return apiError(422, 'invalid_request', 'the dataset is fixed after the first turn')
    const pack = known?.pack ?? asked ?? DEFAULT_PACK
    if (!known) this.sessions.set(sessionId, { pack, createdAt: new Date().toISOString() })
    this.loadedPacks.add(pack)
    this.ops.ensure(pack)
    const name = pickExample(req)
    const disabled = new Set((req.disable ?? []).filter((id) => this.diagram.nodes.some((n) => n.id === id && n.can_disable)))
    const turnNo = ++this.counter
    const approvalId = `apr_${String(turnNo).padStart(4, '0')}_${hex(4)}`
    // A message asking for a table or markdown gets the markdown reply on the plain answer's stream.
    const markdown = name === 'plain_answer' && MARKDOWN_PROMPT.test(req.message)
    // P6, `budget=midturn`: the first Claude turn crosses the shared budget between two calls.
    const routed = this.opts.public ? (req.model ?? this.publicModels().find((m) => m.default)?.alias ?? '') : ''
    const fellThrough = this.opts.public?.budget === 'midturn' && !this.spentMidTurn && HOSTED.has(routed)
    if (fellThrough) this.spentMidTurn = true
    const templates = applyDisabled(EXAMPLES[name].map(toTemplate), disabled, this.diagram).map((t) => {
      if (fellThrough && t.type === 'turn_end') t = { ...t, summary: { ...t.summary, notices: [{ reason: 'budget_exhausted', message: BUDGET_NOTICE }] } }
      if (markdown && t.type === 'turn_end') return { ...t, summary: { ...t.summary, reply: MARKDOWN_REPLY } }
      if (markdown && t.type === 'node_exit' && t.node_id === 'gateway' && t.summary?.phase === 'reply') return { ...t, summary: { ...t.summary, reply_chars: MARKDOWN_REPLY.length } }
      return t.summary && typeof t.summary.approval_id === 'string' ? { ...t, summary: { ...t.summary, approval_id: approvalId } } : t
    })
    const first = templates.shift()!
    first.summary = { ...first.summary, step_mode: req.step === true, message_chars: req.message.length, pack }
    const endReply = (templates.find((t) => t.type === 'turn_end')?.summary?.reply as string | undefined) ?? null
    for (const [t, detail] of payloadsOf(name, templates, req.message, endReply)) {
      t.detail_ref = 'trace://pending'
      this.pendingDetails.set(t, detail)
    }
    const pauseAt = templates.findIndex((t) => t.type === 'node_paused' && t.summary?.reason === 'approval')
    const turn: MockTurn = {
      id: `turn_${String(turnNo).padStart(3, '0')}_${hex(6)}`,
      message: req.message,
      sessionId,
      traceId: hex(32),
      tenantId: TENANT,
      createdAt: Date.now(),
      events: [],
      queue: templates,
      denyQueue: pauseAt >= 0 ? denyVariant(templates.slice(pauseAt + 1)) : null,
      status: 'running',
      exit: null,
      reply: null,
      stepping: req.step === true,
      waiting: null,
      timer: null,
      sources: new Set(),
      disabled,
      deltasSent: false,
      pack,
      details: new Map(),
    }
    this.turns.set(turn.id, turn)
    this.emit(turn, first)
    this.schedule(turn)
    return json(202, {
      turn_id: turn.id,
      trace_id: turn.traceId,
      session_id: turn.sessionId,
      events_url: `/v1/turns/${turn.id}/events`,
    })
  }

  private emit(turn: MockTurn, t: Template): HarnessEvent {
    const seq = turn.events.length
    const ev = {
      event_id: `evt_${turn.id}_${String(seq).padStart(3, '0')}`,
      seq,
      ts: new Date(Math.max(Date.now(), turn.events.length ? Date.parse(turn.events[seq - 1]!.ts) : 0)).toISOString(),
      trace_id: turn.traceId,
      turn_id: turn.id,
      session_id: turn.sessionId,
      tenant_id: turn.tenantId,
      ...structuredClone(t),
    } as HarnessEvent
    if (ev.type === 'approval_requested' && ev.summary) {
      ev.summary.expires_at = new Date(Date.now() + 15 * 60_000).toISOString()
    }
    const detail = this.pendingDetails.get(t)
    if (detail) (turn.details ??= new Map()).set(ev.event_id, detail)
    else if (ev.detail_ref === 'trace://pending') delete ev.detail_ref // a copy (the deny variant) without its record
    if (ev.detail_ref) ev.detail_ref = `trace://${turn.traceId}/${ev.event_id}`
    turn.events.push(ev)
    for (const s of turn.sources) s.deliver(ev)
    return ev
  }

  /** Duration since the most recent open enter of this node, for synthetic closes. */
  private openStack(turn: MockTurn): { nodeId: string; ts: number }[] {
    const stack: { nodeId: string; ts: number }[] = []
    for (const e of turn.events) {
      if (e.type === 'node_enter') stack.push({ nodeId: e.node_id!, ts: Date.parse(e.ts) })
      else if ((e.type === 'node_exit' || e.type === 'error') && e.node_id) {
        for (let i = stack.length - 1; i >= 0; i--) {
          if (stack[i]!.nodeId === e.node_id) {
            stack.splice(i, 1)
            break
          }
        }
      }
    }
    return stack
  }

  private schedule(turn: MockTurn) {
    if (turn.timer || turn.status === 'done' || turn.waiting) return
    turn.timer = setTimeout(() => {
      turn.timer = null
      this.tick(turn)
    }, this.opts.stepMs)
  }

  private tick(turn: MockTurn) {
    if (turn.status === 'done' || turn.waiting) return
    const next = turn.queue.shift()
    if (!next) return
    // The reply gateway span streams the reply text before it closes.
    if (next.type === 'node_exit' && next.node_id === 'gateway' && next.summary?.phase === 'reply' && !turn.deltasSent && this.opts.messageDelta) {
      turn.deltasSent = true
      const end = turn.queue.find((t) => t.type === 'turn_end')
      const reply = (end?.summary?.reply as string | undefined) ?? ''
      const deltas: Template[] = words(reply).map((delta, index) => ({
        schema_version: '0.1.0',
        type: 'message_delta',
        node_id: 'gateway',
        status: 'active',
        summary: { delta, index },
      }))
      turn.queue.unshift(...deltas, next)
      this.tick(turn)
      return
    }
    const ev = this.emit(turn, next)
    // Step-over runs on through expert children and stops at the next simple-view node.
    const expert = ev.type === 'node_enter' && this.diagram.nodes.find((n) => n.id === ev.node_id)?.view === 'expert'
    if (ev.type === 'node_enter' && turn.stepping && turn.stepOver && expert) {
      this.schedule(turn)
      return
    }
    if (ev.type === 'node_enter' && turn.stepping) {
      turn.stepOver = false
      this.emit(turn, { schema_version: '0.1.0', type: 'node_paused', node_id: ev.node_id, status: 'waiting', summary: { reason: 'breakpoint' } })
      turn.waiting = { kind: 'breakpoint', nodeId: ev.node_id! }
      turn.status = 'paused'
      return
    }
    if (ev.type === 'node_paused' && ev.summary?.reason === 'approval') {
      turn.waiting = { kind: 'approval', approvalId: String(ev.summary.approval_id) }
      turn.status = 'paused'
      return
    }
    if (ev.type === 'turn_end') {
      this.finishTurn(turn, ev)
      return
    }
    this.schedule(turn)
  }

  private finishTurn(turn: MockTurn, end: HarnessEvent) {
    turn.status = 'done'
    turn.waiting = null
    turn.exit = (end.summary?.exit as TurnSummary['exit']) ?? 'answered'
    turn.reply = (end.summary?.reply as string | undefined) ?? null
    if (turn.timer) clearTimeout(turn.timer)
    turn.timer = null
    turn.queue = []
    for (const s of [...turn.sources]) s.serverClose(false)
  }

  private resume(turnId: string, mode: 'step' | 'continue' | 'step_over'): Response {
    const turn = this.turns.get(turnId)
    if (!turn) return apiError(404, 'turn_not_found', 'No such turn for this tenant.')
    if (turn.waiting?.kind !== 'breakpoint') return apiError(409, 'turn_not_paused', 'The turn is not at a breakpoint.')
    const nodeId = turn.waiting.nodeId
    if (mode === 'continue') turn.stepping = false
    turn.stepOver = mode === 'step_over'
    turn.waiting = null
    turn.status = 'running'
    this.emit(turn, { schema_version: '0.1.0', type: 'node_resumed', node_id: nodeId, status: 'active', summary: { reason: 'breakpoint', mode } })
    this.schedule(turn)
    return json(200, { turn_id: turnId, resumed_node_id: nodeId, mode })
  }

  private decide(approvalId: string, decision: unknown, reason: unknown): Response {
    if (decision !== 'approve' && decision !== 'deny') return apiError(400, 'invalid_request', 'decision must be approve or deny')
    const turn = [...this.turns.values()].find((t) =>
      t.events.some((e) => e.type === 'approval_requested' && e.summary?.approval_id === approvalId),
    )
    if (!turn) return apiError(404, 'approval_not_found', `No approval ${approvalId} for this tenant.`)
    if (turn.waiting?.kind !== 'approval' || turn.waiting.approvalId !== approvalId) {
      return apiError(409, 'approval_already_decided', `Approval ${approvalId} is decided.`)
    }
    if (decision === 'deny' && turn.denyQueue) turn.queue = turn.denyQueue
    const decidedAt = new Date().toISOString()
    const head = turn.queue[0]
    if (head?.type === 'approval_decided') head.summary = { ...head.summary, decision, decided_by: 'user:dev', ...(typeof reason === 'string' ? { reason } : {}) }
    turn.waiting = null
    turn.status = 'running'
    this.schedule(turn)
    return json(200, { approval_id: approvalId, turn_id: turn.id, decision, decided_by: 'user:dev', decided_at: decidedAt })
  }

  private cancel(turnId: string): Response {
    const turn = this.turns.get(turnId)
    if (!turn) return apiError(404, 'turn_not_found', 'No such turn for this tenant.')
    if (turn.status === 'done') return json(409, { error: { code: 'turn_finished', message: 'The turn has already finished.' }, turn: summarize(turn) })
    if (turn.timer) clearTimeout(turn.timer)
    turn.timer = null
    turn.queue = []
    turn.waiting = null
    const now = Date.now()
    const open = this.openStack(turn)
    for (let i = open.length - 1; i >= 0; i--) {
      const o = open[i]!
      this.emit(turn, {
        schema_version: '0.1.0',
        type: 'error',
        node_id: o.nodeId,
        status: 'error',
        duration_ms: Math.max(0, now - o.ts),
        error: { code: 'cancelled', message: 'The caller cancelled the turn.' },
      })
    }
    const cost = turn.events.reduce<Cost>(
      (acc, e) => (e.cost ? { input_tokens: acc.input_tokens + e.cost.input_tokens, output_tokens: acc.output_tokens + e.cost.output_tokens, usd: acc.usd + e.cost.usd } : acc),
      { input_tokens: 0, output_tokens: 0, usd: 0 },
    )
    const steps = turn.events.filter((e) => e.type === 'node_enter' && e.node_id === 'model_layer').length
    const end = this.emit(turn, {
      schema_version: '0.1.0',
      type: 'turn_end',
      status: 'done',
      duration_ms: Math.max(0, now - Date.parse(turn.events[0]!.ts)),
      summary: { exit: 'cancelled', steps },
      cost,
    })
    this.finishTurn(turn, end)
    return json(202, { turn_id: turnId, cancel_requested: true })
  }

  // ---------- nodes ----------

  /** `?pack=` on a pack-scoped route, default the configured pack. */
  private packParam(url: URL): string {
    const pack = url.searchParams.get('pack')
    return pack && PACK_IDS.includes(pack) ? pack : DEFAULT_PACK
  }

  private node(nodeId: string, pack = DEFAULT_PACK): Response {
    const node = this.diagram.nodes.find((n) => n.id === nodeId)
    if (!node) return apiError(404, 'node_not_found', `No node ${nodeId}.`)
    const recent = [...this.turns.values()]
      .sort((a, b) => b.createdAt - a.createdAt)
      .flatMap((t) => t.events.filter((e) => e.type === 'node_exit' && e.node_id === nodeId).reverse())
      .slice(0, 5)
      .map((e) => ({ turn_id: e.turn_id, status: e.status, summary: e.summary ?? {} }))
    const src = sourceFor(node.source_hint)
    const info: NodeInfo = {
      id: node.id,
      label: node.label,
      kind: node.kind,
      group: node.group,
      lesson: node.lesson,
      implemented: node.implemented ?? false,
      can_disable: node.can_disable ?? false,
      inspector: this.inspector(nodeId, recent, pack),
      source: { path: node.source_hint, language: 'python', content: src ?? '' },
      recent,
    }
    return json(200, info)
  }

  private inspector(nodeId: string, recent: NodeInfo['recent'], pack = DEFAULT_PACK): NodeInfo['inspector'] {
    const node = this.diagram.nodes.find((n) => n.id === nodeId)!
    switch (node.kind) {
      case 'config': {
        // The served working set (P4): what the harness loads in dev.
        const kind = { policy: 'policy', router: 'models', registry: 'tools' }[nodeId as string]
        const art = kind ? this.ops.current(kind, kind, pack) : undefined
        const path = { policy: 'config/policy.yaml', router: 'config/models.yaml', registry: `datasets/${pack}/tools.yaml` }[nodeId as string] ?? null
        const content = art?.body ?? (path ? repoFile(path) : undefined)
        return { type: 'config', path, version: art ? `${art.name}@${art.version}` : null, format: 'yaml', content: content ?? null, editable: true, artifact: kind ? this.ops.inspectorArtifact(kind, kind, pack) : null }
      }
      case 'prompt': {
        const prompt = this.ops.current('prompt', 'system_prompt', pack)
        const items = [{ name: 'system_prompt', version: `system_prompt@${prompt?.hash.slice(0, 8) ?? 'none'}`, content: prompt?.body ?? '', artifact: this.ops.inspectorArtifact('prompt', 'system_prompt', pack) }]
        for (const skill of this.ops.skills(pack)) {
          const art = this.ops.current('skill', skill, pack)
          if (art) items.push({ name: skill, version: `${skill}@${art.hash.slice(0, 8)}`, content: art.body, artifact: this.ops.inspectorArtifact('skill', skill, pack) })
        }
        return { type: 'prompt', items, editable: true, release: this.ops.releaseState(pack) }
      }
      case 'queue':
        return {
          type: 'queue',
          pending: [...this.turns.values()]
            .filter((t) => t.waiting?.kind === 'approval')
            .map((t) => ({ approval_id: (t.waiting as { approvalId: string }).approvalId, turn_id: t.id })),
        }
      case 'model':
        return {
          type: 'model',
          models: { 'fake-small': { provider: 'fake' } },
          router_rules: [{ name: 'default', model: 'fake-small' }],
          providers: ['fake'],
          last_routing: recent?.[0]?.summary ?? null,
          ...(node.artifact_hint === 'config/models.yaml' ? { artifact: this.ops.inspectorArtifact('models', 'models', pack) } : {}),
        }
      case 'store': {
        if (nodeId === 'trace') {
          return {
            type: 'store',
            turns: [...this.turns.values()].slice(-5).map((t) => ({ turn_id: t.id, status: t.status, events: t.events.length })),
          }
        }
        return { type: 'store', counts: {}, last_records: recent?.map((r) => ({ turn_id: r.turn_id, ...r.summary })) ?? [] }
      }
      default:
        return {
          type: 'source',
          path: node.source_hint,
          language: 'python',
          last_summary: recent?.[0] ?? null,
          ...(node.artifact_hint === 'config/context.yaml' ? { artifact: this.ops.inspectorArtifact('context', 'context', pack) } : {}),
        }
    }
  }

  private nodeSource(nodeId: string): Response {
    const node = this.diagram.nodes.find((n) => n.id === nodeId)
    if (!node) return apiError(404, 'node_not_found', `No node ${nodeId}.`)
    const source = sourceFor(node.source_hint)
    if (source === undefined) return apiError(404, 'source_not_found', `No source at ${node.source_hint}.`)
    const lines = source.split('\n')
    const symbols: NodeSource['symbols'] = []
    lines.forEach((line, i) => {
      const m = line.match(/^(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)/)
      if (m) symbols.push({ name: m[1]!, line: i + 1 })
    })
    const body: NodeSource = {
      node_id: nodeId,
      path: node.source_hint,
      language: 'python',
      line_count: lines.length,
      source,
      symbols,
    }
    return json(200, body)
  }

  attach(turnId: string, source: MockEventSource): MockTurn | undefined {
    const turn = this.turns.get(turnId)
    if (turn) turn.sources.add(source)
    return turn
  }

  detach(turnId: string, source: MockEventSource) {
    this.turns.get(turnId)?.sources.delete(source)
  }
}

function boundEventSource(server: MockServer): EventSourceCtor {
  // One constructor for both streams: a turn's events, or an eval run's progress (P4).
  return class {
    constructor(url: string) {
      const path = new URL(url, 'http://mock.local').pathname
      if (path === '/v1/events') return new MockOpsEventSource(server.ops, url)
      // An eval case's events_url serves its stored turn stream.
      const kase = path.match(/^\/v1\/evals\/runs\/([^/]+)\/cases\/([^/]+)\/events$/)
      if (kase) {
        const turnId = server.ops.caseTurn(decodeURIComponent(kase[1]!), decodeURIComponent(kase[2]!)) ?? 'turn_none'
        return new MockEventSource(server, `/v1/turns/${encodeURIComponent(turnId)}/events?follow=false`)
      }
      return /\/v1\/evals\/runs\/[^/]+\/events/.test(url) ? new MockEvalEventSource(server.ops, url) : new MockEventSource(server, url)
    }
  } as unknown as EventSourceCtor
}

type Listener = (ev: MessageEvent) => void

/** Enough of the EventSource interface for the stream client, with browser-like reconnects. */
export class MockEventSource implements EventSourceLike {
  static readonly CONNECTING = 0
  static readonly OPEN = 1
  static readonly CLOSED = 2
  readyState = 0
  url: string
  onopen: EventSourceLike['onopen'] = null
  onerror: EventSourceLike['onerror'] = null
  onmessage: EventSourceLike['onmessage'] = null
  private listeners = new Map<string, Set<Listener>>()
  private server: MockServer
  private turnId: string
  private after: number
  private follow: boolean
  private lastEventId = ''

  constructor(server: MockServer, url: string) {
    this.server = server
    this.url = url
    const u = new URL(url, 'http://mock.local')
    const m = u.pathname.match(/^\/v1\/turns\/([^/]+)\/events$/)
    this.turnId = m ? decodeURIComponent(m[1]!) : ''
    const after = u.searchParams.get('after')
    this.after = after === null ? -1 : Number(after)
    this.follow = u.searchParams.get('follow') !== 'false'
    setTimeout(() => this.open(), 0)
  }

  private open() {
    if (this.readyState === 2) return
    const turn = this.server.getTurn(this.turnId)
    if (!turn) {
      this.readyState = 2
      this.emitError()
      return
    }
    this.readyState = 1
    this.onopen?.call(this as unknown as EventSource, new Event('open'))
    const cursor = this.lastEventId ? Number(this.lastEventId) : this.after
    for (const ev of turn.events) if (ev.seq > cursor) this.deliver(ev)
    if (this.readyState !== 1) return
    if (!this.follow || turn.status === 'done') {
      this.serverClose(false)
      return
    }
    this.server.attach(this.turnId, this)
  }

  deliver(ev: HarnessEvent) {
    if (this.readyState !== 1) return
    this.lastEventId = String(ev.seq)
    const msg = new MessageEvent(ev.type, { data: JSON.stringify(ev), lastEventId: String(ev.seq) })
    const set = this.listeners.get(ev.type)
    if (set && set.size > 0) for (const l of set) l(msg)
    else this.onmessage?.call(this as unknown as EventSource, msg)
  }

  /** The server ended the response. A browser would then retry with Last-Event-ID. */
  serverClose(_dropped: boolean) {
    if (this.readyState === 2) return
    this.server.detach(this.turnId, this)
    this.readyState = 0
    this.emitError()
    if (this.readyState === 0) setTimeout(() => this.open(), 1000)
  }

  /** Like a browser: a connection error goes to `error` listeners as well as to onerror. */
  private emitError() {
    const ev = new Event('error')
    for (const l of this.listeners.get('error') ?? []) l(ev as MessageEvent)
    this.onerror?.call(this as unknown as EventSource, ev)
  }

  addEventListener(type: string, listener: Listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set())
    this.listeners.get(type)!.add(listener)
  }

  removeEventListener(type: string, listener: Listener) {
    this.listeners.get(type)?.delete(listener)
  }

  close() {
    this.readyState = 2
    this.server.detach(this.turnId, this)
  }
}

/**
 * A pack's documents, cut into chunks at each heading like the harness does first. A PDF the
 * pack ships is read from the markdown it was rendered from (`sources/`), as its text.
 */
function mockDocs(pack = DEFAULT_PACK) {
  return packFiles(pack)
    .filter((f) => f.path.startsWith('docs/') && /\.(md|pdf)$/.test(f.path))
    .map(({ path, text }) => {
      const name = path.split('/').pop()!
      const title = /^#\s+(.+)$/m.exec(text)?.[1]?.trim() ?? name
      const sections = text.split(/^##\s+/m)
      const chunks = sections.map((sec, position) => {
        const heading = position === 0 ? '' : (sec.split('\n')[0] ?? '').trim()
        const body = (position === 0 ? sec.replace(/^#\s+.+$/m, '') : sec.split('\n').slice(1).join('\n')).trim()
        return { position, heading, text: body, tokens: Math.ceil(body.length / 4), citation: heading ? `${title}, ${heading}` : title }
      })
      return { doc_id: name.replace(/\.(md|pdf)$/, ''), title, path: name, kind: name.endsWith('.pdf') ? 'pdf' : 'markdown', version: 'mock', scope: 'pack', text, chunks }
    })
}
