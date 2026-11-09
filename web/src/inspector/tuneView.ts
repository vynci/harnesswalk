// What the Tune panel says (0.2.0, spec/contracts/tuning.md), as plain functions so they are
// tested without a DOM: the comparison table (pack x arm), the tool_calls split per arm, the
// loss series of a run from its tune_step events, the canary rule in words, why an action is
// off (public mode, a recorded replay, a job running), and which node shows the Tune panel.
// Nothing here names a node id: the tune node is found by the artifact kinds it writes.
import type {
  CanaryRule,
  CanaryState,
  Capabilities,
  CompareArm,
  Comparison,
  ComparisonCell,
  Diagram,
  DiagramNode,
  OpsEvent,
  TuneArtifactKind,
  TuneRun,
  TuneStep,
} from '@/api/types'

export const ARMS: CompareArm[] = ['base', 'candidate', 'prompt_arm']

export const ARM_LABELS: Record<CompareArm, string> = { base: 'Base', candidate: 'Tuned', prompt_arm: 'Prompt arm' }

/** What each arm answers with, for the column header's tooltip. */
export const ARM_HINTS: Record<CompareArm, string> = {
  base: 'The base model on the working set',
  candidate: 'The tuned candidate model on the same working set',
  prompt_arm: "The base model with the control arm's text added to the system prompt",
}

/** A number without trailing zeros: 13, 12.5, 0.36. */
export function num(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return '·'
  return String(Number(x.toFixed(digits)))
}

// ---------- which node shows what ----------

const TUNE_KIND = /\b(dataset|adapter|model)\/</g

/** The tune artifact kinds a node's `artifact_hint` names (`dataset/<name>`, `model/<name>`). */
export function tuneKinds(node: Pick<DiagramNode, 'artifact_hint'> | null | undefined): TuneArtifactKind[] {
  const hint = node?.artifact_hint ?? ''
  return [...new Set([...hint.matchAll(TUNE_KIND)].map((m) => m[1] as TuneArtifactKind))]
}

/**
 * The part a tune node plays, from what it writes: `trains` for the node that writes adapters
 * and models (it opens the Release panel's Tune tab), `curates` for one that writes datasets
 * only (it opens the Eval panel with a way to the datasets), null for any other node.
 */
export function tunePart(node: Pick<DiagramNode, 'artifact_hint'> | null | undefined): 'trains' | 'curates' | null {
  const kinds = tuneKinds(node)
  if (kinds.includes('adapter') || kinds.includes('model')) return 'trains'
  if (kinds.includes('dataset')) return 'curates'
  return null
}

/** The node that shows the Tune tab (the one writing models), for the curate node's link. */
export function tuneNodeId(diagram: Diagram | null | undefined): string | null {
  return diagram?.nodes.find((n) => tunePart(n) === 'trains')?.id ?? null
}

// ---------- actions ----------

/**
 * Why the tune actions (curate, train, compare, promote, roll back) are off, or null when they
 * are on. A public server never tunes (every POST is 403 there) and answers from a recorded run.
 */
export function tuneBlockReason(capabilities: Capabilities | null | undefined, replay: boolean, running?: { id: string; kind: string } | null): string | null {
  if (replay || capabilities?.mode === 'public') return 'The public sandbox never trains: this is a recorded run of the fake trainer, replayed. Run the loop locally with the CLI or the dev server.'
  if (running) return `A ${running.kind} (${running.id}) is running: one tuning job at a time.`
  return null
}

/** What a recorded replay says, above the panel. */
export const REPLAY_NOTE =
  'Recorded run. The sandbox never curates, trains, compares or promotes: everything here was recorded offline on the fake trainer and is replayed as it happened. The actions are off.'

// ---------- comparisons ----------

export interface CompareCellView {
  arm: CompareArm
  cell: ComparisonCell | null
  /** "13/24", with "(12-14)" when the samples differ. */
  text: string
  /** The arm's mean minus the base's, null for the base itself or a missing cell. */
  delta: number | null
}

export interface CompareRow {
  pack: string
  role: 'target' | 'canary'
  cells: CompareCellView[]
  /** A canary pack that lost more than the tolerance on the candidate. */
  regressed: boolean
}

/** The arms a comparison measured, in table order. */
export function comparisonArms(c: Pick<Comparison, 'cells'>): CompareArm[] {
  return ARMS.filter((a) => c.cells && c.cells[a])
}

export function cellText(cell: ComparisonCell | null | undefined): string {
  if (!cell) return 'n/a'
  const spread = cell.min !== cell.max ? ` (${num(cell.min)}-${num(cell.max)})` : ''
  return `${num(cell.passed)}/${num(cell.scored)}${spread}`
}

/** The pack x arm table, target packs first, as `render_comparison` reads it. */
export function compareRows(c: Pick<Comparison, 'cells' | 'request' | 'verdict'>): CompareRow[] {
  const arms = comparisonArms(c)
  const regressed = new Set(c.verdict?.regressed ?? [])
  const packs = [...c.request.packs.map((p) => [p, 'target'] as const), ...c.request.canary_packs.map((p) => [p, 'canary'] as const)]
  return packs.map(([pack, role]) => {
    const base = c.cells?.base?.[pack] ?? null
    return {
      pack,
      role,
      regressed: role === 'canary' && regressed.has(pack),
      cells: arms.map((arm) => {
        const cell = c.cells?.[arm]?.[pack] ?? null
        return { arm, cell, text: cellText(cell), delta: arm === 'base' || !cell || !base ? null : cell.passed - base.passed }
      }),
    }
  })
}

export interface ToolSplitRow {
  bucket: string
  values: Partial<Record<CompareArm, number>>
}

/** The failed cases' tool_calls buckets per arm, summed over the target packs. */
export function toolSplitRows(c: Pick<Comparison, 'cells' | 'request'>): ToolSplitRow[] {
  const arms = comparisonArms(c)
  const buckets = new Set<string>()
  for (const arm of arms) for (const p of c.request.packs) for (const k of Object.keys(c.cells?.[arm]?.[p]?.tool_split ?? {})) buckets.add(k)
  return [...buckets].sort().map((bucket) => ({
    bucket,
    values: Object.fromEntries(arms.map((arm) => [arm, Number(c.request.packs.reduce((sum, p) => sum + (c.cells?.[arm]?.[p]?.tool_split?.[bucket] ?? 0), 0).toFixed(2))])),
  }))
}

/** The verdict in one sentence: passed or failed, why, the gain against what it needed. */
export function verdictText(c: Pick<Comparison, 'verdict'>): string {
  const v = c.verdict
  if (!v) return 'No verdict yet.'
  const drops = Object.entries(v.canary_drop ?? {})
    .map(([p, d]) => `${p} ${num(d)}`)
    .join(', ')
  const head = v.status === 'passed' ? 'Gate passed' : `Gate failed${v.reason ? ` (${v.reason})` : ''}`
  return `${head}. Gain on the target packs ${num(v.gain)} cases (needs ${num(v.min_gain_cases)}); canary drop ${drops || 'none'} (at most ${num(v.canary_tolerance_cases)} each).`
}

/** The control arm's line: what the prompt alone gained, and whether it would pass the gate. */
export function promptArmText(c: Pick<Comparison, 'verdict'>): string | null {
  const pa = c.verdict?.prompt_arm
  if (!pa) return null
  return `Prompt arm: gain ${num(pa.gain)}, canary ${pa.canary_ok ? 'ok' : 'regressed'}; it ${pa.would_pass ? 'would' : 'would not'} pass the same gate.`
}

/** "model/approvals_fake@v1". */
export function refText(kind: string, ref: { name: string; version: number } | null | undefined): string {
  return ref ? `${kind}/${ref.name}@v${ref.version}` : '·'
}

// ---------- runs ----------

export interface LossPoint {
  step: number
  loss: number
}

export interface LossSeries {
  train: LossPoint[]
  eval: LossPoint[]
  total: number | null
  last: TuneStep | null
  /** A phase after training (merge, convert, quantize, create), from the last step. */
  phase: string | null
}

const finite = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x)

/**
 * The loss curve of a run from its tune_step summaries (the record's `steps`, then its live
 * stream): one train point per step, the validation points where measured, the total, and the
 * worker's later phase (merge, convert, quantize, create; reported at the last step) if any.
 */
export function lossSeries(steps: TuneStep[]): LossSeries {
  const train = new Map<number, number>()
  const evalLoss = new Map<number, number>()
  let last: TuneStep | null = null
  for (const s of steps) {
    if (!finite(s.step)) continue
    if ((s.phase ?? 'train') === 'train' && finite(s.loss)) train.set(s.step, s.loss)
    if (finite(s.eval_loss)) evalLoss.set(s.step, s.eval_loss)
    if (!last || s.step >= last.step) last = s
  }
  const points = (m: Map<number, number>) => [...m.entries()].sort((a, b) => a[0] - b[0]).map(([step, loss]) => ({ step, loss }))
  const total = steps.reduce<number | null>((t, s) => (finite(s.total_steps) ? s.total_steps : t), null)
  return { train: points(train), eval: points(evalLoss), total, last, phase: last?.phase && last.phase !== 'train' ? last.phase : null }
}

/** The tune_step summaries of a stream's events. */
export function stepsOf(events: OpsEvent[]): TuneStep[] {
  return events.filter((e) => e.type === 'tune_step' && e.summary).map((e) => e.summary as unknown as TuneStep)
}

/** A run's steps: the record's, then whatever its live stream added. */
export function runSteps(run: Pick<TuneRun, 'steps' | 'events'> | null | undefined, live: OpsEvent[] = []): TuneStep[] {
  return [...(run?.steps ?? []), ...stepsOf(run?.events ?? []), ...stepsOf(live)]
}

export interface ChartGeometry {
  width: number
  height: number
  pad: { left: number; right: number; top: number; bottom: number }
  xMax: number
  yMin: number
  yMax: number
  /** SVG path of the train loss. */
  path: string
  /** Circles for the validation loss. */
  dots: { cx: number; cy: number; step: number; loss: number }[]
  yTicks: { y: number; label: string }[]
}

/** The SVG geometry of a loss chart: x is the step (to the total), y the loss from 0. */
export function chartGeometry(series: LossSeries, width = 320, height = 140): ChartGeometry {
  const pad = { left: 34, right: 8, top: 8, bottom: 20 }
  const all = [...series.train, ...series.eval]
  const xMax = Math.max(1, series.total ?? 0, ...all.map((p) => p.step))
  const yMaxRaw = Math.max(0, ...all.map((p) => p.loss))
  const yMax = yMaxRaw > 0 ? Number((yMaxRaw * 1.1).toPrecision(2)) : 1
  const yMin = 0
  const w = width - pad.left - pad.right
  const h = height - pad.top - pad.bottom
  const x = (step: number) => pad.left + (step / xMax) * w
  const y = (loss: number) => pad.top + h - ((loss - yMin) / (yMax - yMin)) * h
  const path = series.train.map((p, i) => `${i ? 'L' : 'M'}${x(p.step).toFixed(1)},${y(p.loss).toFixed(1)}`).join(' ')
  const dots = series.eval.map((p) => ({ cx: Number(x(p.step).toFixed(1)), cy: Number(y(p.loss).toFixed(1)), step: p.step, loss: p.loss }))
  const yTicks = [0, yMax / 2, yMax].map((v) => ({ y: Number(y(v).toFixed(1)), label: num(v) }))
  return { width, height, pad, xMax, yMin, yMax, path, dots, yTicks }
}

/** "step 34 of 34, loss 0.36, eval 0.42". */
export function stepText(s: TuneStep | null | undefined): string {
  if (!s) return 'no steps yet'
  const parts = [`step ${s.step}${s.total_steps ? ` of ${s.total_steps}` : ''}`]
  if (finite(s.loss)) parts.push(`loss ${num(s.loss, 4)}`)
  if (finite(s.eval_loss)) parts.push(`eval ${num(s.eval_loss, 4)}`)
  if (s.phase && s.phase !== 'train') parts.push(s.phase)
  if (finite(s.eta_s) && s.eta_s > 0) parts.push(`about ${Math.round(s.eta_s)} s left`)
  return parts.join(', ')
}

// ---------- the canary ----------

/** The packs a rule's `when.pack` names, as a list. */
export function rulePacks(rule: Pick<CanaryRule, 'when'> | null | undefined): string[] {
  const p = rule?.when?.pack
  return Array.isArray(p) ? p.map(String) : typeof p === 'string' ? [p] : []
}

/** "50% of ecommerce, logistics to tuned-approvals-fake, the rest to fake-student". */
export function canaryRuleText(rule: CanaryRule | null | undefined): string {
  if (!rule?.split) return 'No canary rule is set: every turn goes to the models the rules name.'
  const { base_alias, candidate_alias, fraction } = rule.split
  const packs = rulePacks(rule)
  const where = packs.length ? packs.join(', ') : 'every pack'
  const pct = `${num(fraction * 100, 1)}%`
  if (fraction <= 0) return `Rolled back: no turn of ${where} goes to ${candidate_alias}; every turn goes to ${base_alias}.`
  if (fraction >= 1) return `Full: every turn of ${where} goes to ${candidate_alias} (${base_alias} answers only as its fallback).`
  return `${pct} of the turns of ${where} go to ${candidate_alias}, the rest to ${base_alias}.`
}

/** "2 of 6 failed (33%), 2 thumbs down", or "no turns yet". */
export function armText(arm: CanaryState['arms']['base'] | null | undefined): string {
  if (!arm || arm.turns === 0) return 'no turns yet'
  const rate = arm.failure_rate === null ? '' : ` (${num(arm.failure_rate * 100, 1)}%)`
  return `${arm.failed} of ${arm.turns} failed${rate}, ${arm.bad_feedback} thumbs down`
}

/** A canary fraction from the form: a number in [0, 1], else null. */
export function parseFraction(text: string): number | null {
  const t = text.trim()
  if (!t) return null
  const v = t.endsWith('%') ? Number(t.slice(0, -1)) / 100 : Number(t)
  return Number.isFinite(v) && v >= 0 && v <= 1 ? v : null
}

// ---------- ops events in the inspector ----------

/** One line for an ops event in a node's inspector: what happened, in a few words. */
export function opsEventText(ev: OpsEvent): string {
  const s = (ev.summary ?? {}) as Record<string, unknown>
  const str = (k: string) => (typeof s[k] === 'string' ? (s[k] as string) : undefined)
  switch (ev.type) {
    case 'tune_run_started':
      return `run ${str('run_id') ?? ''} started: ${str('trainer') ?? '?'} on ${str('dataset') ?? '?'}, student ${str('student') ?? '?'}`
    case 'tune_step':
      return stepText(s as unknown as TuneStep)
    case 'tune_run_finished':
      return `run ${str('run_id') ?? ''} ${str('status') ?? ev.status ?? ''}${str('model') ? `: ${str('model')}` : ''}${str('error_code') ? ` (${str('error_code')})` : ''}`
    case 'eval_run_started':
      return `${str('purpose') ?? 'eval run'} ${str('run_id') ?? ''} started${typeof s.total === 'number' ? `, ${s.total} cases` : ''}`
    case 'eval_case_finished':
      return `${str('case_id') ?? 'case'} ${str('status') ?? ''}${s.kept === true ? ', kept' : s.kept === false ? ', dropped' : ''}`
    case 'eval_run_finished':
      return `${str('run_id') ?? 'run'} finished${typeof s.kept === 'number' ? `, ${s.kept} kept` : ''}${str('dataset') ? `: ${str('dataset')}` : ''}`
    case 'release_gated':
      return `${str('suite') ?? 'golden'} gate ${str('gate') ?? str('status') ?? ev.status ?? ''}${str('candidate') ? ` for ${str('candidate')}` : ''}${str('reason') ? ` (${str('reason')})` : ''}`
    default:
      return ev.type
  }
}

/**
 * The latest ops events of a node and its children, newest first, with runs of tune_step
 * events folded into their last one so a long run takes one line.
 */
export function latestOpsEvents(log: OpsEvent[], ids: ReadonlySet<string>, limit = 8): OpsEvent[] {
  const mine = log.filter((e) => e.node_id && ids.has(e.node_id))
  const out: OpsEvent[] = []
  for (let i = mine.length - 1; i >= 0 && out.length < limit; i--) {
    const ev = mine[i]!
    const prev = out[out.length - 1]
    if (ev.type === 'tune_step' && prev?.type === 'tune_step' && prev.summary?.run_id === ev.summary?.run_id) continue
    out.push(ev)
  }
  return out
}
