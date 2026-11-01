// What this API lets the reader do, and why not when it does not: one source for every control
// that depends on a feature (phase-6 CONTRACT section 6). Private-only controls stay visible in
// public mode, disabled, with the reason as their tooltip; they are never hidden.
import type { Capabilities, ModelUnavailableReason, PackSummary, PublicModel } from '@/api/types'
import { fixtureChoices } from './packs'

export type FeatureName = 'artifact_edit' | 'uploads' | 'memory_review' | 'evals' | 'judge' | 'release' | 'disable_nodes' | 'diagnose' | 'byo_key_ui'

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
  // The API still takes a key in public mode (features.byo_key); the site does not offer it.
  byo_key_ui: 'Bring your own key arrives with accounts. Coming soon.',
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
  byo_key_ui: 'This API does not take a key of your own.',
}

export function featureState(capabilities: Capabilities | null | undefined, name: FeatureName): FeatureState {
  if (!capabilities) return { enabled: false, reason: 'Connecting to the API.' }
  if (capabilities.mode === 'public') return { enabled: false, reason: PRIVATE_ONLY[name] }
  // Uploads are not built in any mode yet.
  if (name === 'uploads') return { enabled: false, reason: NOT_OFFERED.uploads }
  // Older APIs leave disable_nodes out: absent means on (P2).
  const on =
    name === 'disable_nodes' ? capabilities.features.disable_nodes !== false : name === 'byo_key_ui' ? capabilities.features.byo_key === true : capabilities.features[name] === true
  return on ? { enabled: true, reason: null } : { enabled: false, reason: NOT_OFFERED[name] }
}

// ---------- the model picker (every mode) ----------

const HM = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' })

function resetDate(resetsAt: string | null | undefined): Date {
  const d = resetsAt ? new Date(resetsAt) : null
  if (d && Number.isFinite(d.getTime())) return d
  const next = new Date()
  next.setUTCHours(24, 0, 0, 0)
  return next
}

/** "00:00 UTC" from the budget's reset time (the next UTC midnight when unknown). */
export function resetTime(resetsAt: string | null | undefined): string {
  return `${HM.format(resetDate(resetsAt))} UTC`
}

export interface ClockOptions {
  /** The viewer's time zone (default: the browser's). */
  timeZone?: string
  /** The viewer's locale (default: the browser's). */
  locale?: string
}

/** The reset time on the viewer's clock ("7:00 PM"), or null when it reads the same as UTC. */
export function localResetTime(resetsAt: string | null | undefined, opts: ClockOptions = {}): string | null {
  const d = resetDate(resetsAt)
  try {
    const local = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: opts.timeZone }).format(d)
    if (local === HM.format(d)) return null
    return new Intl.DateTimeFormat(opts.locale, { hour: 'numeric', minute: '2-digit', timeZone: opts.timeZone }).format(d)
  } catch {
    return null
  }
}

/** "00:00 UTC (7:00 PM your time)", or "00:00 UTC" for a viewer on UTC. */
export function resetPhrase(resetsAt: string | null | undefined, opts: ClockOptions = {}): string {
  const local = localResetTime(resetsAt, opts)
  return local ? `${resetTime(resetsAt)} (${local} your time)` : resetTime(resetsAt)
}

/** How a reason is worded: whether this site offers a key of the reader's own, and the reset time. */
export interface ReasonContext extends ClockOptions {
  mode?: Capabilities['mode']
  /** The key button works here (private mode): the wording may suggest it. Never in public. */
  byo?: boolean
  resetsAt?: string | null
}

/** The tooltip of a model the picker cannot offer. */
export function modelReasonText(reason: ModelUnavailableReason | null, ctx: ReasonContext = {}): string {
  if (reason === 'budget_exhausted') {
    const back = `Today's shared Claude budget is used up. Back at ${resetPhrase(ctx.resetsAt, ctx)}.`
    return ctx.byo ? `${back} Bring your own key or use the fake model.` : `${back} Auto and the fake model still work.`
  }
  if (reason === 'provider_unconfigured') {
    if (ctx.byo) return 'No key is configured for this model. Bring your own key.'
    return ctx.mode === 'dev' ? "This model's provider is not configured on this server." : 'This model is not set up on this server.'
  }
  if (reason === 'provider_unhealthy') return "This model's provider is not answering right now. Try again in a minute or use another model."
  return 'This model is not available right now.'
}

/** A short form for the menu row under the model's label. */
export function modelReasonShort(reason: ModelUnavailableReason | null, ctx: ReasonContext = {}): string {
  if (reason === 'budget_exhausted') return `Back at ${resetPhrase(ctx.resetsAt, ctx)}`
  if (reason === 'provider_unconfigured') return ctx.byo ? 'Needs your key' : 'Not configured'
  if (reason === 'provider_unhealthy') return 'Not answering'
  return 'Unavailable'
}

export interface PickerModel extends PublicModel {
  /** The reason as the tooltip says it, null when the model can be picked. */
  reasonText: string | null
  /** The reason as the menu row says it, null when the model can be picked. */
  reasonShort: string | null
}

/** Whether the key button works here: dev and private mode with `features.byo_key`, never public. */
export function byoKeyOffered(capabilities: Capabilities | null | undefined): boolean {
  return featureState(capabilities, 'byo_key_ui').enabled
}

/** A dev-mode API from before P6 has no `public.models`: its configured providers' aliases. */
function legacyModels(capabilities: Capabilities): PublicModel[] {
  const seen = new Set<string>()
  const out: PublicModel[] = []
  for (const p of capabilities.providers) {
    for (const alias of p.models) {
      if (seen.has(alias)) continue
      seen.add(alias)
      out.push({ alias, label: alias, provider: p.name, default: !!p.default && p.configured && !out.some((m) => m.default), available: p.configured, reason: p.configured ? null : 'provider_unconfigured' })
    }
  }
  return out
}

/**
 * The models the picker lists after Auto: `capabilities.public.models` (every mode since P6;
 * the configured providers' aliases on an older dev API). Where the key button works, a key the
 * reader brought makes the hosted (non-fake) models available whatever the budget or site key.
 */
export function pickerModels(capabilities: Capabilities | null | undefined, byoKey: string | null, clock: ClockOptions = {}): PickerModel[] {
  if (!capabilities) return []
  const pub = capabilities.public
  const listed = pub && Array.isArray(pub.models) ? pub.models : capabilities.mode === 'dev' ? legacyModels(capabilities) : []
  const byo = byoKeyOffered(capabilities)
  const ctx: ReasonContext = { ...clock, mode: capabilities.mode, byo, resetsAt: pub?.budget?.resets_at }
  return listed.map((m) => {
    const unlocked =
      byo && !m.available && !!byoKey && m.provider !== 'fake' && (m.reason === 'provider_unconfigured' || (m.reason === 'budget_exhausted' && pub?.byo_key_bypasses_budget !== false))
    const available = m.available || unlocked
    return {
      ...m,
      available,
      reason: available ? null : m.reason,
      reasonText: available ? null : modelReasonText(m.reason, ctx),
      reasonShort: available ? null : modelReasonShort(m.reason, ctx),
    }
  })
}

/** What Auto resolves to right now: the alias the API flags `default` (it moves when the budget is spent), else the first that can answer. */
export function autoModel(models: PickerModel[]): PickerModel | null {
  return models.find((m) => m.default && m.available) ?? models.find((m) => m.available) ?? null
}

/** The reader's explicit pick while it can answer; null means Auto (the turn names no model). */
export function selectedModel(models: PickerModel[], picked: string | null): PickerModel | null {
  return picked ? (models.find((m) => m.alias === picked && m.available) ?? null) : null
}

/** The model the next turn runs on, as far as the UI can tell: the pick, else what Auto resolves to. */
export function effectiveModel(models: PickerModel[], picked: string | null): PickerModel | null {
  return selectedModel(models, picked) ?? autoModel(models)
}

/** The scripted model: the one a turn may name a fixture for (in public mode too). */
export function isScripted(model: PublicModel | null | undefined): boolean {
  return model?.provider === 'fake'
}

/** The fake model's fallback when the shared budget took the reader's Claude pick away. */
export function scriptedModel(models: PickerModel[]): PickerModel | null {
  return models.find((m) => isScripted(m) && m.available) ?? null
}

// ---------- the fixture select ----------

export interface FixtureCatalog {
  names: string[]
  descriptions: Record<string, string | null>
  /** Which packs ship each name (`public.fixtures[].packs`); null on an API that does not say. */
  packsOf: Record<string, string[]> | null
  /** What a fake-model turn without a fixture plays; null when the API does not say. */
  defaultName: string | null
}

/** The fixtures a turn may name: `public.fixtures` (every mode), else the dev-mode `fixtures`. */
export function fixtureCatalog(capabilities: Capabilities | null | undefined): FixtureCatalog {
  const listed = capabilities?.public?.fixtures
  const defaultName = capabilities?.public?.default_fixture ?? null
  if (Array.isArray(listed)) {
    const withPacks = listed.every((f) => Array.isArray(f.packs))
    return {
      names: listed.map((f) => f.name),
      descriptions: Object.fromEntries(listed.map((f) => [f.name, f.description ?? null])),
      packsOf: withPacks ? Object.fromEntries(listed.map((f) => [f.name, f.packs ?? []])) : null,
      defaultName,
    }
  }
  return { names: capabilities?.mode === 'dev' ? [...(capabilities.fixtures ?? [])] : [], descriptions: {}, packsOf: null, defaultName }
}

/** The select's groups for a session on `pack`: its own fixtures, then the shared ones. */
export function fixtureGroups(catalog: FixtureCatalog, packs: PackSummary[] | null | undefined, pack: string | null): { own: string[]; shared: string[]; all: string[] } {
  const owned = catalog.packsOf
  if (!owned) return fixtureChoices(packs, catalog.names, pack)
  const own = catalog.names.filter((n) => !!pack && owned[n]!.includes(pack))
  const shared = catalog.names.filter((n) => owned[n]!.length === 0)
  return { own, shared, all: [...own, ...shared] }
}

/**
 * Whether the Fixture select shows: the next turn runs on the scripted model (picked, or what
 * Auto resolves to) and the API takes a fixture for it. In dev mode a provider override decides:
 * only `fake` plays fixtures.
 */
export function fixtureVisible(capabilities: Capabilities | null | undefined, effective: PickerModel | null, provider: string | null): boolean {
  if (!capabilities) return false
  if (fixtureCatalog(capabilities).names.length === 0) return false
  if (capabilities.mode === 'dev' && provider) return provider === 'fake'
  if (capabilities.mode === 'private') return false
  return isScripted(effective)
}
