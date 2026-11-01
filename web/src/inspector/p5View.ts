// What the P5 panels say (a diagnosis layer, a gate result, a remember and recall pair), as plain
// functions so they are tested without a DOM.
import type { ReleaseGate } from '@/api/types'

const LAYER: Record<string, string> = {
  retrieval: 'retrieval',
  prompt_or_skill: 'prompt or skill',
  tool: 'tool',
  model: 'model',
  guardrail: 'guardrail',
  budget: 'budget',
  unknown: 'unknown layer',
}

export const LAYER_HINT: Record<string, string> = {
  retrieval: 'The stores or the knowledge index returned nothing useful for this turn.',
  prompt_or_skill: 'A prompt or skill changed since the last pass.',
  tool: 'A tool failed, timed out, or the tool file changed.',
  model: 'A model call failed (a provider error, or a fake fixture that no longer matches).',
  guardrail: 'A guardrail or policy blocked the turn, or the policy changed.',
  budget: 'The loop stopped on a limit: steps, tokens, cost or time.',
  unknown: 'No rule matched; the evidence lists what the trace shows.',
}

export function layerLabel(layer: string): string {
  return LAYER[layer] ?? layer.replace(/_/g, ' ')
}

/** Why a remember and recall pair must run in order (evaluator.md, `after`). */
export function afterNote(after: string): string {
  return `Runs after ${after}: it reads what that case remembered, so run alone it has nothing to recall.`
}

const GATE_REASON: Record<string, string> = {
  disabled: 'the gate is off (release.gate.enabled)',
  pack_not_gated: 'this pack is not in release.gate.packs',
  no_cases: 'the golden set has no case to run',
  forced: 'released anyway, without the gate; the audit log records it as release_forced',
  startup: 'made by the harness at start-up',
  below_min_pass_rate: 'the pass rate is below the minimum',
}

/** One sentence on a gate result: what ran and why it passed, failed or was skipped. */
export function gateText(gate: ReleaseGate): string {
  const rate = typeof gate.pass_rate === 'number' ? `pass rate ${gate.pass_rate}${typeof gate.min_pass_rate === 'number' ? `, needs ${gate.min_pass_rate}` : ''}` : ''
  const why = gate.reason ? (GATE_REASON[gate.reason] ?? gate.reason.replace(/_/g, ' ')) : ''
  const runs = gate.run_ids?.length ? `run ${gate.run_ids.join(', ')}` : ''
  const head = gate.forced ? 'Gate skipped' : `Gate ${gate.status}`
  const suite = gate.suite && !gate.forced && gate.status !== 'skipped' ? ` on the ${gate.suite} suite` : ''
  return `${head}${suite}${why ? `: ${why}` : ''}${rate ? ` (${rate})` : ''}${runs ? `, ${runs}` : ''}`
}

