// What a node shows besides its label: a one-line live stat from its latest summary, and for
// the memory node, one badge per store that lights from the expert-child events.
import type { Diagram, Summary } from '@/api/types'
import type { NodeRun, NodeState, TurnState } from '@/state/reducer'

function n(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}
function s(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined
}

export function formatMs(ms: number | undefined): string | undefined {
  if (ms === undefined) return undefined
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`
}

export function formatTokens(t: number): string {
  return t >= 10_000 ? `${(t / 1000).toFixed(1)}k` : String(t)
}

/** One short line, at most about 28 characters, from a node's latest summary. */
export function nodeStat(nodeId: string, run: NodeRun | undefined): string | undefined {
  if (!run || run.state === 'idle') return undefined
  if (run.state === 'waiting') return run.pausedReason === 'approval' ? 'waiting for a human' : 'paused at breakpoint'
  if (run.state === 'error') return run.error?.code ?? 'error'
  const sum: Summary = (run.state === 'active' ? run.enterSummary : run.lastSummary) ?? {}
  const ms = formatMs(run.lastDurationMs)
  const join = (...parts: (string | undefined)[]) => parts.filter(Boolean).join(' · ') || undefined

  if (run.state === 'active') {
    switch (nodeId) {
      case 'model_layer':
        return n(sum.step) !== undefined ? `step ${sum.step}` : 'calling'
      case 'tools':
        return s(sum.tool)
      case 'context_builder':
        return n(sum.budget_tokens) !== undefined ? `budget ${formatTokens(sum.budget_tokens as number)}` : undefined
      case 'loop':
        return n(sum.max_steps) !== undefined ? `max ${sum.max_steps} steps` : undefined
      default:
        return 'working'
    }
  }
  if (run.state === 'skipped') return join('skipped', s(sum.reason))

  switch (nodeId) {
    case 'gateway':
      return sum.phase === 'reply' ? join('reply', n(sum.reply_chars) !== undefined ? `${sum.reply_chars} chars` : undefined) : join(s(sum.phase), ms)
    case 'input_guardrails':
    case 'output_guardrails':
      return join(s(sum.action), ms)
    case 'policy':
      return join(s(sum.decision), s(sum.tool))
    case 'approval':
      return join(s(sum.decision), formatMs(n(sum.waited_ms)))
    case 'audit':
      return join(s(sum.record_id), s(sum.decision))
    case 'context_builder': {
      const used = n(sum.used_tokens)
      const budget = n(sum.budget_tokens)
      if (used !== undefined && budget !== undefined) return `${formatTokens(used)} / ${formatTokens(budget)} tok`
      return ms
    }
    case 'memory': {
      const hits = sum.hits as Record<string, number> | undefined
      if (hits && typeof hits === 'object') {
        const total = Object.values(hits).reduce((a, b) => a + (typeof b === 'number' ? b : 0), 0)
        return join(`${total} hits`, ms)
      }
      return ms
    }
    case 'loop':
      return join(n(sum.steps) !== undefined ? `${sum.steps} steps` : undefined, s(sum.exit_reason))
    case 'model_layer':
      return join(s(sum.model), ms)
    case 'tools':
      return join(s(sum.tool), s(sum.outcome))
    case 'trace':
      return join(n(sum.events) !== undefined ? `${sum.events} events` : undefined, s(sum.store))
    case 'memory_writer':
      return join(n(sum.written) !== undefined ? `${sum.written} written` : undefined, n(sum.pending) ? `${sum.pending} pending` : undefined)
    case 'procedural':
      return join(n(sum.skills_indexed) !== undefined ? `${sum.skills_indexed} skills` : undefined, n(sum.tokens) !== undefined ? `${sum.tokens} tok` : undefined)
    case 'session':
      return join(n(sum.turns) !== undefined ? `${sum.turns} turns` : undefined, n(sum.tokens) !== undefined ? `${sum.tokens} tok` : undefined)
    case 'semantic':
    case 'episodic':
    case 'knowledge':
      return join(n(sum.hits) !== undefined ? `${sum.hits} hits` : undefined, n(sum.tokens) !== undefined ? `${sum.tokens} tok` : undefined)
    case 'router':
      return join(s(sum.rule), s(sum.model))
    case 'providers':
      return join(s(sum.provider), s(sum.model))
    case 'registry':
      return join(s(sum.tool), s(sum.action_class))
    case 'sandbox':
      return join(sum.executed === true ? 'executed' : sum.executed === false ? 'not run' : undefined, ms)
    case 'release':
    case 'artifacts':
      return join(s(sum.artifact), s(sum.version))
    case 'eval':
    case 'online_eval':
    case 'offline_eval':
      return n(sum.cases) !== undefined ? `${sum.passed ?? 0}/${sum.cases} passed` : ms
    default:
      return ms
  }
}

export interface StoreBadge {
  id: string
  label: string
  state: NodeState
}

/**
 * The memory node's store badges: one per expert child of `memory`, in diagram order. A badge
 * takes the state of its store's own events. When the harness did not emit expert children,
 * the badge falls back to memory's exit summary (`stores_queried`).
 */
export function memoryBadges(diagram: Diagram, turn: Pick<TurnState, 'nodes'>, parentId = 'memory'): StoreBadge[] {
  const parentRun = turn.nodes[parentId]
  const queried = parentRun?.lastSummary?.stores_queried
  const queriedSet = new Set(Array.isArray(queried) ? (queried as string[]) : [])
  return diagram.nodes
    .filter((nd) => nd.parent === parentId)
    .map((nd) => {
      const run = turn.nodes[nd.id]
      let state: NodeState = run?.state ?? 'idle'
      if (state === 'idle' && queriedSet.has(nd.id)) state = 'done'
      return { id: nd.id, label: nd.label, state }
    })
}
