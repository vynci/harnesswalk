import { describe, expect, it } from 'vitest'
import { diffLines, diffStats, parseUnifiedDiff, unifiedDiff } from './diff'

describe('line diffs', () => {
  it('keeps common lines and marks the changed ones', () => {
    const lines = diffLines('a\nb\nc\n', 'a\nB\nc\nd\n')
    expect(lines).toEqual([
      { type: 'same', text: 'a' },
      { type: 'removed', text: 'b' },
      { type: 'added', text: 'B' },
      { type: 'same', text: 'c' },
      { type: 'added', text: 'd' },
    ])
    expect(diffStats(lines)).toEqual({ added: 2, removed: 1 })
  })

  it('writes a unified diff that reads back to the same changes', () => {
    const before = Array.from({ length: 20 }, (_, i) => `line ${i + 1}`).join('\n')
    const after = before.replace('line 3', 'line three').replace('line 18', 'line eighteen')
    const diff = unifiedDiff(before, after, { from: 'skill/x@v1', to: 'skill/x@v2' })
    expect(diff.split('\n').slice(0, 2)).toEqual(['--- skill/x@v1', '+++ skill/x@v2'])
    // Two changes 15 lines apart are two hunks with three lines of context each.
    expect(diff.match(/^@@/gm)).toHaveLength(2)
    expect(diff).toContain('@@ -1,6 +1,6 @@')
    const parsed = parseUnifiedDiff(diff)
    expect(parsed.filter((l) => l.type === 'hunk')).toHaveLength(2)
    expect(parsed.filter((l) => l.type !== 'same' && l.type !== 'hunk')).toEqual([
      { type: 'removed', text: 'line 3' },
      { type: 'added', text: 'line three' },
      { type: 'removed', text: 'line 18' },
      { type: 'added', text: 'line eighteen' },
    ])
    expect(unifiedDiff('same', 'same')).toBe('')
  })

  it('reads git-style headers and the no-newline marker without showing them', () => {
    const parsed = parseUnifiedDiff('diff --git a/x b/x\nindex 1..2\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n')
    expect(parsed).toEqual([
      { type: 'hunk', text: '@@ -1 +1 @@' },
      { type: 'removed', text: 'old' },
      { type: 'added', text: 'new' },
    ])
  })
})
