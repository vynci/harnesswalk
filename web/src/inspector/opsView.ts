// What the LLM Ops panels say (an eval run, a receipt, a YAML problem), as plain functions so
// they are tested without a DOM.
import { parse as parseYaml } from 'yaml'
import type { Cost, EvalCase, EvalCaseStatus, EvalRunSummary, TraceTurn } from '@/api/types'
import { formatMs, formatTokens } from '@/diagram/stats'

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

/**
 * The Test Results headline: "Ran 12 cases, 3 failed" (or "Running 4 of 12 cases"). The API's
 * `totals.failed` counts failed and error cases together; `errors` says how many of them errored,
 * so the headline reads "3 failed (1 error)".
 */
export function runHeadline(run: Pick<EvalRunSummary, 'status' | 'totals'>, done?: number, errors = 0): string {
  const t = run.totals
  if (run.status === 'running') return `Running ${done ?? t.passed + t.failed + t.skipped} of ${plural(t.cases, 'case')}`
  if (run.status === 'error') return `Run stopped after ${plural(t.passed + t.failed + t.skipped, 'case')}`
  const failed = t.failed ? `${t.failed} failed${errors ? ` (${plural(errors, 'error')})` : ''}` : t.skipped ? 'none failed' : 'all passed'
  return `Ran ${plural(t.cases, 'case')}, ${failed}`
}

/** What a regression is, for the badge's hint and the run detail. */
export function regressionText(n: number, previousRunId: string | null | undefined): string {
  const what = n === 1 ? 'a case that failed or errored in this run' : 'cases that failed or errored in this run'
  return `${plural(n, 'regression')}: ${what} and passed in ${previousRunId ?? 'the previous run'}`
}

/** "golden · fake-small", the short name of a run. */
export function runName(run: Pick<EvalRunSummary, 'suite' | 'model' | 'pack'>): string {
  return [run.suite === 'golden' ? 'Golden' : 'Judge', run.pack, run.model].filter(Boolean).join(' · ')
}

export interface ScorerSuite {
  scorer: string
  passed: number
  failed: number
  cases: { c: EvalCase; pass: boolean | null; detail?: string | null; value: unknown }[]
}

/** The cases grouped by scorer, failing suites first, then by name. */
export function byScorer(cases: EvalCase[]): ScorerSuite[] {
  const suites = new Map<string, ScorerSuite>()
  for (const c of cases) {
    for (const [scorer, s] of Object.entries(c.scores ?? {})) {
      const suite = suites.get(scorer) ?? { scorer, passed: 0, failed: 0, cases: [] }
      if (s.pass === true) suite.passed++
      else if (s.pass === false) suite.failed++
      suite.cases.push({ c, pass: s.pass, detail: s.detail, value: s.value })
      suites.set(scorer, suite)
    }
  }
  return [...suites.values()].sort((a, b) => Number(b.failed > 0) - Number(a.failed > 0) || a.scorer.localeCompare(b.scorer))
}

const ORDER: Record<EvalCaseStatus, number> = { error: 0, failed: 1, passed: 2, skipped: 3 }

/** Regressions first, then errors and failures, then the rest in run order. */
export function sortCases(cases: EvalCase[]): EvalCase[] {
  return cases
    .map((c, i) => ({ c, i }))
    .sort((a, b) => Number(b.c.regression) - Number(a.c.regression) || ORDER[a.c.status] - ORDER[b.c.status] || a.i - b.i)
    .map(({ c }) => c)
}

/** A scorer value as text: arrays joined, booleans as yes or no. */
export function showValue(v: unknown): string {
  if (v === null || v === undefined) return '·'
  if (Array.isArray(v)) return v.length ? v.map(showValue).join(v.every((x) => typeof x === 'string') ? ' → ' : ', ') : 'none'
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2)
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

/** A YAML parse error to show under the editor, or null. */
export function yamlProblem(text: string): string | null {
  try {
    parseYaml(text)
    return null
  } catch (err) {
    return err instanceof Error ? (err.message.split('\n')[0] ?? err.message) : String(err)
  }
}

export const tokens = (c: Cost | null | undefined) => (c ? c.input_tokens + c.output_tokens : 0)
export const usd = (c: Cost | null | undefined) => `$${(c?.usd ?? 0).toFixed(4)}`

/** The receipt line of a turn: tokens, cost, steps and time. */
export function receiptLine(t: TraceTurn): string {
  return [
    `${formatTokens(tokens(t.cost))} tok`,
    usd(t.cost),
    typeof t.steps === 'number' ? `${t.steps} step${t.steps === 1 ? '' : 's'}` : '',
    formatMs(t.duration_ms ?? undefined) ?? '',
  ]
    .filter(Boolean)
    .join(' · ')
}
