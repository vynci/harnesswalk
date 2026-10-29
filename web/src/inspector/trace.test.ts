import { describe, expect, it } from 'vitest'
import type { HarnessEvent } from '@/api/types'
import { examples } from '@/test/fixtures'
import { traceItems, traceStatus } from './trace'

const label = (id: string) => id
const upTo = (events: HarnessEvent[], pred: (e: HarnessEvent) => boolean) => events.slice(0, events.findIndex(pred) + 1)
const statusOf = (events: HarnessEvent[], pred: (e: HarnessEvent) => boolean, opts?: { turnOver?: boolean }) =>
  traceStatus(events, events.findIndex(pred), opts)

describe('trace status per event', () => {
  const all = examples.approval_pause
  const enterTools = (e: HarnessEvent) => e.type === 'node_enter' && e.node_id === 'tools'
  const requested = (e: HarnessEvent) => e.type === 'approval_requested'
  const paused = (e: HarnessEvent) => e.type === 'node_paused'

  it('a node_enter is running while its span is open, plain once it closes', () => {
    const live = upTo(all, requested)
    expect(statusOf(live, enterTools)).toBe('running')
    expect(statusOf(all, enterTools)).toBeUndefined()
  })

  it('an approval request and its pause are pending until the decision', () => {
    const live = upTo(all, paused)
    expect(statusOf(live, requested)).toBe('pending')
    expect(statusOf(live, paused)).toBe('pending')
    expect(statusOf(all, requested)).toBeUndefined()
    expect(statusOf(all, paused)).toBeUndefined()
  })

  it('exits, edges and decisions are plain lines', () => {
    for (const type of ['node_exit', 'edge', 'approval_decided', 'turn_end'] as const) {
      expect(statusOf(all, (e) => e.type === type), type).toBeUndefined()
    }
  })

  it('a failed event is error, open or not', () => {
    const failed: HarnessEvent = { ...all.find((e) => e.type === 'node_exit')!, status: 'error', error: { code: 'tool_failed', message: 'boom' } }
    expect(traceStatus([failed], 0)).toBe('error')
    const harnessError: HarnessEvent = { ...all[0]!, type: 'error', status: 'error' }
    expect(traceStatus([harnessError], 0)).toBe('error')
  })

  it('a turn that is over closes every open line', () => {
    const live = upTo(all, requested)
    expect(statusOf(live, enterTools, { turnOver: true })).toBeUndefined()
    expect(statusOf(live, requested, { turnOver: true })).toBeUndefined()
  })
})

describe('trace items', () => {
  it('names child nodes, keeps the event type and lists seq, duration and tokens', () => {
    const events = examples.tool_call.filter((e) => e.node_id === 'model_layer' || e.node_id === 'router')
    const items = traceItems(events, { self: 'model_layer', label })
    const exit = items.find((i) => i.event.type === 'node_exit' && i.event.node_id === 'model_layer')!
    expect(exit.title).toBe('node_exit')
    expect(exit.description).toMatch(/^#\d+ · .*905 in \/ 24 out tok$/)
    expect(exit.detail).toMatchObject({ summary: { finish_reason: 'tool_calls' }, cost: { input_tokens: 905 } })
    expect(items.find((i) => i.event.node_id === 'router')!.title).toBe('node_enter · router')
    expect(items.find((i) => i.event.type === 'node_enter' && i.event.node_id === 'model_layer')!.detail).toMatchObject({ summary: { step: 1 } })
  })

  it('an event with nothing to show opens to nothing', () => {
    const enter = examples.tool_call.find((e) => e.type === 'node_enter' && !e.summary)!
    expect(traceItems([enter], { self: enter.node_id!, label })[0]!.detail).toBeNull()
  })
})
