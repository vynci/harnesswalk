// One turn's report (the chat timeline's rows and the status bar's popover) and the session's
// totals (the session chip). A turn this browser streamed or loaded events for reports from its
// reduced state; a turn read back from the session history has only its stored summary, so its
// report holds what the summary has (exit, duration, tokens, cost, events, release) and omits
// the rest (tool calls, steps, the budget bars and the policy's sentence).
import type { TurnSummary } from '@/api/types'
import { budgetRows, type BudgetRow } from '@/diagram/budget'
import { formatMs } from '@/diagram/stats'
import type { TurnPhase, TurnState } from './reducer'
import { budgetFacts } from './turnFacts'

/** The release a turn ran on (`turn_start.summary.release`, or the stored summary's). */
export interface TurnRelease {
  releaseId: string | null
  workingSet: boolean
  hash?: string
}

export interface TurnReport {
  phase: TurnPhase
  exit?: string
  durationMs?: number
  tokens?: number
  usd?: number
  toolCalls?: number
  events?: number
  steps?: number
  /** Budgets the loop had (tokens, cost, steps, a policy budget), as bars. */
  rows: BudgetRow[]
  why?: string
  release?: TurnRelease
  /** Read from the stored summary only (no events here). */
  fromSummary: boolean
}

function releaseOf(raw: unknown): TurnRelease | undefined {
  if (!raw || typeof raw !== 'object') return undefined
  const r = raw as Record<string, unknown>
  const releaseId = typeof r.release_id === 'string' && r.release_id ? r.release_id : null
  const workingSet = r.working_set === true
  if (!releaseId && !workingSet) return undefined
  return { releaseId, workingSet, hash: typeof r.artifacts_hash === 'string' && r.artifacts_hash ? r.artifacts_hash : undefined }
}

const summaryPhase = (s: TurnSummary): TurnPhase => (s.status === 'done' ? (s.exit === 'error' ? 'error' : 'done') : s.status === 'paused' ? 'paused' : 'running')

/**
 * The report of a turn. `state` wins when it has events; `summary` fills the release and stands
 * in for the whole report when there are no events. Pass no summary for a replayed turn, so the
 * report stays at the replay's cursor.
 */
export function turnReport(state: TurnState | undefined, summary?: TurnSummary | null): TurnReport {
  if (state && state.events.length > 0) {
    const budget = budgetFacts(state.events)
    const start = state.events.find((e) => e.type === 'turn_start')
    return {
      phase: state.phase,
      exit: state.exit ?? undefined,
      durationMs: state.durationMs ?? undefined,
      tokens: state.cost.input_tokens + state.cost.output_tokens,
      usd: state.cost.usd,
      toolCalls: budget.toolCalls,
      events: state.events.length,
      steps: budget.steps,
      rows: budgetRows(state, budget),
      why: budget.why,
      release: releaseOf(start?.summary?.release) ?? releaseOf(summary?.release),
      fromSummary: false,
    }
  }
  if (!summary) return { phase: state?.phase ?? 'idle', rows: [], fromSummary: false }
  const started = Date.parse(summary.started_at)
  const ended = summary.ended_at ? Date.parse(summary.ended_at) : NaN
  return {
    phase: summaryPhase(summary),
    exit: summary.exit ?? undefined,
    durationMs: Number.isFinite(started) && Number.isFinite(ended) && ended >= started ? ended - started : undefined,
    tokens: summary.cost ? summary.cost.input_tokens + summary.cost.output_tokens : undefined,
    usd: summary.cost?.usd,
    events: typeof summary.event_count === 'number' ? summary.event_count : undefined,
    rows: [],
    release: releaseOf(summary.release),
    fromSummary: true,
  }
}

const PHASE_WORD: Record<TurnPhase, string> = { idle: 'not started', running: 'running', paused: 'paused', done: 'done', error: 'error' }

/** The row's one line: "answered · 1.65 s · 2349 tok · $0.0050", with what the report has. */
export function reportLine(r: TurnReport): string {
  return [
    r.exit ?? PHASE_WORD[r.phase],
    formatMs(r.durationMs),
    r.tokens !== undefined ? `${r.tokens} tok` : undefined,
    r.usd !== undefined ? `$${r.usd.toFixed(4)}` : undefined,
  ]
    .filter(Boolean)
    .join(' · ')
}

/** One fact of the turn in the report card's list (exit, duration, tool calls, events...). */
export interface TurnFact {
  label: string
  value: string
  testId: string
  title?: string
}

/** The report card's facts. Tokens, cost and steps show as bars when the loop had a budget for them. */
export function reportFacts(r: TurnReport): TurnFact[] {
  const facts: TurnFact[] = []
  const bar = (label: string) => r.rows.some((row) => row.label === label)
  if (r.exit) facts.push({ label: 'Exit', value: r.exit, testId: 'turn-exit' })
  const duration = formatMs(r.durationMs)
  if (duration) facts.push({ label: 'Duration', value: duration, testId: 'turn-duration' })
  if (r.toolCalls !== undefined) facts.push({ label: 'Tool calls', value: String(r.toolCalls), testId: 'turn-tool-calls' })
  if (r.events !== undefined) facts.push({ label: 'Events', value: String(r.events), testId: 'turn-events' })
  if (r.tokens !== undefined && !bar('Tokens')) facts.push({ label: 'Tokens', value: String(r.tokens), testId: 'turn-tokens', title: 'Input plus output tokens' })
  if (r.usd !== undefined && !bar('Cost')) facts.push({ label: 'Cost', value: `$${r.usd.toFixed(4)}`, testId: 'turn-cost' })
  if (r.steps !== undefined && !bar('Steps')) facts.push({ label: 'Steps', value: String(r.steps), testId: 'turn-steps' })
  return facts
}

/** "Ran on release r2", "Ran on the working set", or nothing. */
export function releaseText(release: TurnRelease | undefined): string | undefined {
  if (!release) return undefined
  return release.releaseId ? `Ran on release ${release.releaseId}` : 'Ran on the working set'
}

export interface SessionTotals {
  turns: number
  tokens: number
  usd: number
  events: number
  toolCalls: number
  /** The turns whose tool calls are counted: only turns with events carry them. */
  toolCallTurns: number
}

/** The session's turns summed: tokens, cost and events from each report, tool calls where known. */
export function sessionTotals(reports: TurnReport[]): SessionTotals {
  const t: SessionTotals = { turns: reports.length, tokens: 0, usd: 0, events: 0, toolCalls: 0, toolCallTurns: 0 }
  for (const r of reports) {
    t.tokens += r.tokens ?? 0
    t.usd += r.usd ?? 0
    t.events += r.events ?? 0
    if (r.toolCalls !== undefined) {
      t.toolCalls += r.toolCalls
      t.toolCallTurns += 1
    }
  }
  return t
}
