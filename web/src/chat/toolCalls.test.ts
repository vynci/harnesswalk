import { describe, expect, it } from 'vitest'
import type { HarnessEvent } from '@/api/types'
import { examples } from '@/test/fixtures'
import { toolCalls } from './toolCalls'

describe('tool calls from the event stream', () => {
  it('a read tool under policy runs and ends ok', () => {
    expect(toolCalls(examples.tool_call)).toEqual([
      expect.objectContaining({ callId: 'call_1', tool: 'lookup_order', status: 'done', decision: 'allow', rule: 'dev.read.allow', actionClass: 'read', outcome: 'ok', executed: true }),
    ])
  })

  it('a destructive tool waits for approval, then runs', () => {
    const all = examples.approval_pause
    const waiting = all.slice(0, all.findIndex((e) => e.type === 'approval_requested') + 1)
    expect(toolCalls(waiting)[0]).toMatchObject({ status: 'waiting', decision: 'needs_approval', args: { order_id: 'A-1002' }, approval: { state: 'requested' } })
    expect(toolCalls(all)[0]).toMatchObject({ status: 'done', approval: { state: 'approve', decidedBy: 'user:dev' } })
  })

  it('a denied call reads denied, a failed one error', () => {
    const all = examples.tool_call
    const exitAt = all.findIndex((e) => e.type === 'node_exit' && e.summary?.call_id && e.summary?.outcome)
    const withOutcome = (summary: Record<string, unknown>, status: HarnessEvent['status'] = 'done') =>
      all.map((e, i) => (i === exitAt ? { ...e, status, summary: { ...e.summary, ...summary } } : e))
    expect(toolCalls(withOutcome({ outcome: 'denied', executed: false }))[0]!.status).toBe('denied')
    expect(toolCalls(withOutcome({ outcome: 'timeout' }))[0]!.status).toBe('error')
    expect(toolCalls(withOutcome({}, 'error'))[0]!.status).toBe('error')
  })

  it('a turn without tools has none', () => {
    expect(toolCalls(examples.plain_answer)).toEqual([])
  })
})
