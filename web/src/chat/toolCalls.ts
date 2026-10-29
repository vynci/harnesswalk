// The tool calls of a turn, read from its events for the chat's inline Tool lines. Nothing here
// names a node: a call is the span whose node_enter carries `tool` and `call_id`, its policy
// decision any event about the same tool with a `decision` and a `rule`, its approval the
// approval_* events, and it ends with the node_exit of the span it opened.
import type { HarnessEvent } from '@/api/types'

export type ToolCallStatus = 'running' | 'waiting' | 'done' | 'denied' | 'error'

export interface ToolCallView {
  callId: string
  tool: string
  /** The node whose span is the call. */
  nodeId: string
  status: ToolCallStatus
  actionClass?: string
  decision?: string
  rule?: string
  /** Arguments as the harness shows them (only approvals carry a preview so far). */
  args?: unknown
  approval?: { state: 'requested' | 'approve' | 'deny' | 'expired'; reason?: string; decidedBy?: string }
  outcome?: string
  executed?: boolean
  durationMs?: number
  error?: { code: string; message: string }
}

function str(v: unknown): string | undefined {
  return typeof v === 'string' ? v : undefined
}

export function toolCalls(events: HarnessEvent[]): ToolCallView[] {
  const calls: ToolCallView[] = []
  let open: ToolCallView | null = null
  for (const ev of events) {
    const s = ev.summary ?? {}
    const tool = str(s.tool)
    const callId = str(s.call_id)
    if (ev.type === 'node_enter' && ev.node_id && tool && callId) {
      open = { callId, tool, nodeId: ev.node_id, status: 'running' }
      calls.push(open)
      continue
    }
    if (!open) continue
    if (ev.type === 'node_exit' && ev.node_id === open.nodeId) {
      const outcome = str(s.outcome)
      open.outcome = outcome
      open.executed = typeof s.executed === 'boolean' ? s.executed : undefined
      open.durationMs = ev.duration_ms
      open.error = ev.error
      open.status = ev.status === 'error' ? (outcome === 'denied' ? 'denied' : 'error') : outcome === 'denied' ? 'denied' : outcome && outcome !== 'ok' ? 'error' : 'done'
      open = null
      continue
    }
    if (tool && tool !== open.tool) continue
    if (str(s.action_class)) open.actionClass = str(s.action_class)
    if (str(s.decision) && str(s.rule)) {
      open.decision = str(s.decision)
      open.rule = str(s.rule)
    }
    if (ev.type === 'approval_requested') {
      open.status = 'waiting'
      open.args = s.args_preview
      open.approval = { state: 'requested', reason: str(s.reason) }
    }
    if (ev.type === 'approval_decided') {
      const d = str(s.decision)
      open.approval = { ...open.approval, state: d === 'approve' || d === 'deny' || d === 'expired' ? d : 'requested', decidedBy: str(s.decided_by) }
      if (open.status === 'waiting') open.status = 'running'
    }
  }
  return calls
}
