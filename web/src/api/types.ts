// Types for the harness event stream (spec/events.schema.json) and the HTTP API
// (spec/contracts/http_api.md plus the P2 additions in the phase-2 contract).

export type EventType =
  | 'turn_start'
  | 'turn_end'
  | 'node_enter'
  | 'node_exit'
  | 'node_paused'
  | 'node_resumed'
  | 'edge'
  | 'approval_requested'
  | 'approval_decided'
  | 'memory_proposed'
  | 'memory_written'
  | 'artifact_released'
  | 'message_delta'
  | 'error'
  // P4, point events outside a turn (turn_id null): feedback and working-set edits.
  | 'feedback_recorded'
  | 'artifact_changed'

export const EVENT_TYPES: readonly EventType[] = [
  'turn_start',
  'turn_end',
  'node_enter',
  'node_exit',
  'node_paused',
  'node_resumed',
  'edge',
  'approval_requested',
  'approval_decided',
  'memory_proposed',
  'memory_written',
  'artifact_released',
  'message_delta',
  'error',
  'feedback_recorded',
  'artifact_changed',
]

export type EventStatus = 'active' | 'waiting' | 'done' | 'error' | 'skipped'

export interface Cost {
  input_tokens: number
  output_tokens: number
  usd: number
}

export type Summary = Record<string, unknown>

export interface HarnessEvent {
  schema_version: string
  event_id: string
  seq: number
  ts: string
  trace_id: string
  turn_id: string
  session_id: string
  tenant_id: string
  type: EventType
  node_id?: string
  edge_id?: string
  status: EventStatus
  duration_ms?: number
  summary?: Summary
  detail_ref?: string | null
  cost?: Cost
  error?: { code: string; message: string }
  ext?: Record<string, unknown>
}

// ---------- Diagram ----------

export type PlaneId = 'control' | 'context' | 'execution' | 'ops'
export type NodeKind = 'code' | 'config' | 'prompt' | 'store' | 'queue' | 'model'
export type View = 'simple' | 'expert'
export type EdgeKind = 'request' | 'data' | 'control' | 'telemetry' | 'release'

export interface DiagramGroup {
  id: PlaneId
  label: string
  order: number
  description?: string
}

export interface DiagramNode {
  id: string
  group: PlaneId
  label: string
  kind: NodeKind
  view: View
  parent?: string
  lesson: string
  source_hint: string
  artifact_hint?: string
  can_disable?: boolean
  /** Fixed top-left in the simple view, with an optional expert-view position. */
  layout?: { x: number; y: number; expert?: { x: number; y: number } }
  /** Added by GET /v1/diagram. */
  implemented?: boolean
  stub?: boolean
  disabled?: boolean
}

export interface DiagramEdge {
  id: string
  from: string
  to: string
  label: string
  kind: EdgeKind
  view: View
  refines?: string
  layout?: EdgeLayoutHint
}

export type Side = 'top' | 'right' | 'bottom' | 'left'

/** Simple-view routing hints from diagram.json: ports by side, bend points, label side. */
export interface EdgeLayoutHint {
  handles?: { source?: Side; target?: Side }
  waypoints?: [number, number][]
  label?: 'above' | 'below' | 'left' | 'right'
}

export interface Diagram {
  spec_version: string
  title?: string
  groups: DiagramGroup[]
  nodes: DiagramNode[]
  edges: DiagramEdge[]
  walk_order: string[]
  paths: Record<string, string[]>
}

// ---------- HTTP ----------

export interface Capabilities {
  mode: 'dev' | 'public' | 'private'
  version: string
  spec_version?: string
  providers: { name: string; configured: boolean; default?: boolean; models: string[] }[]
  fixtures: string[]
  features: {
    step_mode: boolean
    approvals: boolean
    message_delta: boolean
    cancel: boolean
    byo_key: boolean
    disable_nodes?: boolean
    /** P3: knowledge is among the context stores, so replies may carry citations. */
    knowledge?: boolean
    /** P3: proposals waiting at the review gate can be accepted or rejected here. */
    memory_review?: boolean
    /** P4: golden and judge eval runs (POST /v1/evals/runs). */
    evals?: boolean
    /** P4: releases of the working set, with rollback. */
    release?: boolean
    /** P4: PUT /v1/artifacts/{kind}/{name} edits the working set. */
    artifact_edit?: boolean
    /** P4: Good and Bad feedback on a turn. */
    feedback?: boolean
  }
  /** P4: which release serves turns, and whether the working set has unreleased edits. */
  release?: { active_release_id: string | null; working_set_dirty: boolean }
  limits: {
    max_message_chars: number
    max_steps: number
    session_ttl_seconds: number | null
  }
}

export type TurnStatus = 'running' | 'paused' | 'done'
export type TurnExit = 'answered' | 'blocked' | 'limit' | 'error' | 'cancelled'

export interface TurnSummary {
  turn_id: string
  session_id: string
  tenant_id: string
  trace_id?: string
  turn_index?: number
  /** The user's message, when the API returns it. */
  message?: string | null
  status: TurnStatus
  exit: TurnExit | null
  reply: string | null
  started_at: string
  ended_at: string | null
  cost: Cost | null
  event_count: number
  paused_at_node: string | null
  pending_approval_id: string | null
  /** P4: the person's Good or Bad, null when none. Absent from APIs before P4. */
  feedback?: TurnFeedback | null
  /** P4: online judge scores by name, when this turn was sampled. */
  scores?: Record<string, number> | null
  /** P4: the loop checkpoint; `restored` after a restart resumed the turn from it. */
  checkpoint?: { step: number; at: string; restored: boolean } | null
}

export type FeedbackRating = 'good' | 'bad'

export interface TurnFeedback {
  rating: FeedbackRating
  note?: string | null
  at?: string
}

export interface StartTurnRequest {
  message: string
  session_id?: string
  step?: boolean
  provider?: string
  fixture?: string
  disable?: string[]
  /** P3: a JSON Schema the answer must satisfy, or the output guardrails block it. */
  output_schema?: Record<string, unknown>
  /** P3, dev mode: a model alias hint for the router. */
  model?: string
}

export interface StartTurnResponse {
  turn_id: string
  trace_id?: string
  session_id: string
  events_url?: string
}

export interface NodeInspector {
  type: 'source' | 'config' | 'prompt' | 'store' | 'queue' | 'model'
  [key: string]: unknown
}

export interface NodeInfo {
  id: string
  label: string
  kind: NodeKind
  group: PlaneId
  lesson: string
  implemented: boolean
  stub?: boolean
  can_disable?: boolean
  inspector: NodeInspector
  source?: { path: string; language: string; content: string }
  recent?: { turn_id: string; status: string; summary: Summary }[]
}

export interface NodeSource {
  node_id: string
  path: string
  language: string
  line_count: number
  source: string
  symbols?: { name: string; line: number }[]
}

// ---------- Context plane (P3) ----------

/** One entry of `turn_end.summary.citations`: the reply's `[n]` names chunk `chunk` of `doc`. */
export interface Citation {
  n: number
  doc: string
  chunk: number
  citation: string
}

export interface KnowledgeDoc {
  doc_id: string
  title: string
  path: string
  kind: string
  version: string
  chunks: number
  scope: 'pack' | 'tenant' | string
}

export interface KnowledgeChunk {
  position: number
  heading: string
  text: string
  tokens: number
  citation: string
}

export interface KnowledgeDocDetail extends Omit<KnowledgeDoc, 'chunks'> {
  text: string
  chunks: KnowledgeChunk[]
}

export interface MemoryDiff {
  op: string
  before: unknown
  after: unknown
}

export interface MemoryProposal {
  proposal_id: string
  store: string
  content: unknown
  source?: string
  evidence?: unknown
  gate?: string
  status: string
  reason?: string | null
  record_id?: string | null
  version?: number | null
  op?: string | null
  created_at?: string
  decided_at?: string | null
  decided_by?: string | null
  diff?: MemoryDiff
}

// ---------- Control plane (P3) ----------

export interface AuditRecord {
  record_id: string
  tenant_id: string
  ts: string
  entry: Record<string, unknown>
  prev_hash: string
  hash: string
}

export interface AuditExport {
  tenant_id: string
  verify: { ok: boolean; records: number; first_bad_record?: string }
  records: AuditRecord[]
  next_since: string | null
}

// ---------- LLM Ops (P4, phase-4 CONTRACT.md) ----------

export type EvalSuite = 'golden' | 'judge'
export type EvalCaseStatus = 'passed' | 'failed' | 'skipped' | 'error'

export interface EvalTotals {
  cases: number
  passed: number
  failed: number
  skipped: number
}

export interface EvalRunSummary {
  run_id: string
  pack: string
  suite: EvalSuite
  model: string | null
  started_at: string
  ended_at: string | null
  status: 'running' | 'done' | 'error'
  totals: EvalTotals
  /** How many cases failed now and passed in `previous_run_id`. */
  regressions: number
  previous_run_id: string | null
}

export interface EvalScore {
  value: number | boolean | string | null
  pass: boolean | null
  detail?: string | null
}

export interface EvalCase {
  case_id: string
  title: string
  status: EvalCaseStatus
  /** By scorer name: path, citations, tool_calls, schema, pii_leak, answer_match, faithfulness, relevance. */
  scores: Record<string, EvalScore>
  expected: Record<string, unknown> | null
  actual: { reply: string | null; path: string[]; citations: unknown[]; tool_calls: unknown[] } | null
  turn_id: string | null
  previous_status: EvalCaseStatus | null
  regression: boolean
}

export interface EvalRunDetail extends EvalRunSummary {
  cases: EvalCase[]
}

export interface StartEvalRequest {
  pack: string
  suite: EvalSuite
  model?: string
  cases?: string[]
}

export interface ArtifactRef {
  /** prompt, skill, policy, context, models or tools (phase-4 CONTRACT.md). */
  kind: string
  name: string
  version: number
  hash: string
  updated_at?: string
  updated_by?: string
}

export interface Artifact extends ArtifactRef {
  body: string
}

/** PUT /v1/artifacts/{kind}/{name}: the new working-set version and a unified diff against the one before. */
export interface ArtifactSaved extends Artifact {
  diff: string
}

export interface ReleaseSummary {
  release_id: string
  created_at: string
  note: string | null
  hash: string
  active: boolean
  artifacts: { kind: string; name: string; version: number; hash: string }[]
}

export interface ReleaseDiff {
  kind: string
  name: string
  from_version: number | null
  to_version: number | null
  diff: string
}

export interface ReleaseDetail extends ReleaseSummary {
  diff_from_previous: ReleaseDiff[]
}

export interface TraceQuery {
  session?: string
  node?: string
  status?: string
  since?: string
  until?: string
  limit?: number
  after?: string
}

/** One row of GET /v1/trace/turns: the turn with its receipt totals (tracer.md TurnSummary plus receipts). */
export interface TraceTurn {
  turn_id: string
  session_id: string
  trace_id?: string
  started_at: string
  ended_at?: string | null
  status: string
  exit?: string | null
  cost?: Cost | null
  steps?: number | null
  duration_ms?: number | null
  /** The nodes visited, in order. */
  path?: string[]
}

/** GET /v1/trace/turns/{id}/receipt, as tracer.md's Receipt. */
export interface Receipt {
  turn_id: string
  total: Cost
  by_node: Record<string, Cost>
  by_model: Record<string, Cost>
  latency_ms: Record<string, number>
}

/** A point event on the eval run stream (GET /v1/evals/runs/{id}/events). */
export interface EvalStreamEvent {
  type: 'eval_run_started' | 'eval_case_finished' | 'eval_run_finished'
  run_id?: string
  turn_id?: null
  tenant_id?: string
  summary?: Summary
}

export interface ApiErrorBody {
  error: { code: string; message: string }
}
