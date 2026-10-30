import type { Diagram, HarnessEvent } from '@/api/types'

/**
 * Step-over in replay: the index of the next `node_enter` of a simple-view node after `cursor`,
 * skipping everything inside expert children, or the last event when there is none.
 */
export function nextSimpleEnter(events: HarnessEvent[], cursor: number, diagram: Diagram | null): number {
  const simple = new Set((diagram?.nodes ?? []).filter((n) => n.view === 'simple').map((n) => n.id))
  for (let i = cursor + 1; i < events.length; i++) {
    const e = events[i]!
    if (e.type === 'node_enter' && e.node_id && simple.has(e.node_id)) return i
  }
  return events.length - 1
}
