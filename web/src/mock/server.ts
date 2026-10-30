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
import { MockEvalEventSource, MockOps } from './ops'
import { repoFile, repoFilesUnder, sourceFor } from './repo'

export interface MockOptions {
  /** Milliseconds between streamed events. 0 plays as fast as timers allow. */
  stepMs?: number
  /** Seed session `sess_demo` with the three recorded turns. */
  seed?: boolean
  /** Split the reply into message_delta events inside the reply gateway span. */
  messageDelta?: boolean
}

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
}

const TENANT = 'dev'
const DENY_REPLY = 'I did not cancel order A-1002: the request was denied, so nothing was changed.'

function hex(n: number): string {
  let out = ''
  while (out.length < n) out += Math.floor(Math.random() * 16).toString(16)
  return out === '0'.repeat(n) ? hex(n) : out
}

function json(status: number, body: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function apiError(status: number, code: string, message: string): Response {
  return json(status, { error: { code, message } })
}

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
  readonly opts: Required<MockOptions>
  readonly diagram: Diagram = servedDiagram()
  private turns = new Map<string, MockTurn>()
  private counter = 0
  readonly ops: MockOps

  constructor(opts: MockOptions = {}) {
    this.opts = { stepMs: opts.stepMs ?? 140, seed: opts.seed ?? true, messageDelta: opts.messageDelta ?? true }
    // P4: the LLM Ops nodes are served by the ops half of the mock.
    for (const n of this.diagram.nodes) if (n.group === 'ops') n.implemented = true
    this.ops = new MockOps({
      stepMs: this.opts.stepMs,
      diagram: this.diagram,
      turns: () => [...this.turns.values()],
      anyRunning: () => [...this.turns.values()].some((t) => t.status !== 'done'),
      addFinishedTurn: (example, message, sessionId, reply) => this.addFinishedTurn(example, message, sessionId, reply),
    })
    if (this.opts.seed) {
      this.seedHistory()
      this.ops.seed(EXAMPLES.plain_answer[0]!.turn_id)
    }
  }

  /** A finished turn played instantly from a recorded stream (an eval case), with fresh ids. */
  private addFinishedTurn(example: ExampleName, message: string, sessionId: string, reply?: string): string {
    const turnNo = ++this.counter
    const id = `turn_${String(turnNo).padStart(3, '0')}_${hex(6)}`
    const traceId = hex(32)
    const start = Date.now()
    const events = EXAMPLES[example].map((e, seq) => {
      const t = toTemplate(e)
      if (t.type === 'turn_end' && reply !== undefined) t.summary = { ...t.summary, reply }
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
    this.turns.set(id, {
      id,
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
      this.turns.set(first.turn_id, {
        id: first.turn_id,
        message: EXAMPLE_MESSAGES[name],
        sessionId: first.session_id,
        traceId: first.trace_id,
        tenantId: first.tenant_id,
        createdAt: Date.parse(first.ts),
        events: structuredClone(events),
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
      })
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
    if (method === 'POST' && p === '/v1/turns') return this.startTurn(body as unknown as StartTurnRequest)
    if ((m = p.match(/^\/v1\/turns\/([^/]+)$/)) && method === 'GET') {
      const turn = this.turns.get(decodeURIComponent(m[1]!))
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
      if (list.length === 0) return apiError(404, 'session_not_found', 'No such session for this tenant.')
      list.sort((a, b) => a.createdAt - b.createdAt)
      const indexed = list.map((t, i) => summarize(t, i))
      return json(200, indexed.reverse().slice(0, limit))
    }
    if ((m = p.match(/^\/v1\/nodes\/([^/]+)\/source$/)) && method === 'GET') return this.nodeSource(decodeURIComponent(m[1]!))
    if ((m = p.match(/^\/v1\/nodes\/([^/]+)$/)) && method === 'GET') return this.node(decodeURIComponent(m[1]!))
    // P3 context and control plane routes, over the starter pack's documents. The mock never
    // writes memory, so there is nothing to review, and it keeps no audit chain.
    if (method === 'GET' && p === '/v1/knowledge/docs') return json(200, { pack: '_starter', docs: mockDocs().map(({ text: _t, chunks, ...d }) => ({ ...d, chunks: chunks.length })) })
    if ((m = p.match(/^\/v1\/knowledge\/docs\/([^/]+)$/)) && method === 'GET') {
      const doc = mockDocs().find((d) => d.doc_id === decodeURIComponent(m![1]!))
      return doc ? json(200, doc) : apiError(404, 'doc_not_found', 'No such document.')
    }
    if (method === 'GET' && p === '/v1/memory/proposals') return json(200, { proposals: [] })
    if ((m = p.match(/^\/v1\/memory\/proposals\/([^/]+)$/)) && method === 'POST') return apiError(404, 'proposal_not_found', 'The mock has no proposals.')
    if (method === 'GET' && p === '/v1/audit') return json(200, { tenant_id: 'dev', verify: { ok: true, records: 0 }, records: [], next_since: null })
    // P4: evals, artifacts, releases, feedback and the trace query.
    const ops = this.ops.handle(method, url, body)
    if (ops) return ops
    return apiError(404, 'not_found', `No route ${method} ${p} in the mock.`)
  }

  capabilities(): Capabilities {
    return {
      mode: 'dev',
      version: '0.2.0-mock',
      spec_version: '0.1.0',
      providers: [
        { name: 'fake', configured: true, default: true, models: ['fake-small'] },
        { name: 'ollama', configured: false, default: false, models: ['llama3.1:8b'] },
      ],
      fixtures: [...EXAMPLE_NAMES],
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
        release: true,
        artifact_edit: true,
        feedback: true,
      },
      limits: { max_message_chars: 4000, max_steps: 8, session_ttl_seconds: null },
      release: this.ops.releaseState(),
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
    const name = pickExample(req)
    const disabled = new Set((req.disable ?? []).filter((id) => this.diagram.nodes.some((n) => n.id === id && n.can_disable)))
    const turnNo = ++this.counter
    const approvalId = `apr_${String(turnNo).padStart(4, '0')}_${hex(4)}`
    const templates = applyDisabled(EXAMPLES[name].map(toTemplate), disabled, this.diagram).map((t) =>
      t.summary && typeof t.summary.approval_id === 'string' ? { ...t, summary: { ...t.summary, approval_id: approvalId } } : t,
    )
    const first = templates.shift()!
    first.summary = { ...first.summary, step_mode: req.step === true, message_chars: req.message.length }
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
    this.emit(turn, { schema_version: '0.1.0', type: 'node_resumed', node_id: nodeId, status: 'active', summary: { reason: 'breakpoint' } })
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

  private node(nodeId: string): Response {
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
      inspector: this.inspector(nodeId, recent),
      source: { path: node.source_hint, language: 'python', content: src ?? '' },
      recent,
    }
    return json(200, info)
  }

  private inspector(nodeId: string, recent: NodeInfo['recent']): NodeInfo['inspector'] {
    const node = this.diagram.nodes.find((n) => n.id === nodeId)!
    switch (node.kind) {
      case 'config': {
        // The served working set (P4): what the harness loads in dev.
        const kind = { policy: 'policy', router: 'models', registry: 'tools' }[nodeId as string]
        const art = kind ? this.ops.current(kind, kind) : undefined
        const path = { policy: 'config/policy.yaml', router: 'config/models.yaml', registry: 'datasets/_starter/tools.yaml' }[nodeId as string] ?? null
        const content = art?.body ?? (path ? repoFile(path) : undefined)
        return { type: 'config', path, version: art ? `${art.name}@${art.version}` : null, format: 'yaml', content: content ?? null, editable: true }
      }
      case 'prompt': {
        const prompt = this.ops.current('prompt', 'system_prompt')
        const items = [{ name: 'system_prompt', version: `system_prompt@${prompt?.version ?? 1}`, content: prompt?.body ?? '' }]
        for (const skill of ['order_status', 'returns']) {
          const art = this.ops.current('skill', skill)
          if (art) items.push({ name: skill, version: `${skill}@${art.version}`, content: art.body })
        }
        return { type: 'prompt', items, editable: true }
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
        return { type: 'source', path: node.source_hint, language: 'python', last_summary: recent?.[0] ?? null }
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

/** The starter pack's documents, cut into chunks at each heading like the harness does first. */
function mockDocs() {
  return repoFilesUnder('datasets/_starter/docs')
    .filter(([path]) => path.endsWith('.md'))
    .map(([path, text]) => {
      const name = path.split('/').pop()!
      const title = /^#\s+(.+)$/m.exec(text)?.[1]?.trim() ?? name
      const sections = text.split(/^##\s+/m)
      const chunks = sections.map((sec, position) => {
        const heading = position === 0 ? '' : (sec.split('\n')[0] ?? '').trim()
        const body = (position === 0 ? sec.replace(/^#\s+.+$/m, '') : sec.split('\n').slice(1).join('\n')).trim()
        return { position, heading, text: body, tokens: Math.ceil(body.length / 4), citation: heading ? `${title}, ${heading}` : title }
      })
      return { doc_id: name.replace(/\.md$/, ''), title, path: name, kind: 'markdown', version: 'mock', scope: 'pack', text, chunks }
    })
}
