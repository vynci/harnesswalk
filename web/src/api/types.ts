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
  /** P5: the chapter about this node, a repo path (`lessons/NN-<id>.md`, with `#section` for an expert node). */
  lesson_url?: string
  /** P5: one short sentence on what the chapter teaches. */
  lesson_summary?: string
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
    /** P4: a judge model is configured, so the judge suite can run. */
    judge?: boolean
    /** P4: resume with mode step_over. */
    step_over?: boolean
    /** P5: GET /v1/packs lists the packs, with their suggested prompts. */
    packs?: boolean
    /** P5: POST /v1/diagnose maps a failed case or a bad turn to the layer at fault (dev and private). */
    diagnose?: boolean
  }
  /** P5: the configured pack and every pack id (GET /v1/packs has the cards). */
  packs?: { default: string; ids: string[] }
  /** P5 integration: the repository the lessons' code links open, without a trailing slash. */
  repo_url?: string
  /** P4: which release serves turns, and whether the working set has unreleased edits. */
  release?: ReleaseState
  limits: {
    max_message_chars: number
    max_steps: number
    session_ttl_seconds: number | null
  }
  /** P6: the public site's models, limits, shared budget and sandbox session (null-ish outside public). */
  public?: PublicInfo
}

/** Why a model cannot be picked: the shared budget is spent, or no key reaches its provider. */
export type ModelUnavailableReason = 'budget_exhausted' | 'provider_unconfigured'

/** One entry of `capabilities.public.models`: what the composer's model picker lists. */
export interface PublicModel {
  alias: string
  label: string
  provider: string
  default: boolean
  available: boolean
  reason: ModelUnavailableReason | null
}

/** `capabilities.public` (phase-6 CONTRACT section 2). No dollar amounts leave the server. */
export interface PublicInfo {
  /** When this browser's anonymous tenant and everything in it is deleted; null outside public. */
  session_expires_at: string | null
  ttl_s: number
  limits: {
    per_ip_per_hour: number
    remaining_this_hour: number
    max_steps: number
    budget_tokens: number
    max_message_chars: number
  }
  models: PublicModel[]
  budget: { status: 'ok' | 'exhausted'; resets_at: string | null; spent_fraction: number }
  byo_key_bypasses_budget: boolean
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
  /** P4: what the turn ran on (`turn_start.summary.release`). */
  release?: { release_id: string | null; working_set: boolean; artifacts_hash: string } | null
  /** P5: the pack the turn ran on (`turn_start.summary.pack`). */
  pack?: string | null
  /** P5: the latest diagnosis of this turn, or null. */
  diagnosis?: Diagnosis | null
}

export type FeedbackRating = 'good' | 'bad'

export interface TurnFeedback {
  rating: FeedbackRating
  note?: string | null
  at?: string
}

/** POST /v1/turns/{id}/feedback: the stored record. */
export interface FeedbackRecord extends TurnFeedback {
  turn_id: string
  by?: string
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
  /** P5: `options.pack` names the pack of a new session (fixed after its first turn). */
  options?: { pack?: string }
}

export interface StartTurnResponse {
  turn_id: string
  trace_id?: string
  session_id: string
  events_url?: string
}

export interface NodeInspector {
  type: 'source' | 'config' | 'prompt' | 'store' | 'queue' | 'model'
  /** P4: the release artifact this inspector shows, to edit with PUT /v1/artifacts/{kind}/{name}. */
  artifact?: InspectorArtifact | null
  [key: string]: unknown
}

/** `inspector.artifact` (http_api.md, GET /v1/nodes/{id}): which working-set artifact a node shows. */
export interface InspectorArtifact {
  kind: string
  name: string
  version: number
  url: string
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

// ---------- Lessons (P5, harnesswalk/control/http_lessons.py) ----------

/** A chapter's `try` front matter: what the "try it" button sets up for the next turn. */
export interface LessonTry {
  /** P5 integration: the pack the demonstration runs on (`_starter` when the chapter names none). */
  pack?: string
  fixture?: string
  prompt?: string
  /** Node ids to disable on the next turn. */
  disable?: string[]
}

/** One chapter in GET /v1/lessons. */
export interface LessonListItem {
  /** `NN-<name>`, the file name without `.md`. */
  slug: string
  n: number
  title: string
  hook: string
  /** The node the chapter is about; for an expert group chapter, the parent. */
  node_id: string | null
  /** The nodes the chapter covers: `[node_id]`, or the expert children of a group chapter. */
  covers: string[]
  /** An expert group chapter (`NN-<parent>-inside`). */
  group: boolean
  words: number
  url: string
}

export interface LessonList {
  lessons: LessonListItem[]
  walk_order: string[]
}

/** A code walk link, `[text](../harnesswalk/<path>#<Symbol>)`, with the symbol's line. */
export interface LessonCodeLink {
  path: string
  symbol: string | null
  line: number | null
  /** The node whose code peek is this file, else null. */
  node_id: string | null
}

/** GET /v1/lessons/{slug}: the list item plus the markdown (front matter stripped). */
/** P5 integration: the same demonstration on a domain pack, with a note on what it shows there. */
export interface LessonAlso extends LessonTry {
  note?: string
}

export interface Lesson extends LessonListItem {
  markdown: string
  try: LessonTry | null
  also?: LessonAlso[]
  code_links: LessonCodeLink[]
  prev: string | null
  next: string | null
}

/** GET /v1/lessons/{slug}/source?path=: a file the chapter links to, shaped like the code peek. */
export interface LessonSource {
  path: string
  language: string
  line_count: number
  source: string
  truncated?: boolean
  symbols?: { name: string; line: number }[]
}

/**
 * One pack in GET /v1/packs (P5). `prompts` may be strings or `{prompt}` objects, so it is read
 * through normalizePrompts.
 */
export interface PackSummary {
  id: string
  title?: string
  domain?: string | null
  /** The one harness feature this pack shows off. */
  feature?: string | null
  prompts?: unknown
  /** docs, facts, episodes, skills, tools and golden, counted from the pack's files. */
  counts?: Record<string, number>
  /** True for the configured `dataset` (GET /v1/packs, P5). */
  default?: boolean
  /** This process has built the pack (a session ran on it). */
  loaded?: boolean
  /** The pack's own fake fixtures, dev mode only (P5 integration). */
  fixtures?: string[]
}

export interface PackFile {
  path: string
  bytes: number
  /**
   * Which part of the harness reads the file (http_api.md, GET /v1/packs/{id}): a node id such as
   * the procedural or knowledge store, `knowledge_source`, `golden`, `fixture`, `card` or `other`.
   * The UI matches it against the diagram's ids and never names one.
   */
  role: string
}

/** GET /v1/packs/{id} (P5 integration): the card and the file tree. */
export interface PackDetail extends PackSummary {
  summary?: string | null
  tier?: string | null
  demonstrates?: { id?: string; nodes?: string[]; summary?: string } | null
  licence?: string | null
  tools?: string[]
  size_bytes?: number
  files: PackFile[]
}

/** GET /v1/packs/{id}/files/{path}: one file as text (`pdf_text` is the text pulled from a PDF). */
export interface PackFileText extends PackFile {
  pack: string
  kind: 'text' | 'pdf_text' | string
  text: string
}

/** POST /v1/sessions and GET /v1/sessions/{id} (P5). */
export interface SessionInfo {
  session_id: string
  pack: string
  tenant_id: string
  created_at: string
  turns?: number
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
  /** P5 integration: the pack whose memory writer made it. */
  pack?: string
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

// ---------- LLM Ops (P4, spec/contracts/http_api.md "P4 additions") ----------

export type EvalSuite = 'golden' | 'judge'
export type EvalCaseStatus = 'passed' | 'failed' | 'skipped' | 'error'

export interface EvalTotals {
  cases: number
  passed: number
  /** Failed and error cases together (http_api.md). */
  failed: number
  skipped: number
}

export interface EvalRunSummary {
  run_id: string
  tenant_id?: string
  pack: string
  suite: EvalSuite
  model: string | null
  judge_model?: string | null
  started_at: string
  ended_at: string | null
  status: 'running' | 'done' | 'error'
  error?: { code: string; message: string }
  totals: EvalTotals
  /** How many cases failed or errored now and passed in `previous_run_id`. */
  regressions: number
  regression_cases?: string[]
  previous_run_id: string | null
  /** What was under test. */
  release_id?: string | null
  working_set?: boolean
  artifacts_hash?: string
  cost?: Cost
  diff?: { fixed: string[]; regressed: string[]; new: string[]; removed: string[] }
  /** P5: `release_gate` for the run a release's gate started, else null. */
  purpose?: 'release_gate' | string | null
  /** P5: the layer of each diagnosed case. */
  diagnoses?: Record<string, string>
  case_tenant?: string
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
  actual: { reply: string | null; path: string[]; citations: unknown[]; tool_calls: unknown[]; exit?: string } | null
  turn_id: string | null
  /** The case turn's stored stream (it lives in the run's scratch tenant, not the caller's). */
  events_url?: string | null
  previous_status: EvalCaseStatus | null
  regression: boolean
  error?: { code: string; message: string }
  /** Why a skipped case was skipped (for example "applies in dev mode only, not private"). */
  detail?: string
  /** P5 integration: the earlier case whose memory writes this one reads. */
  after?: string | null
  /** P5: the latest diagnosis of this case, or null. */
  diagnosis?: Diagnosis | null
}

/** One trace fact a diagnosis found (evaluator.md, P5 additions). */
export interface DiagnosisEvidence {
  kind: string
  fact: string
  [key: string]: unknown
}

export type DiagnosisLayer = 'retrieval' | 'prompt_or_skill' | 'tool' | 'model' | 'guardrail' | 'budget' | 'unknown'

/** POST /v1/diagnose: the layer at fault, the evidence and what to look at first. */
export interface Diagnosis {
  diagnosis_id?: string
  target?: 'case' | 'turn'
  run_id?: string
  case_id?: string
  turn_id?: string
  pack?: string
  layer: DiagnosisLayer | string
  evidence?: DiagnosisEvidence[]
  suggestion?: string
  baseline?: { run_id: string; release_id: string | null; artifacts_hash: string } | null
  automatic?: boolean
  created_at?: string
}

/** One case of a pack's golden set (GET /v1/evals/golden), as stored. */
export interface GoldenCase {
  id: string
  title?: string
  prompt?: string
  fixture?: string
  expect?: Record<string, unknown>
  /** P5: what the `checks` scorer checks. */
  checks?: { router_rules?: string[]; retrieved?: Record<string, string> } | null
  /** P5 integration: the modes the case applies in; absent means every mode. */
  modes?: string[] | null
  /** P5 integration: the earlier case whose memory writes this one reads. */
  after?: string | null
  tags?: string[]
}

export interface GoldenSet {
  pack: string
  path: string
  cases: GoldenCase[]
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

export interface ReleaseState {
  active_release_id: string | null
  working_set_dirty: boolean
  use_working_set?: boolean
}

export interface ArtifactRef {
  /** prompt, skill, policy, context, models or tools (http_api.md). */
  kind: string
  name: string
  version: number
  hash: string
  updated_at?: string
  updated_by?: string
  note?: string | null
  deleted?: boolean
  /** The version turns are served (the active release's, or the working set's in dev). */
  served_version?: number | null
}

export interface ArtifactList {
  pack: string
  artifacts: ArtifactRef[]
  release: ReleaseState
}

export interface Artifact extends ArtifactRef {
  body: string
  path?: string
  latest_version?: number
  history?: ArtifactRef[]
}

/** PUT /v1/artifacts/{kind}/{name}: the new working-set version and a unified diff against the one before. */
export interface ArtifactSaved extends Artifact {
  diff: string
  previous_version: number | null
  /** The same body again: no new version. */
  unchanged?: boolean
  /** The harness reloaded, so the next turn uses it. */
  applied?: boolean
  release?: ReleaseState
}

/** P5: the eval gate a release passed through (release.md, P5 additions); null before P5. */
export interface ReleaseGate {
  status: 'passed' | 'failed' | 'skipped'
  reason?: string | null
  suite?: string | null
  min_pass_rate?: number | null
  pass_rate?: number | null
  run_ids?: string[]
  failed_cases?: string[]
  forced?: boolean
}

/** A failing case in a 409 `gate_failed` answer. */
export interface GateFailedCase {
  case_id: string
  title?: string
  status: EvalCaseStatus
  run_id?: string
  events_url?: string | null
  scores?: Record<string, EvalScore>
  error?: { code: string; message: string } | null
  diagnosis?: Pick<Diagnosis, 'layer' | 'suggestion'> | null
}

/** POST /v1/releases answered 409 gate_failed: no release was made. */
export interface GateFailure {
  pack: string
  message: string
  gate: ReleaseGate
  cases: GateFailedCase[]
}

export interface ReleaseSummary {
  release_id: string
  pack?: string
  created_at: string
  created_by?: string
  note: string | null
  hash: string
  prev_hash: string
  active: boolean
  artifacts: { kind: string; name: string; version: number; hash: string }[]
  gate?: ReleaseGate | null
}

export interface ReleaseList extends ReleaseState {
  pack: string
  releases: ReleaseSummary[]
  /** P5: `activations` counts the activation log rows; `activation_broken_at` is the first bad one. */
  verify: { ok: boolean; releases: number; broken_at?: string; artifact?: string; activations?: number; activation_broken_at?: number }
  activations?: { release_id: string; at: string; by: string; rollback_of: string | null }[]
}

export interface ReleaseDiff {
  kind: string
  name: string
  from_version: number | null
  to_version: number | null
  diff: string
}

export interface ReleaseDetail extends ReleaseSummary {
  previous_release_id?: string | null
  diff_from_previous: ReleaseDiff[]
}

/** POST /v1/releases/{id}/rollback. */
export interface RollbackResult extends ReleaseDetail, ReleaseState {
  rollback_of: string | null
  reset: { kind: string; name: string; from_version: number; to_version: number; restored_version?: number; deleted?: boolean }[]
}

export interface TraceQuery {
  session?: string
  node?: string
  status?: string
  /** chat, or eval for the case turns of the caller's eval runs. */
  kind?: 'chat' | 'eval'
  since?: string
  until?: string
  limit?: number
  after?: string
}

/** One row of GET /v1/trace/turns: the turn with its receipt totals (tracer.md TurnSummary plus receipts). */
export interface TraceTurn {
  turn_id: string
  tenant_id?: string
  session_id: string
  trace_id?: string
  started_at: string
  ended_at?: string | null
  status: string
  exit?: string | null
  cost?: Cost | null
  steps?: number | null
  duration_ms?: number | null
  /** The simple-view nodes visited, in order. */
  path?: string[]
  kind?: 'chat' | 'eval' | string
  channel?: string | null
  /** Eval case turns: the run, the case and the stream to replay. */
  run_id?: string
  case_id?: string | null
  events_url?: string | null
}

export interface TraceTurnPage {
  turns: TraceTurn[]
  next_after: string | null
}

/** GET /v1/trace/turns/{id}/receipt, as tracer.md's Receipt. */
export interface Receipt {
  turn_id: string
  total: Cost
  by_node: Record<string, Cost>
  by_model: Record<string, Cost>
  latency_ms: Record<string, number>
  nodes?: Record<string, { ms: number; count: number; input_tokens: number; output_tokens: number; usd: number }>
  calls?: { seq: number; step: number; provider: string; model: string; input_tokens: number; output_tokens: number; usd: number; ms: number; fallback?: boolean }[]
  scores?: Record<string, Record<string, number>> | null
  feedback?: TurnFeedback | null
}

export type OpsEventType =
  | 'eval_run_started'
  | 'eval_case_finished'
  | 'eval_run_finished'
  | 'feedback_recorded'
  | 'artifact_changed'
  | 'artifact_released'
  // P5: a release's gate finished, and a diagnosis was stored.
  | 'release_gated'
  | 'diagnose_finished'

/** An ops event (events.md section 10): on an eval run's stream, or on GET /v1/events. */
export interface OpsEvent {
  type: OpsEventType
  event_id?: string
  seq?: number
  ts?: string
  trace_id?: string
  tenant_id?: string
  turn_id?: string | null
  session_id?: string | null
  node_id?: string
  status?: string
  summary?: Summary
  /** Added by GET /v1/evals/runs/{id}/events. */
  run_id?: string
  /** Added by GET /v1/events: the cursor over the tenant's ops events, and the stream. */
  cursor?: number
  stream?: string
}

/** A point event on the eval run stream (GET /v1/evals/runs/{id}/events). */
export type EvalStreamEvent = OpsEvent & { type: 'eval_run_started' | 'eval_case_finished' | 'eval_run_finished' }

export interface ApiErrorBody {
  error: { code: string; message: string }
}
