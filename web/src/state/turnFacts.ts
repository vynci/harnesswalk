// Facts about a turn that the chat and the status bar show, read from its events. Like
// stats.ts, nothing here names a node: each fact is found by the summary keys the event
// contract defines (spec/events.md section 6), so a harness that emits them from another node
// still gets the same badges and notices.
import type { Citation, HarnessEvent, Summary } from '@/api/types'

function str(v: unknown): string | undefined {
  return typeof v === 'string' && v.length > 0 ? v : undefined
}
function num(v: unknown): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}
function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}
function counts(v: unknown): Record<string, number> {
  if (!isRecord(v)) return {}
  const out: Record<string, number> = {}
  for (const [k, n] of Object.entries(v)) if (typeof n === 'number' && n > 0) out[k] = n
  return out
}

/** The last node_exit whose summary passes `match`. */
function lastExit(events: HarnessEvent[], match: (s: Summary) => boolean): HarnessEvent | undefined {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]!
    if (e.type === 'node_exit' && e.summary && match(e.summary)) return e
  }
  return undefined
}

export interface InputGuardFacts {
  action?: string
  /** Placeholders put in the message before the model saw it, by kind (email, phone, card...). */
  redactions: Record<string, number>
  injectionScore?: number
  injectionSignals: Record<string, number>
  warnings: string[]
  classifierScore?: number
}

/** The input guardrails' verdict: the exit whose summary scores injection or counts redactions. */
export function inputGuardFacts(events: HarnessEvent[]): InputGuardFacts | null {
  const e = lastExit(events, (s) => 'injection_score' in s || 'redactions' in s)
  if (!e) return null
  const s = e.summary!
  return {
    action: str(s.action),
    redactions: counts(s.redactions),
    injectionScore: num(s.injection_score),
    injectionSignals: counts(s.injection_signals),
    warnings: Array.isArray(s.warnings) ? s.warnings.filter((w): w is string => typeof w === 'string') : [],
    classifierScore: num(s.classifier_score),
  }
}

export interface OutputGuardFacts {
  action?: string
  /** Placeholders put back in the reply (dev only by default). */
  restored: number
  citations?: { required?: boolean; present?: number; invalid?: number; uncited_claims?: number }
  reasons: string[]
}

/** The output guardrails' verdict: the exit that reports `restored` or a citations check. */
export function outputGuardFacts(events: HarnessEvent[]): OutputGuardFacts | null {
  const e = lastExit(events, (s) => 'restored' in s || (isRecord(s.citations) && 'required' in s.citations))
  if (!e) return null
  const s = e.summary!
  return {
    action: str(s.action),
    restored: num(s.restored) ?? 0,
    citations: isRecord(s.citations) ? (s.citations as OutputGuardFacts['citations']) : undefined,
    reasons: Array.isArray(s.reasons) ? s.reasons.filter((r): r is string => typeof r === 'string') : [],
  }
}

export interface BudgetUse {
  name: string
  limit: number
  used: number
}

export interface LimitFacts {
  /** Why the loop stopped: max_steps, budget_tokens, budget_usd, budget_tool_calls, timeout... */
  reason?: string
  /** The policy decision that spent a budget, with its plain-words reason. */
  why?: string
  rule?: string
  budget?: BudgetUse
  source?: Record<string, string>
}

function budgetOf(v: unknown): BudgetUse | undefined {
  if (!isRecord(v)) return undefined
  const name = str(v.name)
  const limit = num(v.limit)
  const used = num(v.used)
  return name && limit !== undefined && used !== undefined ? { name, limit, used } : undefined
}

/** Why a turn ended on a limit: the loop's exit reason and the budget decision behind it. */
export function limitFacts(events: HarnessEvent[]): LimitFacts {
  const loop = lastExit(events, (s) => 'exit_reason' in s)
  const spent = lastExit(events, (s) => budgetOf(s.budget) !== undefined)
  const s = loop?.summary ?? {}
  return {
    reason: str(s.exit_reason),
    why: str(spent?.summary?.why),
    rule: str(spent?.summary?.rule),
    budget: budgetOf(spent?.summary?.budget),
    source: isRecord(s.budget_source) ? (s.budget_source as Record<string, string>) : undefined,
  }
}

export interface BudgetFacts {
  maxSteps?: number
  budgetTokens?: number
  budgetUsd?: number
  steps?: number
  toolCalls: number
  /** A budget a policy decision named (tool calls per turn, per hour...). */
  budget?: BudgetUse
  /** Plain words from the last policy decision. */
  why?: string
}

/**
 * Budgets in force for the loop (its enter summary: max_steps, budget_tokens, budget_usd) and
 * what the turn used: tool calls so far and any budget a policy decision reported.
 */
export function budgetFacts(events: HarnessEvent[]): BudgetFacts {
  let enter: Summary | undefined
  let exit: Summary | undefined
  let toolCalls = 0
  let budget: BudgetUse | undefined
  let why: string | undefined
  for (const e of events) {
    const s = e.summary ?? {}
    if (e.type === 'node_enter' && num(s.max_steps) !== undefined) enter = s
    if (e.type === 'node_exit' && 'exit_reason' in s) exit = s
    if (e.type === 'node_enter' && str(s.tool) && str(s.call_id)) toolCalls += 1
    if (e.type === 'node_exit' && str(s.decision) && str(s.why)) why = str(s.why)
    const b = e.type === 'node_exit' ? budgetOf(s.budget) : undefined
    if (b) budget = b
  }
  return {
    maxSteps: num(enter?.max_steps),
    budgetTokens: num(enter?.budget_tokens),
    budgetUsd: num(enter?.budget_usd),
    steps: num(exit?.steps),
    toolCalls,
    budget,
    why,
  }
}

/** `turn_end.summary.citations`, in first-cited order. */
export function citationsOf(events: HarnessEvent[]): Citation[] {
  const end = events.findLast((e) => e.type === 'turn_end')
  const raw = end?.summary?.citations
  if (!Array.isArray(raw)) return []
  return raw.filter(
    (c): c is Citation => isRecord(c) && typeof c.n === 'number' && typeof c.doc === 'string' && typeof c.chunk === 'number',
  )
}

export interface MemoryPoint {
  type: 'memory_proposed' | 'memory_written'
  proposalId: string
  store: string
  seq: number
  gate?: string
  preview?: string
  source?: string
  recordId?: string
  op?: string
  version?: number
}

/** The memory_proposed and memory_written point events of a turn, in order. */
export function memoryPoints(events: HarnessEvent[]): MemoryPoint[] {
  const out: MemoryPoint[] = []
  for (const e of events) {
    if (e.type !== 'memory_proposed' && e.type !== 'memory_written') continue
    const s = e.summary ?? {}
    out.push({
      type: e.type,
      proposalId: str(s.proposal_id) ?? '',
      store: str(s.store) ?? '',
      seq: e.seq,
      gate: str(s.gate),
      preview: str(s.preview),
      source: str(s.source),
      recordId: str(s.record_id),
      op: str(s.op),
      version: num(s.version),
    })
  }
  return out
}

/** Proposals the memory writer rejected (its exit summary's `rejections`), by proposal id. */
export function memoryRejections(events: HarnessEvent[]): Map<string, string> {
  const out = new Map<string, string>()
  for (const e of events) {
    const list = e.type === 'node_exit' ? e.summary?.rejections : undefined
    if (!Array.isArray(list)) continue
    for (const r of list) if (isRecord(r) && typeof r.proposal_id === 'string') out.set(r.proposal_id, str(r.reason) ?? 'rejected')
  }
  return out
}

/** The first error of the turn with this code (rule 8: `restarted`, `cancelled`...). */
export function errorWithCode(events: HarnessEvent[], code: string): HarnessEvent | undefined {
  return events.find((e) => e.type === 'error' && e.error?.code === code)
}

const PLACEHOLDER = /\[[A-Z][A-Z_]*_\d+\]/g

/**
 * The values behind the placeholders in a redacted message, by aligning it with the text the
 * user typed (which only this browser has). "to [EMAIL_1]." against "to ana@example.com."
 * gives {"[EMAIL_1]": "ana@example.com"}. Returns {} when the two do not line up.
 */
export function placeholderValues(original: string, redacted: string): Record<string, string> {
  const names = redacted.match(PLACEHOLDER) ?? []
  if (names.length === 0) return {}
  // The literal runs between placeholders, escaped, with one lazy group per placeholder; the
  // anchors make the engine backtrack until the whole message lines up.
  const runs = redacted.split(PLACEHOLDER)
  // Two placeholders side by side cannot be told apart.
  if (runs.slice(1, -1).some((t) => t === '')) return {}
  const literals = runs.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const m = new RegExp(`^${literals.join('([\\s\\S]+?)')}$`).exec(original)
  if (!m) return {}
  const out: Record<string, string> = {}
  for (let i = 0; i < names.length; i++) {
    const value = m[i + 1]!
    if (out[names[i]!] !== undefined && out[names[i]!] !== value) return {}
    out[names[i]!] = value
  }
  return out
}

/** The reply as the model wrote it: every restored value put back behind its placeholder. */
export function withPlaceholders(reply: string, values: Record<string, string>): string {
  let text = reply
  // Longest value first, so a value that contains another is replaced whole.
  for (const [name, value] of Object.entries(values).sort((a, b) => b[1].length - a[1].length)) {
    text = text.split(value).join(name)
  }
  return text
}

/** Split text into plain runs and placeholder tokens, for highlighting them. */
export function splitPlaceholders(text: string): { text: string; placeholder: boolean }[] {
  const out: { text: string; placeholder: boolean }[] = []
  let last = 0
  for (const m of text.matchAll(PLACEHOLDER)) {
    if (m.index! > last) out.push({ text: text.slice(last, m.index), placeholder: false })
    out.push({ text: m[0], placeholder: true })
    last = m.index! + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last), placeholder: false })
  return out
}
