import { describe, expect, it } from 'vitest'
import type { HarnessEvent } from '@/api/types'
import { pointStat } from '@/diagram/stats'
import { toolCalls } from '@/chat/toolCalls'
import { reduceEvents } from './reducer'
import {
  budgetFacts,
  citationsOf,
  inputGuardFacts,
  limitFacts,
  memoryPoints,
  outputGuardFacts,
  placeholderValues,
  splitPlaceholders,
  withPlaceholders,
} from './turnFacts'

let seq = 0
function ev(type: HarnessEvent['type'], node_id: string | undefined, summary: Record<string, unknown> = {}, extra: Partial<HarnessEvent> = {}): HarnessEvent {
  const status = type === 'node_enter' || type === 'node_resumed' || type === 'message_delta' ? 'active' : type === 'memory_proposed' ? 'waiting' : 'done'
  return {
    schema_version: '0.1.0', event_id: `e${seq}`, seq: seq++, ts: '2026-10-29T00:00:00Z', trace_id: 't'.repeat(32),
    turn_id: 'turn_1', session_id: 's', tenant_id: 'dev', type, ...(node_id ? { node_id } : {}), status, summary, ...extra,
  }
}

// Node ids below are arbitrary on purpose: the facts are found by summary keys alone.
const turn = (): HarnessEvent[] => {
  seq = 0
  return [
    ev('turn_start', undefined, { step_mode: false }),
    ev('node_enter', 'a'),
    ev('node_exit', 'a', { action: 'redact', redactions: { email: 1, phone: 0 }, injection_score: 0.35, injection_signals: { override: 1 }, warnings: ['injection'] }),
    ev('node_enter', 'l', { max_steps: 8, budget_tokens: 16000, budget_usd: 0.05 }),
    ev('node_enter', 'x', { tool: 'lookup_order', call_id: 'c1' }),
    ev('node_enter', 'p'),
    ev('node_exit', 'p', { tool: 'lookup_order', action_class: 'read', decision: 'allow', rule: 'dev.read.allow', why: 'reads are allowed' }),
    ev('node_enter', 'sb'),
    ev('node_exit', 'sb', { tool: 'lookup_order', call_id: 'c1', executed: true, level: 'subprocess', network: 'blocked', jail: 'sandbox-exec' }),
    ev('node_exit', 'x', { tool: 'lookup_order', call_id: 'c1', outcome: 'ok', executed: true }),
    ev('node_enter', 'x', { tool: 'lookup_order', call_id: 'c2' }),
    ev('node_enter', 'p'),
    ev('node_exit', 'p', { tool: 'lookup_order', decision: 'deny', rule: 'budget.tool_calls_per_turn', why: 'budget is 1 per turn', budget: { name: 'tool_calls_per_turn', limit: 1, used: 1 } }),
    ev('node_exit', 'x', { tool: 'lookup_order', call_id: 'c2', outcome: 'denied', executed: false }),
    ev('node_exit', 'l', { steps: 1, exit_reason: 'budget_tool_calls', tool_calls: 2 }),
    ev('node_enter', 'o'),
    ev('node_exit', 'o', { action: 'pass', restored: 1, citations: { required: true, present: 2, invalid: 0, uncited_claims: 0 } }),
    ev('node_enter', 'w'),
    ev('memory_proposed', 'w', { proposal_id: 'p1', store: 'semantic', gate: 'auto', preview: 'name: Sam', source: 'loop' }),
    ev('memory_written', 'w', { proposal_id: 'p1', store: 'semantic', record_id: 'f1', version: 1, op: 'create' }),
    ev('node_exit', 'w', { proposed: 1, written: 1 }),
    ev('turn_end', undefined, { exit: 'limit', citations: [{ n: 1, doc: 'returns_policy', chunk: 2, citation: 'Returns policy, Labels' }, { bad: true }] }, { cost: { input_tokens: 1, output_tokens: 1, usd: 0 } }),
  ]
}

describe('turn facts read from summary keys', () => {
  it('input guardrails: redactions, injection score, warnings', () => {
    expect(inputGuardFacts(turn())).toEqual({
      action: 'redact', redactions: { email: 1 }, injectionScore: 0.35, injectionSignals: { override: 1 }, warnings: ['injection'], classifierScore: undefined,
    })
  })

  it('output guardrails: restored and the citation check', () => {
    expect(outputGuardFacts(turn())).toMatchObject({ action: 'pass', restored: 1, citations: { required: true, present: 2 } })
  })

  it('a limit names the loop exit reason and the budget decision behind it', () => {
    expect(limitFacts(turn())).toMatchObject({ reason: 'budget_tool_calls', why: 'budget is 1 per turn', rule: 'budget.tool_calls_per_turn', budget: { name: 'tool_calls_per_turn', limit: 1, used: 1 } })
  })

  it('budgets in force and used: loop enter summary, tool calls, the spent budget', () => {
    expect(budgetFacts(turn())).toMatchObject({ maxSteps: 8, budgetTokens: 16000, budgetUsd: 0.05, steps: 1, toolCalls: 2, budget: { used: 1, limit: 1 }, why: 'budget is 1 per turn' })
  })

  it('citations come from turn_end and malformed entries are dropped', () => {
    expect(citationsOf(turn())).toEqual([{ n: 1, doc: 'returns_policy', chunk: 2, citation: 'Returns policy, Labels' }])
    expect(citationsOf(turn().slice(0, -1))).toEqual([])
  })

  it('memory point events, and their count on the node that emitted them', () => {
    const points = memoryPoints(turn())
    expect(points.map((p) => [p.type, p.store, p.proposalId])).toEqual([['memory_proposed', 'semantic', 'p1'], ['memory_written', 'semantic', 'p1']])
    const events = turn()
    const open = reduceEvents(events.slice(0, events.findIndex((e) => e.type === 'memory_written') + 1))
    expect(open.nodes.w!.points).toEqual({ memory_proposed: 1, memory_written: 1 })
    expect(open.nodes.w!.state).toBe('active')
    expect(pointStat(open.nodes.w)).toBe('1 proposed · 1 written')
  })

  it('tool lines carry the sandbox level, network and jail, and the policy why and budget', () => {
    const calls = toolCalls(turn())
    expect(calls[0]).toMatchObject({ sandbox: { level: 'subprocess', network: 'blocked', jail: 'sandbox-exec' }, why: 'reads are allowed' })
    expect(calls[1]).toMatchObject({ status: 'denied', budget: { name: 'tool_calls_per_turn', used: 1, limit: 1 } })
    expect(calls[1]!.sandbox).toBeUndefined()
  })

  it('a turn without these facts has none', () => {
    seq = 0
    const bare = [ev('turn_start', undefined), ev('turn_end', undefined, { exit: 'answered' })]
    expect(inputGuardFacts(bare)).toBeNull()
    expect(outputGuardFacts(bare)).toBeNull()
    expect(budgetFacts(bare)).toMatchObject({ toolCalls: 0, budget: undefined })
  })
})

describe('placeholders', () => {
  const typed = 'Please email the receipt for order A-1001 to ana.lopez@example.com.'
  const seen = 'Please email the receipt for order A-1001 to [EMAIL_1].'

  it('lines the redacted message up with what the user typed', () => {
    expect(placeholderValues(typed, seen)).toEqual({ '[EMAIL_1]': 'ana.lopez@example.com' })
    expect(placeholderValues('call 416 555 0199 or a@b.co', 'call [PHONE_1] or [EMAIL_1]')).toEqual({ '[PHONE_1]': '416 555 0199', '[EMAIL_1]': 'a@b.co' })
  })

  it('gives up when the two do not line up', () => {
    expect(placeholderValues('something else', seen)).toEqual({})
    expect(placeholderValues('ab', '[A_1][B_1]')).toEqual({})
    expect(placeholderValues(typed, typed)).toEqual({})
  })

  it('puts placeholders back in a restored reply and splits them for highlighting', () => {
    const reply = 'I will send it to ana.lopez@example.com today.'
    const masked = withPlaceholders(reply, { '[EMAIL_1]': 'ana.lopez@example.com' })
    expect(masked).toBe('I will send it to [EMAIL_1] today.')
    expect(splitPlaceholders(masked)).toEqual([
      { text: 'I will send it to ', placeholder: false },
      { text: '[EMAIL_1]', placeholder: true },
      { text: ' today.', placeholder: false },
    ])
  })
})
