import { describe, expect, it } from 'vitest'
import type { HarnessEvent, TurnSummary } from '@/api/types'
import { reduceEvents } from './reducer'
import { releaseText, reportFacts, reportLine, sessionTotals, turnReport } from './turnReport'

let seq = 0
function ev(type: HarnessEvent['type'], node_id: string | undefined, summary: Record<string, unknown> = {}, extra: Partial<HarnessEvent> = {}): HarnessEvent {
  return {
    schema_version: '0.1.0', event_id: `e${seq}`, seq: seq++, ts: '2026-10-29T00:00:00Z', trace_id: 't'.repeat(32),
    turn_id: 'turn_1', session_id: 's', tenant_id: 'dev', type, ...(node_id ? { node_id } : {}), status: type === 'node_enter' ? 'active' : 'done', summary, ...extra,
  }
}

const events = (): HarnessEvent[] => {
  seq = 0
  return [
    ev('turn_start', undefined, { release: { release_id: 'r2', working_set: true, artifacts_hash: 'abc' } }),
    ev('node_enter', 'l', { max_steps: 8, budget_tokens: 16000 }),
    ev('node_enter', 'x', { tool: 'lookup_order', call_id: 'c1' }),
    ev('node_exit', 'p', { decision: 'allow', why: 'reads are allowed' }),
    ev('node_exit', 'l', { exit_reason: 'answered', steps: 2 }),
    ev('turn_end', undefined, { exit: 'answered' }, { cost: { input_tokens: 2000, output_tokens: 349, usd: 0.005 }, duration_ms: 1650 }),
  ]
}

const summary = (over: Partial<TurnSummary> = {}): TurnSummary => ({
  turn_id: 'turn_1', session_id: 's', tenant_id: 'dev', status: 'done', exit: 'answered', reply: 'Hi',
  started_at: '2026-10-29T00:00:00.000Z', ended_at: '2026-10-29T00:00:01.650Z', cost: { input_tokens: 2000, output_tokens: 349, usd: 0.005 },
  event_count: 31, paused_at_node: null, pending_approval_id: null, ...over,
})

describe('turnReport', () => {
  it('reads a streamed turn from its events: facts, bars, the policy and the release it ran on', () => {
    const r = turnReport(reduceEvents(events()), summary({ release: { release_id: 'r9', working_set: false, artifacts_hash: 'x' } }))
    expect(r).toMatchObject({ phase: 'done', exit: 'answered', durationMs: 1650, tokens: 2349, usd: 0.005, toolCalls: 1, events: 6, steps: 2, why: 'reads are allowed', fromSummary: false })
    expect(r.release).toEqual({ releaseId: 'r2', workingSet: true, hash: 'abc' })
    expect(r.rows.map((row) => row.label)).toEqual(['Tokens', 'Steps'])
    expect(reportLine(r)).toBe('answered · 1.65 s · 2349 tok · $0.0050')
    // Tokens and steps are bars, so the facts list leaves them out; cost has no budget, so it stays.
    expect(reportFacts(r).map((f) => f.testId)).toEqual(['turn-exit', 'turn-duration', 'turn-tool-calls', 'turn-events', 'turn-cost'])
  })

  it('reads a history turn from its stored summary and omits what the summary lacks', () => {
    const r = turnReport(undefined, summary({ release: { release_id: 'r1', working_set: true, artifacts_hash: 'h' } }))
    expect(r).toMatchObject({ phase: 'done', exit: 'answered', durationMs: 1650, tokens: 2349, usd: 0.005, events: 31, fromSummary: true, rows: [] })
    expect(r.toolCalls).toBeUndefined()
    expect(reportFacts(r).map((f) => f.testId)).toEqual(['turn-exit', 'turn-duration', 'turn-events', 'turn-tokens', 'turn-cost'])
    expect(releaseText(r.release)).toBe('Ran on release r1')
    // No cost, no end: the line keeps what there is.
    const bare = turnReport(undefined, summary({ cost: null, ended_at: null, status: 'running', exit: null }))
    expect(reportLine(bare)).toBe('running')
    expect(reportFacts(bare).map((f) => f.testId)).toEqual(['turn-events'])
  })

  it('a replayed turn passes no summary, so the report stays at the cursor', () => {
    const r = turnReport(reduceEvents(events().slice(0, 3)), null)
    expect(r).toMatchObject({ phase: 'running', toolCalls: 1, fromSummary: false })
    expect(r.exit).toBeUndefined()
    expect(reportLine(r)).toBe('running · 0 tok · $0.0000')
  })

  it('names a working-set turn and none without a release', () => {
    expect(releaseText({ releaseId: null, workingSet: true })).toBe('Ran on the working set')
    expect(releaseText(undefined)).toBeUndefined()
  })
})

describe('sessionTotals', () => {
  it('sums every turn and counts tool calls only where events carry them', () => {
    const t = sessionTotals([turnReport(reduceEvents(events())), turnReport(undefined, summary())])
    expect(t).toEqual({ turns: 2, tokens: 4698, usd: 0.01, events: 37, toolCalls: 1, toolCallTurns: 1 })
  })
})
