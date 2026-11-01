// The payloads the mock serves (spec/contracts/tracer.md, "Payloads"): real detail records from
// fake-provider turns on the starter pack (payloads.json, written by
// tools/record_mock_payloads.py), one per payload node's enter and exit, matched to the recorded
// stream by node, event type and occurrence. The recorded message and reply are swapped for the
// ones the mock turn actually has, so the Payload tab shows what the reader typed.
import type { HarnessEvent, Payload } from '@/api/types'
import recorded from './payloads.json'
import type { ExampleName } from './examples'

interface Recorded {
  node_id: string
  type: 'node_enter' | 'node_exit'
  occurrence: number
  payload: Payload
}

const DATA = recorded as unknown as { messages: Record<ExampleName, string> } & Record<ExampleName, Recorded[]>

/** The reply each recorded payload set ends with (its gateway reply). */
function recordedReply(name: ExampleName): string | null {
  const last = [...DATA[name]].reverse().find((r) => r.node_id === 'gateway' && r.type === 'node_exit')
  const reply = (last?.payload as { reply?: { text?: string } } | undefined)?.reply?.text
  return typeof reply === 'string' ? reply : null
}

const tokensOf = (text: string) => Math.ceil(text.length / 4)

/** Swap texts inside a payload; a capped text keeps `chars` (and a section `tokens`) in step. */
function swap(value: unknown, pairs: [string, string][]): unknown {
  if (typeof value === 'string') return pairs.reduce((s, [from, to]) => (from && from !== to ? s.split(from).join(to) : s), value)
  if (Array.isArray(value)) return value.map((v) => swap(v, pairs))
  if (!value || typeof value !== 'object') return value
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(value)) out[k] = swap(v, pairs)
  const src = value as Record<string, unknown>
  if (typeof out.text === 'string' && typeof src.text === 'string' && out.text !== src.text && typeof out.chars === 'number') {
    out.chars = out.text.length
    if (typeof out.tokens === 'number') out.tokens = tokensOf(out.text)
  }
  return out
}

/** Recompute the token totals after a swap (sections changed length). */
function retotal(p: Record<string, unknown>): void {
  const fix = (holder: Record<string, unknown> | undefined) => {
    if (!holder || !Array.isArray(holder.sections)) return
    holder.total_tokens = (holder.sections as { tokens: number }[]).reduce((n, s) => n + s.tokens, 0)
  }
  fix(p)
  fix(p.request as Record<string, unknown> | undefined)
}

/**
 * The detail record for each event of a mock turn that has one, keyed by the template's position
 * among the events of the same node and type. `message` and `reply` are the mock turn's own.
 */
export function payloadFor(name: ExampleName, ev: Pick<HarnessEvent, 'node_id' | 'type'>, occurrence: number, message: string, reply: string | null): { payload: Payload } | null {
  const hit = DATA[name].find((r) => r.node_id === ev.node_id && r.type === ev.type && r.occurrence === occurrence)
  if (!hit) return null
  const pairs: [string, string][] = [[DATA.messages[name], message]]
  const was = recordedReply(name)
  if (was && reply) pairs.push([was, reply])
  const payload = swap(hit.payload, pairs) as Record<string, unknown>
  retotal(payload)
  return { payload: payload as unknown as Payload }
}
