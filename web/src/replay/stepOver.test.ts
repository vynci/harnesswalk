import { describe, expect, it } from 'vitest'
import { diagram, examples } from '@/test/fixtures'
import { nextSimpleEnter } from './stepOver'

describe('replay step-over', () => {
  it('jumps from one simple-view node to the next, over the expert children', () => {
    const events = examples.plain_answer
    const at = (id: string) => events.findIndex((e) => e.type === 'node_enter' && e.node_id === id)
    // From the memory enter, the next simple node is the loop: procedural and session are skipped.
    expect(events[nextSimpleEnter(events, at('memory'), diagram)]?.node_id).toBe('loop')
    // From the model layer, router and providers are skipped.
    expect(events[nextSimpleEnter(events, at('model_layer'), diagram)]?.node_id).toBe('output_guardrails')
    // Past the last simple enter it lands on the last event.
    expect(nextSimpleEnter(events, events.length - 2, diagram)).toBe(events.length - 1)
  })
})
