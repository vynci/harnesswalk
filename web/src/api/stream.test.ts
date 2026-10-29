import { describe, expect, it } from 'vitest'
import { MockServer } from '@/mock/server'
import { examples, names } from '@/test/fixtures'
import { createApi } from './client'
import { loadTurnEvents, openTurnStream, type StreamEnd } from './stream'
import type { HarnessEvent } from './types'
import { createEventValidator } from './validate'

const validate = createEventValidator()

function collect(server: MockServer, turnId: string, opts: { after?: number; follow?: boolean } = {}) {
  return new Promise<{ events: HarnessEvent[]; end: StreamEnd; invalid: number }>((resolve) => {
    const events: HarnessEvent[] = []
    let invalid = 0
    openTurnStream(server.transport(), turnId, { ...opts, validate, retryBaseMs: 5 }, {
      onEvent: (e) => events.push(e),
      onInvalid: () => invalid++,
      onEnd: (end) => resolve({ events, end, invalid }),
    })
  })
}

function contiguous(events: HarnessEvent[]) {
  return events.every((e, i) => e.seq === i)
}

describe('event validator', () => {
  it('accepts every recorded example event', () => {
    for (const name of names) for (const e of examples[name]) expect(validate(e), `${name} #${e.seq}`).toBeNull()
  })
  it('rejects an event that breaks the schema', () => {
    const bad = { ...examples.plain_answer[1]!, status: 'done' }
    expect(validate(bad)).not.toBeNull()
  })
})

describe('stream client against the mock API', () => {
  it('streams a live turn to turn_end, in order, valid, with the reply split into deltas', async () => {
    const server = new MockServer({ stepMs: 0, seed: false })
    const api = createApi(server.transport())
    const { turn_id } = await api.startTurn({ message: 'What are your support hours?' })
    const { events, end, invalid } = await collect(server, turn_id)
    expect(end).toBe('turn_end')
    expect(invalid).toBe(0)
    expect(contiguous(events)).toBe(true)
    const reply = events.at(-1)!.summary!.reply as string
    const deltas = events.filter((e) => e.type === 'message_delta').map((e) => e.summary!.delta).join('')
    expect(deltas).toBe(reply)
  })

  it('reconnects after a dropped connection without losing or repeating events', async () => {
    const server = new MockServer({ stepMs: 2, seed: false })
    const api = createApi(server.transport())
    const { turn_id } = await api.startTurn({ message: 'Where is order A-1001?' })
    const done = collect(server, turn_id)
    await new Promise((r) => setTimeout(r, 40))
    server.dropConnections()
    await new Promise((r) => setTimeout(r, 30))
    server.dropConnections()
    const { events, end, invalid } = await done
    expect(end).toBe('turn_end')
    expect(contiguous(events)).toBe(true)
    expect(events.at(-1)!.type).toBe('turn_end')
    expect(invalid).toBe(0) // a connection error is not an invalid harness event
  })

  it('replays a finished turn with follow=false and resumes from a cursor with after', async () => {
    const server = new MockServer({ stepMs: 0 })
    const all = await loadTurnEvents(server.transport(), 'turn_tool_call', validate)
    expect(all).toHaveLength(examples.tool_call.length)
    expect(all[0]!.type).toBe('turn_start')
    const tail = await collect(server, 'turn_tool_call', { after: 40, follow: false })
    expect(tail.events[0]!.seq).toBe(41)
    expect(tail.end).toBe('turn_end')
  })

  it('pauses for approval, then finishes after the decision', async () => {
    const server = new MockServer({ stepMs: 0, seed: false })
    const api = createApi(server.transport())
    const { turn_id } = await api.startTurn({ message: 'Please cancel order A-1002.' })
    const done = collect(server, turn_id)
    let summary = await api.turn(turn_id)
    for (let i = 0; i < 100 && summary.status !== 'paused'; i++) {
      await new Promise((r) => setTimeout(r, 5))
      summary = await api.turn(turn_id)
    }
    expect(summary.paused_at_node).toBe('approval')
    expect(summary.pending_approval_id).toBeTruthy()
    await api.decide(summary.pending_approval_id!, 'deny')
    const { events } = await done
    expect(events.find((e) => e.type === 'approval_decided')?.summary?.decision).toBe('deny')
    expect(events.some((e) => e.node_id === 'sandbox')).toBe(false)
    expect((await api.turn(turn_id)).status).toBe('done')
  })

  it('stops at every node in step mode and cancels cleanly', async () => {
    const server = new MockServer({ stepMs: 0, seed: false })
    const api = createApi(server.transport())
    const { turn_id } = await api.startTurn({ message: 'What are your support hours?', step: true })
    const done = collect(server, turn_id)
    await new Promise((r) => setTimeout(r, 20))
    expect((await api.turn(turn_id)).paused_at_node).toBe('gateway')
    await api.resume(turn_id, 'step')
    await new Promise((r) => setTimeout(r, 20))
    expect((await api.turn(turn_id)).paused_at_node).toBe('input_guardrails')
    await api.cancel(turn_id)
    const { events, invalid } = await done
    expect(invalid).toBe(0)
    const end = events.at(-1)!
    expect(end.type).toBe('turn_end')
    expect(end.status).toBe('done')
    expect(end.summary?.exit).toBe('cancelled')
    expect(events.filter((e) => e.type === 'error').every((e) => e.error?.code === 'cancelled')).toBe(true)
    await expect(api.cancel(turn_id)).rejects.toMatchObject({ status: 409 })
  })

  it('lists session turns newest first', async () => {
    const server = new MockServer({ stepMs: 0 })
    const api = createApi(server.transport())
    const list = await api.sessionTurns('sess_demo')
    expect(list.map((t) => t.turn_id)).toEqual(['turn_approval_pause', 'turn_tool_call', 'turn_plain_answer'])
    await expect(api.sessionTurns('nope')).rejects.toMatchObject({ status: 404 })
  })
})
