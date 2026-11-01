import { describe, expect, it } from 'vitest'
import type { Capabilities, PublicInfo } from '@/api/types'
import {
  autoModel,
  byoKeyOffered,
  effectiveModel,
  featureState,
  fixtureCatalog,
  fixtureGroups,
  fixtureVisible,
  localResetTime,
  modelReasonShort,
  modelReasonText,
  pickerModels,
  PRIVATE_ONLY,
  resetPhrase,
  resetTime,
  scriptedModel,
  selectedModel,
} from './features'

const PUBLIC: PublicInfo = {
  session_expires_at: '2026-11-01T18:04:11Z',
  ttl_s: 7200,
  limits: { per_ip_per_hour: 30, remaining_this_hour: 27, max_steps: 8, budget_tokens: 16000, max_message_chars: 8000 },
  models: [
    { alias: 'hosted-haiku', label: 'Claude Haiku 5.5', provider: 'anthropic', default: true, available: true, reason: null },
    { alias: 'hosted-sonnet', label: 'Claude Sonnet 5.5', provider: 'anthropic', default: false, available: false, reason: 'budget_exhausted' },
    { alias: 'fake-small', label: 'Fake model (scripted)', provider: 'fake', default: false, available: true, reason: null },
  ],
  budget: { status: 'ok', resets_at: '2026-11-02T00:00:00Z', spent_fraction: 0.42 },
  byo_key_bypasses_budget: true,
}

function caps(mode: Capabilities['mode'] = 'dev', features: Partial<Capabilities['features']> = {}, pub?: PublicInfo): Capabilities {
  return {
    mode,
    version: 't',
    providers: [],
    fixtures: [],
    features: { step_mode: true, approvals: true, message_delta: true, cancel: true, byo_key: mode !== 'dev', artifact_edit: true, release: true, evals: true, memory_review: true, diagnose: true, judge: true, ...features },
    limits: { max_message_chars: 8000, max_steps: 8, session_ttl_seconds: null },
    ...(pub ? { public: pub } : {}),
  }
}

describe('featureState', () => {
  it('disables every private-only control in public mode with the contract text', () => {
    const c = caps('public', { artifact_edit: false, evals: false, memory_review: false, disable_nodes: false, diagnose: false, judge: false })
    for (const name of Object.keys(PRIVATE_ONLY) as (keyof typeof PRIVATE_ONLY)[]) {
      expect(featureState(c, name)).toEqual({ enabled: false, reason: PRIVATE_ONLY[name] })
    }
    expect(featureState(c, 'artifact_edit').reason).toBe('Editing needs a private workspace. Coming later.')
    expect(featureState(c, 'uploads').reason).toBe('Uploads need a private workspace.')
    // features.release stays true in public (the list is read-only there), the controls do not.
    expect(featureState(caps('public', { release: true }), 'release').enabled).toBe(false)
  })

  it('follows the feature flags in dev, and an absent disable_nodes means on', () => {
    expect(featureState(caps(), 'evals')).toEqual({ enabled: true, reason: null })
    expect(featureState(caps('dev', { evals: false }), 'evals').enabled).toBe(false)
    expect(featureState(caps('dev', { disable_nodes: undefined }), 'disable_nodes').enabled).toBe(true)
    expect(featureState(caps(), 'uploads')).toEqual({ enabled: false, reason: 'Attach, coming later' })
    expect(featureState(null, 'evals').reason).toBe('Connecting to the API.')
  })
})

describe('the model picker', () => {
  const SPENT: PublicInfo = {
    ...PUBLIC,
    budget: { status: 'exhausted', resets_at: '2026-11-02T00:00:00Z', spent_fraction: 1 },
    // The API's `default` moves to what Auto runs on now: the fake model.
    models: PUBLIC.models.map((m) => (m.provider === 'anthropic' ? { ...m, default: false, available: false, reason: 'budget_exhausted' as const } : { ...m, default: true })),
  }

  it('Auto is the default pick: no model named, and it resolves to the alias the API flags default', () => {
    const models = pickerModels(caps('public', {}, PUBLIC), null)
    expect(models.map((m) => [m.alias, m.available])).toEqual([
      ['hosted-haiku', true],
      ['hosted-sonnet', false],
      ['fake-small', true],
    ])
    expect(selectedModel(models, null)).toBeNull()
    expect(autoModel(models)?.label).toBe('Claude Haiku 5.5')
    expect(effectiveModel(models, null)?.alias).toBe('hosted-haiku')
    expect(selectedModel(models, 'fake-small')?.alias).toBe('fake-small')
    // A pick that became unavailable is Auto again.
    expect(selectedModel(models, 'hosted-sonnet')).toBeNull()
    expect(effectiveModel(models, 'hosted-sonnet')?.alias).toBe('hosted-haiku')
  })

  it('with the budget spent the Claude models say when they are back, and Auto resolves to the fake model', () => {
    const models = pickerModels(caps('public', {}, SPENT), null, { timeZone: 'America/New_York', locale: 'en-US' })
    expect(models.filter((m) => m.available).map((m) => m.alias)).toEqual(['fake-small'])
    expect(autoModel(models)?.label).toBe('Fake model (scripted)')
    const haiku = models[0]!
    expect(haiku.reasonShort).toBe('Back at 00:00 UTC (7:00 PM your time)')
    expect(haiku.reasonText).toBe("Today's shared Claude budget is used up. Back at 00:00 UTC (7:00 PM your time). Auto and the fake model still work.")
    expect(scriptedModel(models)?.alias).toBe('fake-small')
  })

  it('public mode never offers a key: no unlock, no key wording; private mode does', () => {
    const unconfigured: PublicInfo = { ...PUBLIC, models: [{ ...PUBLIC.models[0]!, available: false, reason: 'provider_unconfigured' }] }
    const pub = pickerModels(caps('public', {}, unconfigured), 'sk-ant-test')
    expect(pub[0]!.available).toBe(false)
    expect(pub[0]!.reasonText).toBe('This model is not set up on this server.')
    expect(pub[0]!.reasonShort).toBe('Not configured')
    for (const m of [...pub, ...pickerModels(caps('public', {}, SPENT), 'sk-ant-test')]) expect(`${m.reasonText} ${m.reasonShort}`).not.toMatch(/key/i)
    expect(byoKeyOffered(caps('public', { byo_key: true }))).toBe(false)
    expect(featureState(caps('public', { byo_key: true }), 'byo_key_ui')).toEqual({ enabled: false, reason: 'Bring your own key arrives with accounts. Coming soon.' })
    // Private mode: the key button works, and a key unlocks the hosted models.
    expect(byoKeyOffered(caps('private'))).toBe(true)
    const priv = pickerModels(caps('private', {}, unconfigured), null)
    expect(priv[0]!.reasonText).toBe('No key is configured for this model. Bring your own key.')
    expect(pickerModels(caps('private', {}, unconfigured), 'k')[0]!.available).toBe(true)
    expect(pickerModels(caps('private', {}, SPENT), 'sk-ant-test').every((m) => m.available && m.reasonText === null)).toBe(true)
    // Dev mode has no key button (features.byo_key is false there).
    expect(featureState(caps('dev'), 'byo_key_ui').enabled).toBe(false)
  })

  it('a provider marked down stays unavailable with its own reason', () => {
    const down: PublicInfo = { ...PUBLIC, models: PUBLIC.models.map((m) => (m.alias === 'hosted-sonnet' ? { ...m, available: false, reason: 'provider_unhealthy' as const } : m)) }
    for (const key of [null, 'sk-ant-test']) {
      const sonnet = pickerModels(caps('private', {}, down), key).find((m) => m.alias === 'hosted-sonnet')!
      expect(sonnet.available).toBe(false)
      expect(sonnet.reason).toBe('provider_unhealthy')
      expect(sonnet.reasonText).toBe("This model's provider is not answering right now. Try again in a minute or use another model.")
    }
    expect(modelReasonShort('provider_unhealthy')).toBe('Not answering')
  })

  it('dev mode lists the API models; an older dev API without public.models lists its providers', () => {
    const dev = pickerModels(caps('dev', {}, { ...PUBLIC, models: [{ alias: 'local-8b', label: 'local-8b', provider: 'ollama', default: false, available: false, reason: 'provider_unconfigured' }] }), null)
    expect(dev[0]!.reasonText).toBe("This model's provider is not configured on this server.")
    const legacy = pickerModels({ ...caps('dev'), providers: [{ name: 'fake', configured: true, default: true, models: ['fake-small'] }, { name: 'ollama', configured: false, models: ['llama3.1:8b'] }] }, null)
    expect(legacy.map((m) => [m.alias, m.available, m.default])).toEqual([
      ['fake-small', true, true],
      ['llama3.1:8b', false, false],
    ])
    expect(autoModel(legacy)?.alias).toBe('fake-small')
    // Public mode on an API from before P6: no picker, so turns name no model.
    expect(pickerModels(caps('public'), null)).toEqual([])
    expect(effectiveModel([], null)).toBeNull()
  })

  it('names the reset time in UTC, and on the viewer clock when it differs', () => {
    expect(resetTime('2026-11-02T00:00:00Z')).toBe('00:00 UTC')
    expect(resetTime('2026-11-02T06:30:00Z')).toBe('06:30 UTC')
    expect(resetTime(null)).toBe('00:00 UTC')
    expect(resetPhrase('2026-11-02T00:00:00Z', { timeZone: 'UTC', locale: 'en-US' })).toBe('00:00 UTC')
    expect(resetPhrase('2026-11-02T00:00:00Z', { timeZone: 'Asia/Manila', locale: 'en-US' })).toBe('00:00 UTC (8:00 AM your time)')
    expect(localResetTime('2026-11-02T00:00:00Z', { timeZone: 'Europe/London', locale: 'en-US' })).toBeNull()
    expect(modelReasonText('budget_exhausted', { resetsAt: '2026-11-02T06:30:00Z', timeZone: 'UTC' })).toContain('Back at 06:30 UTC.')
  })
})

describe('the fixture select', () => {
  const FIXTURES: PublicInfo = {
    ...PUBLIC,
    fixtures: [
      { name: 'cite_mgmt_fee_hcp01', description: null, packs: ['finance'] },
      { name: 'plain_answer', description: 'A plain question answered from the system prompt, no tool.', packs: [] },
      { name: 'public_demo', description: 'The scripted model a public server offers.', packs: [] },
      { name: 'tool_call', description: null, packs: ['_starter'] },
    ],
    default_fixture: 'public_demo',
  }

  it('shows while the next turn runs on the scripted model: picked, or what Auto resolves to', () => {
    const c = caps('public', {}, FIXTURES)
    const models = pickerModels(c, null)
    expect(fixtureVisible(c, effectiveModel(models, null), null)).toBe(false) // Auto is Claude Haiku
    expect(fixtureVisible(c, effectiveModel(models, 'fake-small'), null)).toBe(true)
    const spent = caps('public', {}, { ...FIXTURES, models: FIXTURES.models.map((m) => ({ ...m, default: m.provider === 'fake', available: m.provider === 'fake', reason: m.provider === 'fake' ? null : ('budget_exhausted' as const) })) })
    expect(fixtureVisible(spent, effectiveModel(pickerModels(spent, null), null), null)).toBe(true)
    // An API that lists no fixtures for public mode (before the picker fixes): never.
    expect(fixtureVisible(caps('public', {}, PUBLIC), effectiveModel(models, 'fake-small'), null)).toBe(false)
    // Dev mode: a provider override decides; private mode takes no fixture.
    const dev = caps('dev', {}, FIXTURES)
    expect(fixtureVisible(dev, effectiveModel(models, 'fake-small'), 'ollama')).toBe(false)
    expect(fixtureVisible(dev, effectiveModel(models, null), 'fake')).toBe(true)
    expect(fixtureVisible(caps('private', {}, FIXTURES), effectiveModel(models, 'fake-small'), null)).toBe(false)
  })

  it('groups the session pack own fixtures, then the shared ones, with the server default named', () => {
    const catalog = fixtureCatalog(caps('public', {}, FIXTURES))
    expect(catalog.defaultName).toBe('public_demo')
    expect(catalog.descriptions.plain_answer).toBe('A plain question answered from the system prompt, no tool.')
    expect(fixtureGroups(catalog, null, 'finance')).toEqual({ own: ['cite_mgmt_fee_hcp01'], shared: ['plain_answer', 'public_demo'], all: ['cite_mgmt_fee_hcp01', 'plain_answer', 'public_demo'] })
    expect(fixtureGroups(catalog, null, '_starter').own).toEqual(['tool_call'])
    // The dev-mode list, with GET /v1/packs saying whose is whose.
    const legacy = fixtureCatalog({ ...caps('dev'), fixtures: ['plain_answer', 'tool_call'] })
    expect(legacy.packsOf).toBeNull()
    expect(fixtureGroups(legacy, [{ id: '_starter', fixtures: ['tool_call'] } as never], '_starter')).toEqual({ own: ['tool_call'], shared: ['plain_answer'], all: ['tool_call', 'plain_answer'] })
    expect(fixtureCatalog(caps('public')).names).toEqual([])
  })
})
