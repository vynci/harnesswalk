// The router's rules in words (router.md), found by shape: a list of rules where each one has
// `use` (the aliases to try in order) or, since 0.2.0, `split` (the canary rule: a share of the
// matching turns to a candidate alias, the rest to the base). A split rule has no `use`, so
// nothing here reads `use` without checking it.
import { isRecord } from './shapes'

export interface RuleView {
  id: string
  /** `step: summarize, pack: ecommerce, logistics`, or `always`. */
  when: string
  /** `local-8b, hosted-haiku`, or `50% to tuned-x, else base-y`. */
  target: string
  split: { base_alias: string; candidate_alias: string; fraction: number } | null
}

function whenText(when: unknown): string {
  if (!isRecord(when) || Object.keys(when).length === 0) return 'always'
  return Object.entries(when)
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : String(v)}`)
    .join('; ')
}

function pct(fraction: number): string {
  return `${Number((fraction * 100).toFixed(1))}%`
}

/** One rule as a line, or null when it is not a router rule (neither `use` nor `split`). */
export function ruleView(rule: unknown, index = 0): RuleView | null {
  if (!isRecord(rule)) return null
  const id = typeof rule.id === 'string' ? rule.id : typeof rule.name === 'string' ? rule.name : `rule ${index + 1}`
  const split = isRecord(rule.split) ? rule.split : null
  if (split) {
    const base = String(split.base_alias ?? '?')
    const cand = String(split.candidate_alias ?? '?')
    const fraction = typeof split.fraction === 'number' && Number.isFinite(split.fraction) ? split.fraction : 0
    return { id, when: whenText(rule.when), target: `${pct(fraction)} to ${cand}, the rest to ${base}`, split: { base_alias: base, candidate_alias: cand, fraction } }
  }
  if (rule.use === undefined) return null
  const use = Array.isArray(rule.use) ? rule.use.map(String).join(', ') : String(rule.use)
  return { id, when: whenText(rule.when), target: use || 'none', split: null }
}

/**
 * The router rules of an effective config (`inspector.effective` of a config node), in the
 * order the router reads them, with `canary` where the router evaluates it when the config
 * keeps it beside the list (`router.canary`). Empty when the value holds no router rules.
 */
export function routerRules(effective: unknown): RuleView[] {
  if (!isRecord(effective) || !Array.isArray(effective.rules)) return []
  const rules = effective.rules
  if (rules.length === 0 || !rules.every((r) => isRecord(r) && ('use' in r || 'split' in r))) return []
  const out = rules.map((r, i) => ruleView(r, i)).filter((r): r is RuleView => r !== null)
  const canary = ruleView(effective.canary, out.length)
  if (canary && !out.some((r) => r.split)) {
    // After the public rule, or first when there is none (router.md, "0.2.0: the canary rule").
    const at = out.findIndex((r) => r.when.startsWith('mode: public'))
    out.splice(at + 1, 0, { ...canary, id: canary.id.startsWith('rule ') ? 'canary' : canary.id })
  }
  return out
}

/** The router span's arm and bucket (a call the canary routed), as "candidate arm, bucket 0.4649". */
export function armBucketText(summary: Record<string, unknown> | null | undefined): string | null {
  if (!summary || typeof summary.arm !== 'string') return null
  const bucket = typeof summary.bucket === 'number' ? `, bucket ${summary.bucket}` : ''
  return `${summary.arm} arm${bucket}`
}
