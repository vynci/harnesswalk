// Line diffs for artifact edits and release diffs: a small LCS diff for the edit preview (and the
// mock's PUT responses), a unified-diff writer, and a reader for the unified diffs the API returns.

export type DiffLineType = 'same' | 'added' | 'removed' | 'hunk'

export interface DiffLine {
  type: DiffLineType
  text: string
}

function splitLines(text: string): string[] {
  if (text === '') return []
  const lines = text.split('\n')
  if (lines[lines.length - 1] === '') lines.pop()
  return lines
}

/** Above this many cells the LCS table is skipped and the whole text shows as replaced. */
const MAX_CELLS = 4_000_000

/** The lines of `before` and `after` as kept, removed and added, by longest common subsequence. */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = splitLines(before)
  const b = splitLines(after)
  // Trim the common head and tail first: edits are usually small.
  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) head++
  let tail = 0
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++
  const ma = a.slice(head, a.length - tail)
  const mb = b.slice(head, b.length - tail)
  const out: DiffLine[] = a.slice(0, head).map((text) => ({ type: 'same', text }))
  if (ma.length * mb.length > MAX_CELLS) {
    for (const text of ma) out.push({ type: 'removed', text })
    for (const text of mb) out.push({ type: 'added', text })
  } else {
    const n = ma.length
    const m = mb.length
    const lcs: Uint32Array[] = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        lcs[i]![j] = ma[i] === mb[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!)
      }
    }
    let i = 0
    let j = 0
    while (i < n && j < m) {
      if (ma[i] === mb[j]) {
        out.push({ type: 'same', text: ma[i]! })
        i++
        j++
      } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) {
        out.push({ type: 'removed', text: ma[i++]! })
      } else {
        out.push({ type: 'added', text: mb[j++]! })
      }
    }
    while (i < n) out.push({ type: 'removed', text: ma[i++]! })
    while (j < m) out.push({ type: 'added', text: mb[j++]! })
  }
  for (const text of a.slice(a.length - tail)) out.push({ type: 'same', text })
  return out
}

/** A unified diff (`---`, `+++`, `@@` hunks with `context` lines around each change). */
export function unifiedDiff(before: string, after: string, opts: { from?: string; to?: string; context?: number } = {}): string {
  const lines = diffLines(before, after)
  const context = opts.context ?? 3
  const changed = lines.map((l, i) => (l.type === 'same' ? -1 : i)).filter((i) => i >= 0)
  if (changed.length === 0) return ''
  // Group changes whose context windows touch into one hunk.
  const hunks: [number, number][] = []
  for (const i of changed) {
    const start = Math.max(0, i - context)
    const end = Math.min(lines.length - 1, i + context)
    const last = hunks[hunks.length - 1]
    if (last && start <= last[1] + 1) last[1] = end
    else hunks.push([start, end])
  }
  const out = [`--- ${opts.from ?? 'a'}`, `+++ ${opts.to ?? 'b'}`]
  // Line numbers in the old and new text at each index of `lines`.
  const oldAt: number[] = []
  const newAt: number[] = []
  let o = 1
  let n = 1
  for (const l of lines) {
    oldAt.push(o)
    newAt.push(n)
    if (l.type !== 'added') o++
    if (l.type !== 'removed') n++
  }
  for (const [start, end] of hunks) {
    const slice = lines.slice(start, end + 1)
    const oldLen = slice.filter((l) => l.type !== 'added').length
    const newLen = slice.filter((l) => l.type !== 'removed').length
    out.push(`@@ -${oldLen ? oldAt[start] : oldAt[start]! - 1},${oldLen} +${newLen ? newAt[start] : newAt[start]! - 1},${newLen} @@`)
    for (const l of slice) out.push(`${l.type === 'added' ? '+' : l.type === 'removed' ? '-' : ' '}${l.text}`)
  }
  return out.join('\n') + '\n'
}

/** The lines of a unified diff, without the file headers. */
export function parseUnifiedDiff(diff: string): DiffLine[] {
  const out: DiffLine[] = []
  for (const line of splitLines(diff)) {
    if (line.startsWith('--- ') || line.startsWith('+++ ') || line.startsWith('diff ') || line.startsWith('index ')) continue
    if (line.startsWith('\\')) continue // "\ No newline at end of file"
    if (line.startsWith('@@')) out.push({ type: 'hunk', text: line })
    else if (line.startsWith('+')) out.push({ type: 'added', text: line.slice(1) })
    else if (line.startsWith('-')) out.push({ type: 'removed', text: line.slice(1) })
    else out.push({ type: 'same', text: line.startsWith(' ') ? line.slice(1) : line })
  }
  return out
}

export function diffStats(lines: DiffLine[]): { added: number; removed: number } {
  let added = 0
  let removed = 0
  for (const l of lines) {
    if (l.type === 'added') added++
    else if (l.type === 'removed') removed++
  }
  return { added, removed }
}
