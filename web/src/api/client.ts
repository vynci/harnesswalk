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
  EventDetail,
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
  Diagnosis,
  GateFailure,
  GoldenSet,
  Lesson,
  LessonList,
  LessonSource,
  PackDetail,
  PackFileText,
  PackSummary,
  SessionInfo,
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
  /** P6: seconds to wait, from a 429's `Retry-After` header (or its body's `retry_after`). */
  readonly retryAfter: number | null
  /** P6: the response's `X-Request-Id`, so an error report can name the request in the server's logs. */
  readonly requestId: string | null
  constructor(status: number, code: string, message: string, body?: unknown, retryAfter: number | null = null, requestId: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.body = body
    this.retryAfter = retryAfter
    this.requestId = requestId
  }
}

/**
 * The BYO key header (http_api.md, P3, kept in P6): sent on POST /v1/turns only, never stored by
 * the web app beyond the tab's memory.
 */
export const PROVIDER_KEY_HEADER = 'X-Harnesswalk-Provider-Key'

function retryAfterOf(res: Response, data: unknown): number | null {
  const header = res.headers.get('Retry-After')
  const fromHeader = header !== null && /^\d+$/.test(header.trim()) ? Number(header) : header ? Math.max(0, Math.round((Date.parse(header) - Date.now()) / 1000)) : NaN
  if (Number.isFinite(fromHeader)) return fromHeader
  const body = (data as { error?: { retry_after?: unknown }; retry_after?: unknown } | undefined) ?? {}
  const value = body.error?.retry_after ?? body.retry_after
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/** The server's request id (a plain token, every response since P6), or null. */
export function requestIdOf(res: Response): string | null {
  const id = res.headers?.get?.('X-Request-Id')?.trim()
  return id && /^[A-Za-z0-9._:-]{1,128}$/.test(id) ? id : null
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

/** POST /v1/releases: the release, or the gate's refusal (409 gate_failed) with its failing cases. */
export type CreateReleaseResult = { release: ReleaseDetail; gateFailed?: undefined } | { release?: undefined; gateFailed: GateFailure }

export function createApi(t: Transport) {
  /**
   * The session's pack (P5): every pack-scoped call (nodes, artifacts, releases, evals,
   * knowledge, proposals) sends it as `?pack=`, so switching packs switches what they show.
   * Null sends nothing and the API uses its configured pack.
   */
  let pack: string | null = null
  const scoped = (extra: Record<string, string | number | undefined | null> = {}) => query({ ...extra, pack })

  async function call<T>(method: string, path: string, body?: unknown, extra?: Record<string, string>): Promise<T> {
    const res = await t.fetch(`${t.baseUrl}${path}`, {
      method,
      headers: {
        ...(body === undefined ? { Accept: 'application/json' } : { 'Content-Type': 'application/json', Accept: 'application/json' }),
        ...extra,
      },
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
      throw new ApiError(res.status, err?.code ?? `http_${res.status}`, err?.message ?? res.statusText, data, res.status === 429 ? retryAfterOf(res, data) : null, requestIdOf(res))
    }
    return data as T
  }

  return {
    transport: t,
    capabilities: () => call<Capabilities>('GET', '/v1/capabilities'),
    diagram: () => call<Diagram>('GET', '/v1/diagram'),
    /** POST /v1/turns; `apiKey` (P6, BYO key) goes in the provider key header for this turn only. */
    startTurn: (req: StartTurnRequest, opts: { apiKey?: string | null } = {}) =>
      call<StartTurnResponse>('POST', '/v1/turns', req, opts.apiKey ? { [PROVIDER_KEY_HEADER]: opts.apiKey } : undefined),
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
    /** The pack the pack-scoped calls name; set by the store from the session. */
    setPack: (p: string | null) => {
      pack = p
    },
    pack: () => pack,
    node: (nodeId: string) => call<NodeInfo>('GET', `/v1/nodes/${encodeURIComponent(nodeId)}${scoped()}`),
    nodeSource: (nodeId: string) => call<NodeSource>('GET', `/v1/nodes/${encodeURIComponent(nodeId)}/source`),
    // P3: the context plane's routes and the audit export.
    knowledgeDocs: () => call<{ pack: string; docs: KnowledgeDoc[] }>('GET', `/v1/knowledge/docs${scoped()}`),
    knowledgeDoc: (docId: string) => call<KnowledgeDocDetail>('GET', `/v1/knowledge/docs/${encodeURIComponent(docId)}${scoped()}`),
    /** A URL the API returned for the browser to load itself (a document's `file.url`): an origin-relative one gets the API base. */
    resolveUrl: (url: string) => (url.startsWith('/') && !url.startsWith('//') ? `${t.baseUrl}${url}` : url),
    memoryProposals: (status: 'pending' | 'all' = 'pending') =>
      call<{ proposals: MemoryProposal[] }>('GET', `/v1/memory/proposals${scoped({ status })}`),
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
    /** The runs of the session's pack (or of `q.pack`), newest first. */
    evalRuns: async (q: { pack?: string; limit?: number } = {}) =>
      (await call<{ runs: EvalRunSummary[] }>('GET', `/v1/evals/runs${query({ ...q, pack: q.pack ?? pack })}`)).runs,
    golden: () => call<GoldenSet>('GET', `/v1/evals/golden${scoped()}`),
    evalRun: (runId: string) => call<EvalRunDetail>('GET', `/v1/evals/runs/${encodeURIComponent(runId)}`),
    evalRunEventsUrl: (runId: string) => `${t.baseUrl}/v1/evals/runs/${encodeURIComponent(runId)}/events`,
    artifacts: () => call<ArtifactList>('GET', `/v1/artifacts${scoped()}`),
    artifact: (kind: string, name: string, version?: number) =>
      call<Artifact>('GET', `/v1/artifacts/${encodeURIComponent(kind)}/${encodeURIComponent(name)}${scoped({ version })}`),
    saveArtifact: (kind: string, name: string, body: string, note?: string) =>
      call<ArtifactSaved>('PUT', `/v1/artifacts/${encodeURIComponent(kind)}/${encodeURIComponent(name)}${scoped()}`, note ? { body, note } : { body }),
    releaseList: () => call<ReleaseList>('GET', `/v1/releases${scoped()}`),
    /** Release ids are unique across packs, so one release needs no pack. */
    release: (releaseId: string) => call<ReleaseDetail>('GET', `/v1/releases/${encodeURIComponent(releaseId)}`),
    /**
     * POST /v1/releases on the session's pack. The eval gate runs first; a 409 gate_failed comes
     * back as `gateFailed` (no release was made). `force` skips the gate: dev mode only, audited.
     */
    createRelease: async (note?: string, opts: { force?: boolean } = {}): Promise<CreateReleaseResult> => {
      const body = { ...(note ? { note } : {}), ...(pack ? { pack } : {}), ...(opts.force ? { force: true } : {}) }
      try {
        return { release: await call<ReleaseDetail>('POST', '/v1/releases', body) }
      } catch (err) {
        if (err instanceof ApiError && err.status === 409 && err.code === 'gate_failed') {
          const data = (err.body ?? {}) as Partial<GateFailure> & { error?: { message?: string } }
          return {
            gateFailed: {
              pack: pack ?? '',
              message: data.error?.message ?? err.message,
              gate: data.gate ?? { status: 'failed' },
              cases: Array.isArray(data.cases) ? data.cases : [],
            },
          }
        }
        throw err
      }
    },
    rollback: (releaseId: string) => call<RollbackResult>('POST', `/v1/releases/${encodeURIComponent(releaseId)}/rollback`, {}),
    traceTurns: (q: TraceQuery = {}) => call<TraceTurnPage>('GET', `/v1/trace/turns${query({ ...q })}`),
    receipt: (turnId: string) => call<Receipt>('GET', `/v1/trace/turns/${encodeURIComponent(turnId)}/receipt`),
    /** One event's detail record: a hop's payload (tracer.md, "Payloads"). The turn's tenant only. */
    detail: (turnId: string, eventId: string) =>
      call<EventDetail>('GET', `/v1/trace/turns/${encodeURIComponent(turnId)}/details/${encodeURIComponent(eventId)}`),
    /** GET /v1/events: every ops event of the tenant, live (SSE). */
    opsEventsUrl: () => `${t.baseUrl}/v1/events`,
    // P5: the lesson chapters (harnesswalk/control/http_lessons.py).
    lessons: () => call<LessonList>('GET', '/v1/lessons'),
    lesson: (slug: string) => call<Lesson>('GET', `/v1/lessons/${encodeURIComponent(slug)}`),
    lessonSource: (slug: string, path: string) => call<LessonSource>('GET', `/v1/lessons/${encodeURIComponent(slug)}/source${query({ path })}`),
    /** P5: the packs, when `features.packs`. The envelope is `{default, packs}` or a bare list. */
    packs: async () => {
      const data = await call<{ packs?: PackSummary[] } | PackSummary[]>('GET', '/v1/packs')
      return Array.isArray(data) ? data : (data.packs ?? [])
    },
    /** P5 integration: one pack's card and file tree, and one of its files as text. */
    packDetail: (packId: string) => call<PackDetail>('GET', `/v1/packs/${encodeURIComponent(packId)}`),
    packFile: (packId: string, path: string) =>
      call<PackFileText>('GET', `/v1/packs/${encodeURIComponent(packId)}/files/${path.split('/').map(encodeURIComponent).join('/')}`),
    /** P5: a new session on a pack (sets the session cookie too), and one session's pack. */
    createSession: (packId?: string) => call<SessionInfo>('POST', '/v1/sessions', packId ? { pack: packId } : {}),
    session: (sessionId: string) => call<SessionInfo>('GET', `/v1/sessions/${encodeURIComponent(sessionId)}`),
    /** P5: the layer at fault for a failed eval case or a bad turn. */
    diagnose: (target: { run_id?: string; case_id: string } | { turn_id: string }) => call<Diagnosis>('POST', '/v1/diagnose', target),
  }
}

export type Api = ReturnType<typeof createApi>
