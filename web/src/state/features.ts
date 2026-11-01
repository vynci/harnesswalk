// What this API lets the reader do, and why not when it does not: one source for every control
// that depends on a feature (phase-6 CONTRACT section 6). Private-only controls stay visible in
// public mode, disabled, with the reason as their tooltip; they are never hidden.
import type { Capabilities, ModelUnavailableReason, PublicModel } from '@/api/types'

export type FeatureName = 'artifact_edit' | 'uploads' | 'memory_review' | 'evals' | 'judge' | 'release' | 'disable_nodes' | 'diagnose'

export interface FeatureState {
  enabled: boolean
  /** Why the control is disabled, for its tooltip; null when it is enabled. */
  reason: string | null
}

/** The tooltip of a private-only control in public mode. */
export const PRIVATE_ONLY: Record<FeatureName, string> = {
  artifact_edit: 'Editing needs a private workspace. Coming later.',
  uploads: 'Uploads need a private workspace.',
  memory_review: 'Reviewing memory needs a private workspace. Coming later.',
  evals: 'Eval runs need a private workspace. Coming later.',
  judge: 'The judge suite needs a private workspace. Coming later.',
  release: 'Releases and rollbacks need a private workspace. Coming later.',
  disable_nodes: 'Turning nodes off needs a private workspace. Coming later.',
  diagnose: 'Diagnosis needs a private workspace. Coming later.',
}

/** The reason in dev and private mode, when the API simply does not offer the feature. */
const NOT_OFFERED: Record<FeatureName, string> = {
  artifact_edit: 'This API does not offer artifact editing.',
  uploads: 'Attach, coming later',
  memory_review: 'Review is off in this mode: nothing here can be accepted.',
  evals: 'This API does not offer eval runs.',
  judge: 'The judge suite needs a judge model (evals.judge.model); this API has none.',
  release: 'This API does not offer releases.',
  disable_nodes: 'This API does not let a turn turn nodes off.',
  diagnose: 'This API does not offer diagnosis.',
}

export function featureState(capabilities: Capabilities | null | undefined, name: FeatureName): FeatureState {
  if (!capabilities) return { enabled: false, reason: 'Connecting to the API.' }
  if (capabilities.mode === 'public') return { enabled: false, reason: PRIVATE_ONLY[name] }
  // Uploads are not built in any mode yet.
  if (name === 'uploads') return { enabled: false, reason: NOT_OFFERED.uploads }
  // Older APIs leave disable_nodes out: absent means on (P2).
  const on = name === 'disable_nodes' ? capabilities.features.disable_nodes !== false : capabilities.features[name] === true
  return on ? { enabled: true, reason: null } : { enabled: false, reason: NOT_OFFERED[name] }
}

// ---------- the public model picker ----------

/** "00:00 UTC" from the budget's reset time (the next UTC midnight when unknown). */
export function resetTime(resetsAt: string | null | undefined): string {
  const d = resetsAt ? new Date(resetsAt) : null
  if (!d || !Number.isFinite(d.getTime())) return '00:00 UTC'
  return `${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')} UTC`
}

/** The tooltip of a model the picker cannot offer. */
export function modelReasonText(reason: ModelUnavailableReason | null, resetsAt?: string | null): string {
  if (reason === 'budget_exhausted') return `Today's shared budget for Claude models is spent. It resets at ${resetTime(resetsAt)}. Bring your own key or use the fake model.`
  if (reason === 'provider_unconfigured') return 'No key is configured for this model. Bring your own key.'
  return 'This model is not available right now.'
}

/** A short form for the menu row under the model's label. */
export function modelReasonShort(reason: ModelUnavailableReason | null): string {
  if (reason === 'budget_exhausted') return "Today's budget is spent"
  if (reason === 'provider_unconfigured') return 'Needs your key'
  return 'Unavailable'
}

export interface PickerModel extends PublicModel {
  /** The reason as the tooltip says it, null when the model can be picked. */
  reasonText: string | null
}

/**
 * The models the composer lists, from `capabilities.public.models`. A key the reader brought
 * makes the hosted (non-fake) models available whatever the shared budget or the site key say.
 */
export function pickerModels(capabilities: Capabilities | null | undefined, byoKey: string | null): PickerModel[] {
  const pub = capabilities?.public
  if (!pub || !Array.isArray(pub.models)) return []
  return pub.models.map((m) => {
    const unlocked = !m.available && !!byoKey && m.provider !== 'fake' && (m.reason === 'provider_unconfigured' || (m.reason === 'budget_exhausted' && pub.byo_key_bypasses_budget !== false))
    const available = m.available || unlocked
    return { ...m, available, reason: available ? null : m.reason, reasonText: available ? null : modelReasonText(m.reason, pub.budget?.resets_at) }
  })
}

/**
 * The model the next public turn asks for: the reader's pick while it is available, else the
 * default, else the first available one. Null when the API sends no public model list (an API
 * from before P6), so the turn names no model.
 */
export function effectiveModel(models: PickerModel[], chosen: string | null): PickerModel | null {
  if (models.length === 0) return null
  const pick = chosen ? models.find((m) => m.alias === chosen && m.available) : undefined
  return pick ?? models.find((m) => m.default && m.available) ?? models.find((m) => m.available) ?? null
}
