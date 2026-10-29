import { describe, expect, it } from 'vitest'
import { diagram } from './fixtures'

// The drift rule (web/README.md): the UI draws whatever /v1/diagram returns and reads summary
// keys, never node ids. The one node the code may name is the memory badge node (layout.ts).
// Words that are also node ids but mean something else in the contract are listed with why.
const sources = import.meta.glob(['../**/*.{ts,tsx}', '!../**/*.test.ts', '!../mock/**', '!../components/ui/**', '!../test/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const ALLOWED: Record<string, string> = {
  'diagram/layout.ts:memory': 'BADGE_NODE, the node whose store children become badges',
  'diagram/stats.ts:memory': 'the doc comment of the badge rule',
  'diagram/stats.ts:approval': 'node_paused.summary.reason, an enum of events.md',
  'state/reducer.ts:approval': 'node_paused.summary.reason',
  'chat/ChatPanel.tsx:approval': 'node_paused.summary.reason',
  'state/store.ts:session': 'the localStorage key harnesswalk.session',
  'diagram/Legend.tsx:release': 'the edge kind release',
  'api/types.ts:release': 'the edge kind release',
  'chat/ToolTrace.tsx:sandbox': 'a field of ToolCallView',
}

describe('drift rule', () => {
  it('no node id is a string literal in the UI, beyond the listed homonyms', () => {
    const found: string[] = []
    for (const [path, text] of Object.entries(sources)) {
      const file = path.replace(/^\.\.\//, '')
      for (const node of diagram.nodes) {
        if (new RegExp(`['"\`]${node.id}['"\`]`).test(text) && !ALLOWED[`${file}:${node.id}`]) found.push(`${file}: ${node.id}`)
      }
    }
    expect(found).toEqual([])
    expect(Object.keys(sources).length).toBeGreaterThan(30)
  })
})
