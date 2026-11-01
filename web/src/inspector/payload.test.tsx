import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { createApi } from '@/api/client'
import { loadTurnEvents } from '@/api/stream'
import type { HarnessEvent, Payload } from '@/api/types'
import { MockServer } from '@/mock/server'
import recorded from '@/mock/payloads.json'
import { edgePayloadNode, payloadEvent, payloadOf, preview, share, wordDiff } from './payload'
import { PayloadView } from './PayloadTab'

type Rec = { node_id: string; type: string; occurrence: number; payload: Payload }
const DATA = recorded as unknown as Record<string, Rec[]>
const rec = (example: string, nodeId: string, type = 'node_exit', occurrence = 0) =>
  DATA[example]!.find((r) => r.node_id === nodeId && r.type === type && r.occurrence === occurrence)!.payload
const html = (payload: Payload, events: HarnessEvent[] = []) => renderToStaticMarkup(<PayloadView payload={payload} events={events} />)
const count = (out: string, needle: string) => out.split(needle).length - 1

const ev = (over: Partial<HarnessEvent>): HarnessEvent =>
  ({ schema_version: '0.1.0', event_id: 'e', seq: 0, ts: '', trace_id: 't', turn_id: 'turn', session_id: 's', tenant_id: 'dev', type: 'node_exit', status: 'done', ...over }) as HarnessEvent

describe('payload helpers', () => {
  it('finds the latest flagged enter or exit of a node, never a child or an unflagged detail', () => {
    const events = [
      ev({ seq: 1, type: 'node_enter', node_id: 'a', detail_ref: 'trace://t/1', summary: { has_payload: true } }),
      ev({ seq: 2, type: 'node_exit', node_id: 'b', detail_ref: 'trace://t/2', summary: { hits: 1 } }),
      ev({ seq: 3, type: 'node_exit', node_id: 'a', detail_ref: 'trace://t/3', summary: { has_payload: true } }),
      ev({ seq: 4, type: 'node_paused', node_id: 'a', summary: { reason: 'breakpoint' } }),
    ]
    expect(payloadEvent(events, 'a')?.seq).toBe(3)
    expect(payloadEvent(events.slice(0, 2), 'a')?.seq).toBe(1)
    expect(payloadEvent(events, 'b')).toBeNull()
    expect(payloadEvent(events, null)).toBeNull()
    // An edge opens the end that recorded what it carries: the source first, else the target.
    expect(edgePayloadNode(events, 'a', 'c')).toBe('a')
    expect(edgePayloadNode(events, 'c', 'a')).toBe('a')
    expect(edgePayloadNode(events, 'b', 'c')).toBeNull()
  })

  it('reads only payload records', () => {
    expect(payloadOf({ payload: rec('tool_call', 'context_builder') })?.shape).toBe('context')
    expect(payloadOf({ items: {}, messages: [] })).toBeNull()
    expect(payloadOf({ payload: { shape: 'other' } })).toBeNull()
    expect(payloadOf(null)).toBeNull()
  })

  it('previews three lines and caps a long line', () => {
    expect(preview('a\nb\nc\nd')).toEqual({ head: 'a\nb\nc', more: true })
    expect(preview('a\nb')).toEqual({ head: 'a\nb', more: false })
    const long = preview('x'.repeat(400))
    expect(long.more).toBe(true)
    expect(long.head.endsWith('…')).toBe(true)
  })

  it('computes token shares', () => {
    expect(share(25, 100)).toBe(25)
    expect(share(1, 3)).toBe(33.3)
    expect(share(5, 0)).toBe(0)
  })

  it('diffs by words', () => {
    expect(wordDiff('same text', 'same text')).toEqual([{ type: 'same', text: 'same text' }])
    const parts = wordDiff('Mail [EMAIL_1] today.', 'Mail jo@example.com today.')
    expect(parts).toEqual([
      { type: 'same', text: 'Mail ' },
      { type: 'removed', text: '[EMAIL_1] ' },
      { type: 'added', text: 'jo@example.com ' },
      { type: 'same', text: 'today.' },
    ])
    const appended = wordDiff('An answer.', 'An answer.\n\n(No sources were cited for this answer.)')
    expect(appended.filter((p) => p.type === 'added').map((p) => p.text).join('')).toContain('No sources were cited')
    expect(appended.some((p) => p.type === 'removed')).toBe(true) // the last word gained a newline
  })
})

describe('PayloadView', () => {
  it('context: one section per entry with role, kind, source and tokens, and Copy as JSON', () => {
    const p = rec('tool_call', 'context_builder')
    const out = html(p)
    expect(p.shape).toBe('context')
    const sections = p.shape === 'context' ? p.sections! : []
    expect(count(out, 'data-testid="payload-section"')).toBe(sections.length)
    expect(out).toContain('System prompt')
    expect(out).toContain('Skills index')
    expect(out).toContain('from order_status, returns')
    expect(out).toContain(`${sections[0]!.tokens} tokens`)
    expect(out).toContain('data-testid="payload-copy-json"')
    expect(out).toContain('of a 16,000 budget')
    // Collapsed to the first three lines, with Expand.
    expect(out).toContain('Expand')
    expect(out).not.toContain(sections[0]!.text.split('\n').filter(Boolean).at(-1)!)
  })

  it('message: received and redacted side by side when they differ, with the count of values', () => {
    const p: Payload = {
      node: 'gateway',
      shape: 'message',
      at: 'exit',
      version: 1,
      phase: 'reply',
      received: { text: 'Mail me at jo@example.com', chars: 25, truncated: false },
      withheld: false,
      redacted: { text: 'Mail me at [EMAIL_1]', chars: 20, truncated: false },
      blocked: false,
      placeholders: { count: 1, kinds: { email: 1 }, tokens: ['[EMAIL_1]'] },
      reply: { text: 'Sure, **done**.', chars: 15, truncated: false },
    }
    const out = html(p)
    expect(out).toContain('data-testid="payload-side-by-side"')
    expect(out).toContain('As received')
    expect(out).toContain('After redaction')
    expect(out).toContain('1 value was replaced with placeholders (1 email)')
    expect(out).toContain('<strong>done</strong>') // the reply goes through the markdown renderer
    // One block when nothing changed, and a note when the original is withheld.
    const same = html({ ...p, received: p.redacted, placeholders: { count: 0, kinds: {}, tokens: [] } })
    expect(same).not.toContain('payload-side-by-side')
    expect(same).toContain('No values were replaced.')
    const withheld = html({ ...p, received: null, withheld: true })
    expect(withheld).not.toContain('payload-side-by-side')
    expect(withheld).toContain('data-testid="payload-withheld"')
  })

  it('input checks: a list with verdict chips and what each changed', () => {
    const p: Payload = {
      node: 'input_guardrails',
      shape: 'input_checks',
      at: 'exit',
      version: 1,
      input: { text: 'Hi jo@example.com', chars: 17, truncated: false },
      withheld: false,
      chars: 17,
      output: { text: 'Hi [EMAIL_1]', chars: 12, truncated: false },
      action: 'redact',
      blocked: false,
      checks: [
        { check: 'size', verdict: 'pass', looked_at: { stage: 'received', chars: 17 }, findings: {}, change: null, reason: null },
        { check: 'pii', verdict: 'redact', looked_at: { stage: 'received', chars: 17 }, findings: { email: 1 }, change: 'replaced 1 email with placeholders', reason: null },
        { check: 'injection', verdict: 'pass', looked_at: { stage: 'after pii', chars: 12 }, findings: {}, change: null, reason: null, score: 0 },
      ],
      warnings: [],
      placeholders: { count: 1, kinds: { email: 1 }, tokens: ['[EMAIL_1]'] },
    }
    const out = html(p)
    expect(count(out, 'data-testid="payload-check"')).toBe(3)
    expect(out).toMatch(/data-verdict="redact"[^>]*>redact</)
    expect(out).toContain('Changed: replaced 1 email with placeholders')
    expect(out).toContain('read 12 chars, after pii')
    expect(out).toContain('Clean input (what goes on)')
  })

  it('model call: the request sections and the reply, its tool calls as tool lines', () => {
    const first = rec('tool_call', 'model_layer', 'node_exit', 0)
    const tools = [
      ev({ seq: 30, type: 'node_enter', node_id: 'tools', summary: { tool: 'lookup_order', call_id: 'call_1' } }),
      ev({ seq: 31, type: 'node_exit', node_id: 'tools', summary: { tool: 'lookup_order', call_id: 'call_1', outcome: 'ok', executed: true } }),
    ]
    const out = html(first, tools)
    expect(out).toContain('data-testid="payload-request"')
    expect(out).toContain('No text: the model asked for tools instead.')
    expect(out).toContain('data-testid="tool-line"')
    expect(out).toContain('Ran lookup_order')
    // Before the tool ran there is no line yet: the call as asked.
    expect(html(first)).toContain('Asked for lookup_order')
    const second = html(rec('tool_call', 'model_layer', 'node_exit', 1))
    expect(second).toContain('lookup_order result')
    expect(second).toContain('Order A-1001 shipped on 24 October')
    expect(html(rec('tool_call', 'model_layer', 'node_enter', 1))).toContain('Request about to be sent')
  })

  it('output checks: before and after as a word diff, with each verdict', () => {
    const p: Payload = {
      node: 'output_guardrails',
      shape: 'output_checks',
      at: 'exit',
      version: 1,
      before: { text: 'Order shipped.', chars: 14, truncated: false },
      after: { text: 'Order shipped.\n\n(No sources were cited for this answer.)', chars: 56, truncated: false },
      action: 'repair',
      repairs: ['citations'],
      reasons: ['citations'],
      checks: [
        { check: 'schema', verdict: 'skipped', note: 'no structured reply was asked for' },
        { check: 'citations', verdict: 'repair', note: 'no citation: a notice was added' },
      ],
      restored: 0,
      restore_mode: true,
    }
    const out = html(p)
    expect(out).toContain('data-testid="payload-before"')
    expect(out).toContain('data-testid="payload-after"')
    expect(out).toContain('data-diff="added"')
    expect(out).toContain('Repaired: citations.')
    expect(count(out, 'data-testid="payload-check"')).toBe(2)
    expect(out).toContain('no citation: a notice was added')
    expect(html({ ...p, after: p.before, action: 'pass', repairs: [] })).toContain('Unchanged by the checks')
  })

  it('says when a text was cut at the cap', () => {
    const p: Payload = { node: 'output_guardrails', shape: 'output_checks', at: 'enter', version: 1, before: { text: 'a'.repeat(32000), chars: 40000, truncated: true } }
    expect(html(p)).toContain('Cut at 32,000 of 40,000 characters.')
  })
})

describe('the mock serves payloads', () => {
  it('flags the payload events of a live turn and serves their records, with the typed message', async () => {
    const server = new MockServer({ stepMs: 0, seed: false })
    const api = createApi(server.transport())
    const { turn_id } = await api.startTurn({ message: 'Where is order A-1001, please?' })
    await new Promise<void>((resolve) => {
      const tick = () => (server.getTurn(turn_id)?.status === 'done' ? resolve() : setTimeout(tick, 5))
      tick()
    })
    const events = await loadTurnEvents(server.transport(), turn_id)
    const flagged = events.filter((e) => e.summary?.has_payload)
    expect(flagged.length).toBe(14)
    const context = payloadEvent(events, 'context_builder')!
    expect(context.type).toBe('node_exit')
    const res = await api.detail(turn_id, context.event_id)
    const payload = payloadOf(res.detail)
    expect(payload?.shape).toBe('context')
    const sections = payload?.shape === 'context' ? payload.sections! : []
    expect(sections.at(-1)!.text).toBe('Where is order A-1001, please?')
    expect(payload?.shape === 'context' && payload.total_tokens).toBe(sections.reduce((n, s) => n + s.tokens, 0))
    await expect(api.detail(turn_id, events[0]!.event_id)).rejects.toMatchObject({ code: 'detail_not_found' })
    await expect(api.detail('turn_nope', context.event_id)).rejects.toMatchObject({ code: 'turn_not_found' })
  })
})
