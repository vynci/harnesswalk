import { describe, expect, it } from 'vitest'
import type { HarnessEvent } from '@/api/types'
import { diagram, edgeIndex, examples, names } from '@/test/fixtures'
import { initialTurnState, reduceEvent, reduceEvents } from './reducer'

const last = (events: HarnessEvent[]) => events[events.length - 1]!

describe('reduceEvents over the recorded example streams', () => {
  for (const name of names) {
    describe(name, () => {
      const events = examples[name]
      const state = reduceEvents(events, edgeIndex)
      const end = last(events)

      it('ends done with the turn_end reply, exit and total cost', () => {
        expect(state.phase).toBe('done')
        expect(state.exit).toBe('answered')
        expect(state.reply).toBe(end.summary?.reply)
        expect(state.cost).toEqual(end.cost)
        expect(state.costIsTotal).toBe(true)
        expect(state.steps).toBe(end.summary?.steps)
        expect(state.paused).toBeNull()
        expect(state.stack).toEqual([])
        expect(state.activeEdge).toBeNull()
        expect(state.events).toHaveLength(events.length)
      })

      it('marks every node on the canonical path done and leaves the rest idle', () => {
        const path = new Set(diagram.paths[name])
        for (const id of path) expect(state.nodes[id]?.state, id).toBe('done')
        for (const n of diagram.nodes.filter((n) => n.view === 'simple' && !path.has(n.id))) {
          expect(state.nodes[n.id]?.state ?? 'idle', n.id).toBe('idle')
        }
      })

      it('counts re-entered nodes and keeps the last exit summary', () => {
        expect(state.nodes.gateway?.enters).toBe(2)
        expect(state.nodes.gateway?.lastSummary?.phase).toBe('reply')
        const enters = events.filter((e) => e.type === 'node_enter' && e.node_id === 'model_layer').length
        expect(state.nodes.model_layer?.enters).toBe(enters)
      })

      it('lights every explicit edge from the stream', () => {
        for (const e of events.filter((e) => e.type === 'edge')) expect(state.edges[e.edge_id!]?.count, e.edge_id).toBeGreaterThan(0)
      })

      it('sums node costs while running, before turn_end replaces them with the total', () => {
        const before = reduceEvents(events.slice(0, -1), edgeIndex)
        const sum = events.filter((e) => e.type === 'node_exit' && e.cost).reduce((a, e) => a + e.cost!.input_tokens, 0)
        expect(before.cost.input_tokens).toBe(sum)
        expect(before.costIsTotal).toBe(false)
      })
    })
  }

  it('pauses at the approval with the pending card, then resumes on the decision', () => {
    const events = examples.approval_pause
    const pauseIdx = events.findIndex((e) => e.type === 'node_paused')
    const paused = reduceEvents(events.slice(0, pauseIdx + 1), edgeIndex)
    expect(paused.phase).toBe('paused')
    expect(paused.paused).toMatchObject({ nodeId: 'approval', reason: 'approval', approvalId: 'apr_0001' })
    expect(paused.nodes.approval?.state).toBe('waiting')
    expect(paused.approval).toMatchObject({ approval_id: 'apr_0001', tool: 'cancel_order', action_class: 'destructive' })
    expect(paused.approval?.decision).toBeUndefined()
    expect(paused.stack).toEqual(['loop', 'tools', 'approval'])

    const resumed = reduceEvents(events.slice(0, pauseIdx + 3), edgeIndex)
    expect(resumed.phase).toBe('running')
    expect(resumed.paused).toBeNull()
    expect(resumed.approval?.decision).toBe('approve')
    expect(resumed.nodes.approval?.state).toBe('active')
  })

  it('shows nested spans active while they are open', () => {
    const events = examples.plain_answer
    const idx = events.findIndex((e) => e.type === 'node_enter' && e.node_id === 'providers')
    const s = reduceEvents(events.slice(0, idx + 1), edgeIndex)
    expect(s.stack).toEqual(['loop', 'model_layer', 'providers'])
    for (const id of s.stack) expect(s.nodes[id]?.state).toBe('active')
    expect(s.focus).toBe('providers')
  })

  it('infers an edge from consecutive nodes when no edge event is sent', () => {
    const withoutEdges = examples.plain_answer.filter((e) => e.type !== 'edge')
    const s = reduceEvents(withoutEdges, edgeIndex)
    expect(s.edges.loop_to_model_layer?.inferred).toBe(true)
    expect(s.edges.model_layer_to_loop?.count).toBeGreaterThan(0)
  })

  it('does not double count a return edge inferred from node_exit and then sent explicitly', () => {
    const s = reduceEvents(examples.plain_answer, edgeIndex)
    expect(s.edges.model_layer_to_loop).toMatchObject({ count: 1, inferred: false })
  })

  it('ignores replayed events at or below the cursor', () => {
    const events = examples.tool_call
    const once = reduceEvents(events, edgeIndex)
    const twice = reduceEvents([...events.slice(0, 20), ...events], edgeIndex)
    expect(twice.events).toHaveLength(once.events.length)
    expect(twice.nodes).toEqual(once.nodes)
  })

  it('builds the reply from message_delta events in index order', () => {
    const base = examples.plain_answer[0]!
    const mk = (seq: number, index: number, delta: string): HarnessEvent => ({ ...base, seq, type: 'message_delta', node_id: 'gateway', status: 'active', summary: { delta, index } })
    let s = reduceEvent(initialTurnState(), base)
    s = reduceEvent(s, mk(1, 0, 'Hello '))
    s = reduceEvent(s, mk(2, 1, 'there.'))
    expect(s.reply).toBe('Hello there.')
  })

  it('closes a span on an error event and records a cancelled turn', () => {
    const events = examples.plain_answer
    const idx = events.findIndex((e) => e.type === 'node_enter' && e.node_id === 'model_layer')
    const open = reduceEvents(events.slice(0, idx + 1), edgeIndex)
    const tpl = events[idx]!
    let s = reduceEvent(open, { ...tpl, seq: idx + 1, type: 'error', status: 'error', node_id: 'model_layer', error: { code: 'cancelled', message: 'stop' }, summary: undefined })
    s = reduceEvent(s, { ...tpl, seq: idx + 2, type: 'error', status: 'error', node_id: 'loop', error: { code: 'cancelled', message: 'stop' }, summary: undefined })
    s = reduceEvent(s, { ...tpl, seq: idx + 3, type: 'turn_end', status: 'done', node_id: undefined, duration_ms: 10, summary: { exit: 'cancelled', steps: 1 }, cost: { input_tokens: 0, output_tokens: 0, usd: 0 } })
    expect(s.nodes.model_layer?.state).toBe('error')
    expect(s.nodes.loop?.state).toBe('error')
    expect(s.exit).toBe('cancelled')
    expect(s.phase).toBe('done')
    expect(s.stack).toEqual([])
  })
})
