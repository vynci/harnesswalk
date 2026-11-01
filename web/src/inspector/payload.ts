// What each content-bearing hop carried (spec/contracts/tracer.md, "Payloads"): which event of a
// node holds its latest payload (the `has_payload` flag, never a node id), which end of an edge
// shows what the edge carries, and the small text helpers the Payload tab renders with (preview
// lines, token shares, a word diff).
import type { HarnessEvent, Payload, PayloadSection, PayloadShape } from '@/api/types'

const SHAPES: ReadonlySet<string> = new Set<PayloadShape>(['message', 'input_checks', 'context', 'model_call', 'output_checks'])

/** Whether an event holds a payload: a node span's enter or exit flagged `has_payload`. */
export function hasPayload(e: HarnessEvent): boolean {
  return (e.type === 'node_enter' || e.type === 'node_exit') && !!e.detail_ref && e.summary?.has_payload === true
}

/**
 * The event of this node (not its children) that holds the latest payload. While a breakpoint
 * holds the node that is its enter record, the input it is about to work on.
 */
export function payloadEvent(events: HarnessEvent[], nodeId: string | null): HarnessEvent | null {
  if (!nodeId) return null
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]!
    if (e.node_id === nodeId && hasPayload(e)) return e
  }
  return null
}

/**
 * Which end of an edge shows what the edge carries: its source when the source recorded a payload
 * ("assembled context" is what the context builder assembled), else its target ("inference" is the
 * request the model layer recorded on entry), else neither.
 */
export function edgePayloadNode(events: HarnessEvent[], source: string, target: string): string | null {
  if (payloadEvent(events, source)) return source
  if (payloadEvent(events, target)) return target
  return null
}

/** A payload record, or null for a detail recorded before payloads (or not one at all). */
export function payloadOf(detail: unknown): Payload | null {
  if (!detail || typeof detail !== 'object') return null
  const p = (detail as { payload?: unknown }).payload
  if (!p || typeof p !== 'object') return null
  const shape = (p as { shape?: unknown }).shape
  return typeof shape === 'string' && SHAPES.has(shape) ? (p as Payload) : null
}

/** The first `lines` lines of a text, and whether anything is left out. */
export function preview(text: string, lines = 3, maxChars = 320): { head: string; more: boolean } {
  const parts = text.split('\n')
  let head = parts.slice(0, lines).join('\n')
  let more = parts.length > lines
  if (head.length > maxChars) {
    head = head.slice(0, maxChars).trimEnd() + '…'
    more = true
  }
  return { head, more }
}

/** A section's share of the total, in percent (0 to 100, one decimal). */
export function share(tokens: number, total: number): number {
  if (!total || total <= 0) return 0
  return Math.min(100, Math.round((tokens / total) * 1000) / 10)
}

export const KIND_LABEL: Record<PayloadSection['kind'], string> = {
  system: 'system',
  skill: 'skill',
  retrieved: 'retrieved',
  history: 'history',
  user: 'user',
  assistant: 'model step',
  tool: 'tool result',
}

export interface WordPart {
  type: 'same' | 'added' | 'removed'
  text: string
}

/** Above this many cells the word table is skipped and the texts show as replaced. */
const MAX_WORD_CELLS = 1_000_000

/** A word-level diff of two texts (whitespace kept with the word before it). */
export function wordDiff(before: string, after: string): WordPart[] {
  if (before === after) return before ? [{ type: 'same', text: before }] : []
  const a = before.match(/\S+\s*|\s+/g) ?? []
  const b = after.match(/\S+\s*|\s+/g) ?? []
  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) head++
  let tail = 0
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail++
  const ma = a.slice(head, a.length - tail)
  const mb = b.slice(head, b.length - tail)
  const parts: WordPart[] = []
  const push = (type: WordPart['type'], text: string) => {
    const last = parts[parts.length - 1]
    if (last && last.type === type) last.text += text
    else parts.push({ type, text })
  }
  for (const w of a.slice(0, head)) push('same', w)
  if (ma.length * mb.length > MAX_WORD_CELLS) {
    for (const w of ma) push('removed', w)
    for (const w of mb) push('added', w)
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
        push('same', ma[i++]!)
        j++
      } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) push('removed', ma[i++]!)
      else push('added', mb[j++]!)
    }
    while (i < n) push('removed', ma[i++]!)
    while (j < m) push('added', mb[j++]!)
  }
  for (const w of a.slice(a.length - tail)) push('same', w)
  return parts
}

/** "1 email" / "2 emails, 1 phone": counts by kind, in words. */
export function kindsInWords(kinds: Record<string, number>): string {
  return Object.entries(kinds)
    .sort(([x], [y]) => x.localeCompare(y))
    .map(([kind, n]) => `${n} ${kind.replace(/_/g, ' ')}${n === 1 ? '' : 's'}`)
    .join(', ')
}
