// The one reducer that turns a harness event stream into what the UI draws. Live streams and
// replay both feed it, so a replayed turn looks exactly like the live one did at that point.
import type {
  Cost,
  DiagramEdge,
  EventStatus,
  HarnessEvent,
  Summary,
  TurnExit,
} from '@/api/types'

export type NodeState = 'idle' | EventStatus

export interface NodeRun {
  state: NodeState
  /** How many times the node was entered this turn. */
  enters: number
  /** Summary of the latest node_enter (budgets, step number, tool name). */
  enterSummary?: Summary
  /** Summary of the latest node_exit: the source of the one-line live stat. */
  lastSummary?: Summary
  lastDurationMs?: number
  totalDurationMs: number
  startedAt?: string
  endedAt?: string
  pausedReason?: 'breakpoint' | 'approval'
  error?: { code: string; message: string }
  cost?: Cost
}

export interface EdgeRun {
  count: number
  lastSeq: number
  inferred: boolean
}

export interface PendingApproval {
  approval_id: string
  tool: string
  action_class: string
  args_preview?: unknown
  reason?: string
  expires_at?: string
  decision?: 'approve' | 'deny' | 'expired'
  decided_by?: string
}

export interface PauseInfo {
  nodeId: string
  reason: 'breakpoint' | 'approval'
  approvalId?: string
  seq: number
}

export type TurnPhase = 'idle' | 'running' | 'paused' | 'done' | 'error'

export interface TurnState {
  turnId: string | null
  sessionId: string | null
  traceId: string | null
  phase: TurnPhase
  stepMode: boolean
  nodes: Record<string, NodeRun>
  edges: Record<string, EdgeRun>
  /** The edge the request most recently moved along. Animated while the turn runs. */
  activeEdge: { id: string; seq: number } | null
  /** Open node spans, outermost first. */
  stack: string[]
  /** The node that holds control: the innermost open span, or the last one closed. */
  focus: string | null
  events: HarnessEvent[]
  lastSeq: number
  cost: Cost
  costIsTotal: boolean
  reply: string
  deltas: string[]
  paused: PauseInfo | null
  approval: PendingApproval | null
  exit: TurnExit | null
  steps: number | null
  error: { code: string; message: string } | null
  startedAt: string | null
  endedAt: string | null
  durationMs: number | null
}

export const ZERO_COST: Cost = { input_tokens: 0, output_tokens: 0, usd: 0 }

export function initialTurnState(): TurnState {
  return {
    turnId: null,
    sessionId: null,
    traceId: null,
    phase: 'idle',
    stepMode: false,
    nodes: {},
    edges: {},
    activeEdge: null,
    stack: [],
    focus: null,
    events: [],
    lastSeq: -1,
    cost: { ...ZERO_COST },
    costIsTotal: false,
    reply: '',
    deltas: [],
    paused: null,
    approval: null,
    exit: null,
    steps: null,
    error: null,
    startedAt: null,
    endedAt: null,
    durationMs: null,
  }
}

/** Lookup of diagram edges by endpoints, used to infer edges from consecutive nodes. */
export type EdgeIndex = Map<string, string>

export function buildEdgeIndex(edges: DiagramEdge[]): EdgeIndex {
  const index: EdgeIndex = new Map()
  for (const e of edges) {
    const key = `${e.from}>${e.to}`
    if (!index.has(key)) index.set(key, e.id)
  }
  return index
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined
}

function addCost(a: Cost, b: Cost): Cost {
  return {
    input_tokens: a.input_tokens + b.input_tokens,
    output_tokens: a.output_tokens + b.output_tokens,
    usd: a.usd + b.usd,
  }
}

function touchEdge(s: TurnState, edgeId: string, seq: number, inferred: boolean): void {
  const prev = s.edges[edgeId]
  s.edges[edgeId] = { count: (prev?.count ?? 0) + 1, lastSeq: seq, inferred }
  s.activeEdge = { id: edgeId, seq }
}

function nodeRun(s: TurnState, id: string): NodeRun {
  const prev = s.nodes[id]
  const next: NodeRun = prev ? { ...prev } : { state: 'idle', enters: 0, totalDurationMs: 0 }
  s.nodes[id] = next
  return next
}

function closeSpan(s: TurnState, nodeId: string): void {
  // Rule 5: a close ends the most recently entered open span with this id.
  const i = s.stack.lastIndexOf(nodeId)
  if (i >= 0) s.stack.splice(i, 1)
}

/**
 * Apply one event. Pure: returns a new state and never mutates the input.
 * Events at or below the last applied seq are ignored, which makes reconnects idempotent.
 */
export function reduceEvent(prev: TurnState, ev: HarnessEvent, edgeIndex?: EdgeIndex): TurnState {
  if (ev.seq <= prev.lastSeq && prev.turnId === ev.turn_id) return prev
  const s: TurnState = {
    ...prev,
    nodes: { ...prev.nodes },
    edges: { ...prev.edges },
    stack: [...prev.stack],
    events: [...prev.events, ev],
    lastSeq: ev.seq,
  }
  const prevEvent = prev.events[prev.events.length - 1]
  const summary = ev.summary ?? {}

  switch (ev.type) {
    case 'turn_start': {
      s.turnId = ev.turn_id
      s.sessionId = ev.session_id
      s.traceId = ev.trace_id
      s.phase = 'running'
      s.stepMode = summary.step_mode === true
      s.startedAt = ev.ts
      break
    }
    case 'node_enter': {
      const id = ev.node_id!
      // An explicit edge event right before this enter already marked the move. Otherwise infer
      // it from the node that held control (rule 9: edges are implied by consecutive nodes).
      const explicit = prevEvent?.type === 'edge'
      if (!explicit && s.focus && s.focus !== id && edgeIndex) {
        const edgeId = edgeIndex.get(`${s.focus}>${id}`)
        if (edgeId) touchEdge(s, edgeId, ev.seq, true)
      }
      const run = nodeRun(s, id)
      run.state = 'active'
      run.enters += 1
      run.enterSummary = ev.summary
      run.startedAt = ev.ts
      run.pausedReason = undefined
      run.error = undefined
      s.stack.push(id)
      s.focus = id
      break
    }
    case 'node_exit': {
      const id = ev.node_id!
      const run = nodeRun(s, id)
      run.state = ev.status === 'skipped' ? 'skipped' : 'done'
      run.lastSummary = ev.summary
      run.lastDurationMs = ev.duration_ms
      run.totalDurationMs += ev.duration_ms ?? 0
      run.endedAt = ev.ts
      run.pausedReason = undefined
      if (ev.cost) {
        run.cost = run.cost ? addCost(run.cost, ev.cost) : ev.cost
        if (!s.costIsTotal) s.cost = addCost(s.cost, ev.cost)
      }
      closeSpan(s, id)
      const parent = s.stack[s.stack.length - 1] ?? null
      // Control returns to the enclosing node; light the return edge if the diagram has one.
      if (parent && edgeIndex) {
        const edgeId = edgeIndex.get(`${id}>${parent}`)
        if (edgeId) touchEdge(s, edgeId, ev.seq, true)
      }
      s.focus = parent ?? id
      break
    }
    case 'node_paused': {
      const id = ev.node_id!
      const run = nodeRun(s, id)
      const reason = summary.reason === 'approval' ? 'approval' : 'breakpoint'
      run.state = 'waiting'
      run.pausedReason = reason
      s.paused = { nodeId: id, reason, approvalId: str(summary.approval_id), seq: ev.seq }
      s.phase = 'paused'
      break
    }
    case 'node_resumed': {
      const id = ev.node_id!
      const run = nodeRun(s, id)
      run.state = 'active'
      run.pausedReason = undefined
      if (s.paused?.nodeId === id) s.paused = null
      if (s.phase === 'paused') s.phase = 'running'
      break
    }
    case 'edge': {
      const id = ev.edge_id
      if (!id) break
      const run = s.edges[id]
      // The return edge was already inferred from the node_exit just before: confirm it.
      if (run?.inferred && s.activeEdge?.id === id && run.lastSeq === ev.seq - 1) {
        s.edges[id] = { ...run, lastSeq: ev.seq, inferred: false }
        s.activeEdge = { id, seq: ev.seq }
      } else {
        touchEdge(s, id, ev.seq, false)
      }
      break
    }
    case 'approval_requested': {
      s.approval = {
        approval_id: str(summary.approval_id) ?? '',
        tool: str(summary.tool) ?? '',
        action_class: str(summary.action_class) ?? '',
        args_preview: summary.args_preview,
        reason: str(summary.reason),
        expires_at: str(summary.expires_at),
      }
      const run = nodeRun(s, 'approval')
      run.state = 'waiting'
      break
    }
    case 'approval_decided': {
      const decision = str(summary.decision) as PendingApproval['decision']
      if (s.approval && s.approval.approval_id === str(summary.approval_id)) {
        s.approval = { ...s.approval, decision, decided_by: str(summary.decided_by) }
      } else {
        s.approval = {
          approval_id: str(summary.approval_id) ?? '',
          tool: '',
          action_class: '',
          decision,
          decided_by: str(summary.decided_by),
        }
      }
      break
    }
    case 'message_delta': {
      const index = typeof summary.index === 'number' ? summary.index : s.deltas.length
      const deltas = [...s.deltas]
      deltas[index] = str(summary.delta) ?? ''
      s.deltas = deltas
      s.reply = deltas.join('')
      break
    }
    case 'memory_proposed':
    case 'memory_written':
    case 'artifact_released':
      // Point events: kept in the timeline; the inspector's Events tab shows them.
      break
    case 'error': {
      s.error = ev.error ?? { code: 'internal', message: 'unknown error' }
      if (ev.node_id) {
        const run = nodeRun(s, ev.node_id)
        run.state = 'error'
        run.error = ev.error
        run.lastDurationMs = ev.duration_ms
        run.endedAt = ev.ts
        run.pausedReason = undefined
        closeSpan(s, ev.node_id)
        s.focus = s.stack[s.stack.length - 1] ?? ev.node_id
        if (s.paused?.nodeId === ev.node_id) s.paused = null
      }
      break
    }
    case 'turn_end': {
      s.phase = ev.status === 'error' ? 'error' : 'done'
      s.exit = (str(summary.exit) as TurnExit | undefined) ?? (ev.status === 'error' ? 'error' : null)
      s.steps = typeof summary.steps === 'number' ? summary.steps : null
      const reply = str(summary.reply)
      if (reply !== undefined) s.reply = reply
      if (ev.cost) {
        s.cost = ev.cost
        s.costIsTotal = true
      }
      s.paused = null
      s.endedAt = ev.ts
      s.durationMs = ev.duration_ms ?? null
      s.activeEdge = null
      // Anything still open is closed by the end of the turn.
      for (const id of s.stack) {
        const run = nodeRun(s, id)
        if (run.state === 'active' || run.state === 'waiting') run.state = 'done'
      }
      s.stack = []
      break
    }
  }
  return s
}

export function reduceEvents(
  events: HarnessEvent[],
  edgeIndex?: EdgeIndex,
  start: TurnState = initialTurnState(),
): TurnState {
  let s = start
  for (const ev of events) s = reduceEvent(s, ev, edgeIndex)
  return s
}

/** Events that belong to one node, for the inspector's Events tab. */
export function eventsForNode(events: HarnessEvent[], nodeId: string): HarnessEvent[] {
  return events.filter((e) => e.node_id === nodeId)
}
