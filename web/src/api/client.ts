// HTTP client for the harnesswalk API. The transport (fetch and EventSource) is injected so the
// in-browser mock can stand in for the server without the client knowing.
import type {
  ApiErrorBody,
  AuditExport,
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
    resume: (turnId: string, mode: 'step' | 'continue') =>
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
  }
}

export type Api = ReturnType<typeof createApi>
