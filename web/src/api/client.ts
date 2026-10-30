// HTTP client for the harnesswalk API. The transport (fetch and EventSource) is injected so the
// in-browser mock can stand in for the server without the client knowing.
import type {
  ApiErrorBody,
  Artifact,
  ArtifactList,
  ArtifactSaved,
  AuditExport,
  EvalRunDetail,
  EvalRunSummary,
  FeedbackRating,
  FeedbackRecord,
  Receipt,
  ReleaseDetail,
  ReleaseList,
  RollbackResult,
  StartEvalRequest,
  TraceQuery,
  TraceTurnPage,
  Capabilities,
  Diagram,
  KnowledgeDoc,
  KnowledgeDocDetail,
  MemoryProposal,
  NodeInfo,
  NodeSource,
  StartTurnRequest,
  StartTurnResponse,
  TurnSummary,
} from './types'

export interface EventSourceLike {
  readonly readyState: number
  onopen: ((this: EventSource, ev: Event) => unknown) | null
  onerror: ((this: EventSource, ev: Event) => unknown) | null
  onmessage: ((this: EventSource, ev: MessageEvent) => unknown) | null
  addEventListener(type: string, listener: (ev: MessageEvent) => void): void
  close(): void
}

export type EventSourceCtor = new (url: string, init?: EventSourceInit) => EventSourceLike

export interface Transport {
  fetch: typeof fetch
  EventSource: EventSourceCtor
  /** Prefix for every path, '' for same origin. */
  baseUrl: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly body: unknown
  constructor(status: number, code: string, message: string, body?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.body = body
  }
}

export function browserTransport(baseUrl = ''): Transport {
  return {
    fetch: (...args) => globalThis.fetch(...args),
    EventSource: globalThis.EventSource as unknown as EventSourceCtor,
    baseUrl,
  }
}

export interface StreamQuery {
  /** Replay events with seq greater than this; omitted means from seq 0. */
  after?: number
  /** Keep streaming live after the replay (server default true). */
  follow?: boolean
}

export function eventsPath(turnId: string, q: StreamQuery = {}): string {
  const params = new URLSearchParams()
  if (q.after !== undefined && q.after >= 0) params.set('after', String(q.after))
  if (q.follow === false) params.set('follow', 'false')
  const qs = params.toString()
  return `/v1/turns/${encodeURIComponent(turnId)}/events${qs ? `?${qs}` : ''}`
}

function query(params: Record<string, string | number | undefined | null>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') q.set(k, String(v))
  const s = q.toString()
  return s ? `?${s}` : ''
}

export type ResumeMode = 'step' | 'continue' | 'step_over'

export function createApi(t: Transport) {
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await t.fetch(`${t.baseUrl}${path}`, {
      method,
      headers: body === undefined ? { Accept: 'application/json' } : { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'include',
    })
    const text = await res.text()
    let data: unknown = undefined
    if (text) {
      try {
        data = JSON.parse(text)
      } catch {
        data = text
      }
    }
    if (!res.ok) {
      const err = (data as ApiErrorBody | undefined)?.error
      throw new ApiError(res.status, err?.code ?? `http_${res.status}`, err?.message ?? res.statusText, data)
    }
    return data as T
  }

  return {
    transport: t,
    capabilities: () => call<Capabilities>('GET', '/v1/capabilities'),
    diagram: () => call<Diagram>('GET', '/v1/diagram'),
    startTurn: (req: StartTurnRequest) => call<StartTurnResponse>('POST', '/v1/turns', req),
    turn: (turnId: string) => call<TurnSummary>('GET', `/v1/turns/${encodeURIComponent(turnId)}`),
    sessionTurns: (sessionId: string, limit = 50) =>
      call<TurnSummary[]>('GET', `/v1/sessions/${encodeURIComponent(sessionId)}/turns?limit=${limit}`),
    cancel: (turnId: string) => call<unknown>('POST', `/v1/turns/${encodeURIComponent(turnId)}/cancel`, {}),
    resume: (turnId: string, mode: ResumeMode) =>
      call<{ turn_id: string; resumed_node_id: string; mode: string }>(
        'POST',
        `/v1/turns/${encodeURIComponent(turnId)}/resume`,
        { mode },
      ),
    decide: (approvalId: string, decision: 'approve' | 'deny', reason?: string) =>
      call<unknown>('POST', `/v1/approvals/${encodeURIComponent(approvalId)}`, reason ? { decision, reason } : { decision }),
    node: (nodeId: string) => call<NodeInfo>('GET', `/v1/nodes/${encodeURIComponent(nodeId)}`),
    nodeSource: (nodeId: string) => call<NodeSource>('GET', `/v1/nodes/${encodeURIComponent(nodeId)}/source`),
    // P3: the context plane's routes and the audit export.
    knowledgeDocs: () => call<{ pack: string; docs: KnowledgeDoc[] }>('GET', '/v1/knowledge/docs'),
    knowledgeDoc: (docId: string) => call<KnowledgeDocDetail>('GET', `/v1/knowledge/docs/${encodeURIComponent(docId)}`),
    memoryProposals: (status: 'pending' | 'all' = 'pending') =>
      call<{ proposals: MemoryProposal[] }>('GET', `/v1/memory/proposals?status=${status}`),
    decideProposal: (proposalId: string, decision: 'accept' | 'reject', reason?: string) =>
      call<MemoryProposal>(
        'POST',
        `/v1/memory/proposals/${encodeURIComponent(proposalId)}`,
        reason ? { decision, reason } : { decision },
      ),
    audit: (limit = 200) => call<AuditExport>('GET', `/v1/audit?limit=${limit}`),
    eventsUrl: (turnId: string, q?: StreamQuery) => `${t.baseUrl}${eventsPath(turnId, q)}`,
    // P4: LLM Ops (spec/contracts/http_api.md, "P4 additions").
    feedback: (turnId: string, rating: FeedbackRating, note?: string) =>
      call<FeedbackRecord>('POST', `/v1/turns/${encodeURIComponent(turnId)}/feedback`, note ? { rating, note } : { rating }),
    startEval: (req: StartEvalRequest) => call<{ run_id: string; events_url: string }>('POST', '/v1/evals/runs', req),
    evalRuns: async (q: { pack?: string; limit?: number } = {}) => (await call<{ runs: EvalRunSummary[] }>('GET', `/v1/evals/runs${query(q)}`)).runs,
    evalRun: (runId: string) => call<EvalRunDetail>('GET', `/v1/evals/runs/${encodeURIComponent(runId)}`),
    evalRunEventsUrl: (runId: string) => `${t.baseUrl}/v1/evals/runs/${encodeURIComponent(runId)}/events`,
    artifacts: (pack?: string) => call<ArtifactList>('GET', `/v1/artifacts${query({ pack })}`),
    artifact: (kind: string, name: string, version?: number) =>
      call<Artifact>('GET', `/v1/artifacts/${encodeURIComponent(kind)}/${encodeURIComponent(name)}${query({ version })}`),
    saveArtifact: (kind: string, name: string, body: string, note?: string) =>
      call<ArtifactSaved>('PUT', `/v1/artifacts/${encodeURIComponent(kind)}/${encodeURIComponent(name)}`, note ? { body, note } : { body }),
    releaseList: (pack?: string) => call<ReleaseList>('GET', `/v1/releases${query({ pack })}`),
    release: (releaseId: string) => call<ReleaseDetail>('GET', `/v1/releases/${encodeURIComponent(releaseId)}`),
    createRelease: (note?: string) => call<ReleaseDetail>('POST', '/v1/releases', note ? { note } : {}),
    rollback: (releaseId: string) => call<RollbackResult>('POST', `/v1/releases/${encodeURIComponent(releaseId)}/rollback`, {}),
    traceTurns: (q: TraceQuery = {}) => call<TraceTurnPage>('GET', `/v1/trace/turns${query({ ...q })}`),
    receipt: (turnId: string) => call<Receipt>('GET', `/v1/trace/turns/${encodeURIComponent(turnId)}/receipt`),
    /** GET /v1/events: every ops event of the tenant, live (SSE). */
    opsEventsUrl: () => `${t.baseUrl}/v1/events`,
  }
}

export type Api = ReturnType<typeof createApi>
