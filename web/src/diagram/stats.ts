// What a node shows besides its label: a one-line live stat from its latest summary, and for
// the badge node (layout.ts BADGE_NODE, memory), one badge per store that lights from the
// expert-child events.
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

type Rule = (sum: Summary, ms: string | undefined) => string | undefined

const join = (...parts: (string | undefined)[]) => parts.filter(Boolean).join(' · ') || undefined

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}

// The stat is read from the summary keys the event contract defines (spec/events.md), never
// from node ids, so a node added to diagram.json gets a sensible stat without a UI change.
// Rules are tried in order; the first that recognises the summary wins.
const ACTIVE_RULES: Rule[] = [
  (x) => (n(x.step) !== undefined ? `step ${x.step}` : undefined),
  (x) => s(x.tool),
  (x) => (n(x.max_steps) !== undefined ? `max ${x.max_steps} steps` : undefined),
  (x) => (n(x.budget_tokens) !== undefined ? `budget ${formatTokens(x.budget_tokens as number)}` : undefined),
]

const DONE_RULES: Rule[] = [
  // The gateway: which phase, and how long the reply was.
  (x, ms) => (x.phase === 'reply' ? join('reply', n(x.reply_chars) !== undefined ? `${x.reply_chars} chars` : undefined) : s(x.phase) ? join(s(x.phase), ms) : undefined),
  // A budget and what was used of it (the context builder).
  (x) => {
    const used = n(x.used_tokens)
    const budget = n(x.budget_tokens)
    return used !== undefined && budget !== undefined ? `${formatTokens(used)} / ${formatTokens(budget)} tok` : undefined
  },
  // Hits per store (memory).
  (x, ms) => {
    if (!isRecord(x.hits)) return undefined
    const total = Object.values(x.hits).reduce<number>((a, b) => a + (typeof b === 'number' ? b : 0), 0)
    return join(`${total} hits`, ms)
  },
  // Steps and why the loop stopped.
  (x) => (n(x.steps) !== undefined ? join(`${x.steps} steps`, s(x.exit_reason)) : undefined),
  // An audit record.
  (x) => (s(x.record_id) ? join(s(x.record_id), s(x.decision)) : undefined),
  // A human decision and how long it waited.
  (x) => (s(x.decision) && n(x.waited_ms) !== undefined ? join(s(x.decision), formatMs(n(x.waited_ms))) : undefined),
  // A policy decision on a spent budget: the budget's use.
  (x) => {
    const b = isRecord(x.budget) ? x.budget : undefined
    return s(x.decision) && b && n(b.used) !== undefined && n(b.limit) !== undefined
      ? join(s(x.decision), `budget ${b.used}/${b.limit}`)
      : undefined
  },
  // A policy decision on a tool.
  (x) => (s(x.decision) ? join(s(x.decision), s(x.tool)) : undefined),
  // A guardrail verdict.
  (x, ms) => (s(x.action) ? join(s(x.action), ms) : undefined),
  // A tool call's outcome.
  (x) => (s(x.outcome) ? join(s(x.tool), s(x.outcome)) : undefined),
  // A registry lookup.
  (x) => (s(x.tool) && s(x.action_class) ? join(s(x.tool), s(x.action_class)) : undefined),
  // A sandbox run.
  (x, ms) => (typeof x.executed === 'boolean' ? join(x.executed ? 'executed' : 'not run', ms) : undefined),
  // A model call.
  (x, ms) => (s(x.model) && (n(x.step) !== undefined || s(x.finish_reason)) ? join(s(x.model), ms) : undefined),
  // A routing decision; 0.2.0: a call the canary split names its arm instead of the model.
  (x) => (s(x.rule) && s(x.model) ? join(s(x.rule), s(x.arm) ? `${s(x.arm)} arm` : s(x.model)) : undefined),
  // A provider attempt.
  (x) => (s(x.provider) ? join(s(x.provider), s(x.model)) : undefined),
  // The trace store.
  (x) => (n(x.events) !== undefined ? join(`${x.events} events`, s(x.store)) : undefined),
  // A cost receipt.
  (x) => {
    const tokens = (n(x.input_tokens) ?? 0) + (n(x.output_tokens) ?? 0)
    return n(x.usd) !== undefined ? join(`${formatTokens(tokens)} tok`, `$${(x.usd as number).toFixed(4)}`) : undefined
  },
  // Memory writes.
  (x) => (n(x.written) !== undefined || n(x.pending) ? join(n(x.written) !== undefined ? `${x.written} written` : undefined, n(x.pending) ? `${x.pending} pending` : undefined) : undefined),
  // Stores: skills, session turns, hits, and the tokens they gave the context.
  (x) => (n(x.skills_indexed) !== undefined ? join(`${x.skills_indexed} skills`, n(x.tokens) !== undefined ? `${x.tokens} tok` : undefined) : undefined),
  (x) => (n(x.turns) !== undefined ? join(`${x.turns} turns`, n(x.tokens) !== undefined ? `${x.tokens} tok` : undefined) : undefined),
  (x) => (n(x.hits) !== undefined ? join(`${x.hits} hits`, n(x.tokens) !== undefined ? `${x.tokens} tok` : undefined) : undefined),
  // Releases and evals.
  (x) => (s(x.artifact) ? join(s(x.artifact), s(x.version)) : undefined),
  (x) => (n(x.cases) !== undefined ? `${n(x.passed) ?? 0}/${x.cases} passed` : undefined),
]

function firstMatch(rules: Rule[], sum: Summary, ms: string | undefined): string | undefined {
  for (const rule of rules) {
    const out = rule(sum, ms)
    if (out) return out
  }
  return undefined
}

/** Point events so far in an open span, by the word after the underscore: "1 proposed · 1 written". */
export function pointStat(run: NodeRun | undefined): string | undefined {
  if (!run?.points) return undefined
  return join(...Object.entries(run.points).map(([type, count]) => `${count} ${type.slice(type.indexOf('_') + 1)}`))
}

/** The plain-words reason a decision carries (`why`), for a tooltip on the node's stat. */
export function nodeWhy(run: NodeRun | undefined): string | undefined {
  return run?.state === 'done' || run?.state === 'skipped' ? s(run.lastSummary?.why) : undefined
}

/** One short line, at most about 28 characters, from a node's latest summary. */
export function nodeStat(run: NodeRun | undefined): string | undefined {
  if (!run || run.state === 'idle') return undefined
  if (run.state === 'waiting') return run.pausedReason === 'approval' ? 'waiting for a human' : 'paused at breakpoint'
  if (run.state === 'error') return run.error?.code ?? 'error'
  const ms = formatMs(run.lastDurationMs)
  if (run.state === 'active') return pointStat(run) ?? firstMatch(ACTIVE_RULES, run.enterSummary ?? {}, undefined) ?? 'working'
  const sum: Summary = run.lastSummary ?? {}
  if (run.state === 'skipped') return join('skipped', s(sum.reason))
  return firstMatch(DONE_RULES, sum, ms) ?? ms
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
export function memoryBadges(diagram: Diagram, turn: Pick<TurnState, 'nodes'>, parentId: string): StoreBadge[] {
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
