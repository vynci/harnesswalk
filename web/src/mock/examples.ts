// The three recorded streams from spec/examples, and the canonical diagram, read straight from
// the spec folder so the mock can never drift from it.
import diagramText from '@spec/diagram/diagram.json?raw'
import approvalPause from '@spec/examples/approval_pause.jsonl?raw'
import plainAnswer from '@spec/examples/plain_answer.jsonl?raw'
import toolCall from '@spec/examples/tool_call.jsonl?raw'
import type { Diagram, HarnessEvent } from '@/api/types'

export type ExampleName = 'plain_answer' | 'tool_call' | 'approval_pause'

export const EXAMPLE_NAMES: ExampleName[] = ['plain_answer', 'tool_call', 'approval_pause']

/** The user message behind each recorded stream (the stream itself only carries its length). */
export const EXAMPLE_MESSAGES: Record<ExampleName, string> = {
  plain_answer: 'What are your support hours?',
  tool_call: 'Where is order A-1001?',
  approval_pause: 'Please cancel order A-1002.',
}

export function parseJsonl(text: string): HarnessEvent[] {
  return text
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => JSON.parse(l) as HarnessEvent)
}

export const EXAMPLES: Record<ExampleName, HarnessEvent[]> = {
  plain_answer: parseJsonl(plainAnswer),
  tool_call: parseJsonl(toolCall),
  approval_pause: parseJsonl(approvalPause),
}

/** The spec diagram, without any served additions. */
export function specDiagram(): Diagram {
  const d = JSON.parse(diagramText) as Diagram & { $schema?: string }
  delete d.$schema
  return d
}

/**
 * The diagram as a P2 API would serve it. With no registry to ask, a node counts as implemented
 * when it appears in at least one recorded stream.
 */
export function servedDiagram(): Diagram {
  const d = specDiagram()
  const seen = new Set<string>()
  for (const events of Object.values(EXAMPLES)) for (const e of events) if (e.node_id) seen.add(e.node_id)
  return {
    ...d,
    nodes: d.nodes.map((n) => ({ ...n, implemented: seen.has(n.id), disabled: false })),
  }
}
