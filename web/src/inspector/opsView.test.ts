import { describe, expect, it } from 'vitest'
import type { EvalCase } from '@/api/types'
import { byScorer, receiptLine, regressionText, runHeadline, runName, showValue, sortCases, yamlProblem } from './opsView'

const kase = (id: string, status: EvalCase['status'], scores: EvalCase['scores'], regression = false): EvalCase => ({
  case_id: id,
  title: id,
  status,
  scores,
  expected: null,
  actual: null,
  turn_id: null,
  previous_status: null,
  regression,
})

describe('eval panel text', () => {
  it('heads a run like the Test Results pattern', () => {
    expect(runHeadline({ status: 'done', totals: { cases: 12, passed: 6, failed: 3, skipped: 3 } })).toBe('Ran 12 cases, 3 failed')
    expect(runHeadline({ status: 'done', totals: { cases: 1, passed: 1, failed: 0, skipped: 0 } })).toBe('Ran 1 case, all passed')
    expect(runHeadline({ status: 'done', totals: { cases: 12, passed: 9, failed: 0, skipped: 3 } })).toBe('Ran 12 cases, none failed')
    expect(runHeadline({ status: 'running', totals: { cases: 12, passed: 0, failed: 0, skipped: 0 } }, 4)).toBe('Running 4 of 12 cases')
    expect(runHeadline({ status: 'done', totals: { cases: 16, passed: 15, failed: 1, skipped: 0 } }, undefined, 1)).toBe('Ran 16 cases, 1 failed (1 error)')
    expect(regressionText(1, 'run_0001')).toBe('1 regression: a case that failed or errored in this run and passed in run_0001')
    expect(runName({ suite: 'golden', pack: '_starter', model: null })).toBe('Golden · _starter')
  })

  it('groups by scorer with failing suites first and sorts regressions to the top', () => {
    const cases = [
      kase('a', 'passed', { path: { value: 1, pass: true }, answer_match: { value: true, pass: true } }),
      kase('b', 'failed', { tool_calls: { value: 'none', pass: false }, path: { value: 1, pass: true } }, true),
      kase('c', 'skipped', {}),
      kase('d', 'failed', { tool_calls: { value: 'x', pass: false } }),
    ]
    expect(byScorer(cases).map((s) => [s.scorer, s.passed, s.failed])).toEqual([
      ['tool_calls', 0, 2],
      ['answer_match', 1, 0],
      ['path', 2, 0],
    ])
    expect(sortCases(cases).map((c) => c.case_id)).toEqual(['b', 'd', 'a', 'c'])
  })

  it('shows scorer values as text', () => {
    expect(showValue(['gateway', 'loop'])).toBe('gateway → loop')
    expect(showValue([])).toBe('none')
    expect(showValue(true)).toBe('yes')
    expect(showValue(0.912)).toBe('0.91')
    expect(showValue(null)).toBe('·')
  })
})

describe('receipts and YAML', () => {
  it('sums a turn receipt into one line', () => {
    expect(
      receiptLine({ turn_id: 't', session_id: 's', started_at: '', status: 'done', cost: { input_tokens: 1917, output_tokens: 45, usd: 0.0012 }, steps: 2, duration_ms: 427 }),
    ).toBe('1962 tok · $0.0012 · 2 steps · 427 ms')
    expect(receiptLine({ turn_id: 't', session_id: 's', started_at: '', status: 'running' })).toBe('0 tok · $0.0000')
  })

  it('names a YAML problem before Save', () => {
    expect(yamlProblem('rules:\n  - { id: a }\n')).toBeNull()
    expect(yamlProblem('rules: [unclosed')).toMatch(/.+/)
  })
})
