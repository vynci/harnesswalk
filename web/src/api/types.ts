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
  }
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
}

export interface StartTurnRequest {
  message: string
  session_id?: string
  step?: boolean
  provider?: string
  fixture?: string
  disable?: string[]
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

export interface ApiErrorBody {
  error: { code: string; message: string }
}
