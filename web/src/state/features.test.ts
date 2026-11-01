import { describe, expect, it } from 'vitest'
import type { Capabilities, PublicInfo } from '@/api/types'
import { effectiveModel, featureState, modelReasonShort, modelReasonText, pickerModels, PRIVATE_ONLY, resetTime } from './features'

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

describe('the public model picker', () => {
  it('lists the public models with the disabled reason, default preselected', () => {
    const models = pickerModels(caps('public', {}, PUBLIC), null)
    expect(models.map((m) => [m.alias, m.available])).toEqual([
      ['hosted-haiku', true],
      ['hosted-sonnet', false],
      ['fake-small', true],
    ])
    expect(models[1]!.reasonText).toBe("Today's shared budget for Claude models is spent. It resets at 00:00 UTC. Bring your own key or use the fake model.")
    expect(effectiveModel(models, null)?.alias).toBe('hosted-haiku')
    expect(effectiveModel(models, 'fake-small')?.alias).toBe('fake-small')
    // A pick that became unavailable falls back to the default.
    expect(effectiveModel(models, 'hosted-sonnet')?.alias).toBe('hosted-haiku')
  })

  it('a key of your own makes the Claude models available whatever the budget says', () => {
    const spent: PublicInfo = {
      ...PUBLIC,
      budget: { status: 'exhausted', resets_at: '2026-11-02T00:00:00Z', spent_fraction: 1 },
      models: PUBLIC.models.map((m) => (m.provider === 'anthropic' ? { ...m, available: false, reason: 'budget_exhausted' as const } : m)),
    }
    const without = pickerModels(caps('public', {}, spent), null)
    expect(without.filter((m) => m.available).map((m) => m.alias)).toEqual(['fake-small'])
    expect(effectiveModel(without, null)?.alias).toBe('fake-small')
    const withKey = pickerModels(caps('public', {}, spent), 'sk-ant-test')
    expect(withKey.every((m) => m.available && m.reasonText === null)).toBe(true)
    expect(effectiveModel(withKey, null)?.alias).toBe('hosted-haiku')
    const unconfigured: PublicInfo = { ...PUBLIC, models: [{ ...PUBLIC.models[0]!, available: false, reason: 'provider_unconfigured' }] }
    expect(pickerModels(caps('public', {}, unconfigured), null)[0]!.reasonText).toBe('No key is configured for this model. Bring your own key.')
    expect(pickerModels(caps('public', {}, unconfigured), 'k')[0]!.available).toBe(true)
  })

  it('a provider marked down stays unavailable with its own reason, key or not', () => {
    const down: PublicInfo = { ...PUBLIC, models: PUBLIC.models.map((m) => (m.alias === 'hosted-sonnet' ? { ...m, available: false, reason: 'provider_unhealthy' as const } : m)) }
    for (const key of [null, 'sk-ant-test']) {
      const sonnet = pickerModels(caps('public', {}, down), key).find((m) => m.alias === 'hosted-sonnet')!
      expect(sonnet.available).toBe(false)
      expect(sonnet.reason).toBe('provider_unhealthy')
      expect(sonnet.reasonText).toBe("This model's provider is not answering right now. Try again in a minute or use another model.")
    }
    expect(modelReasonShort('provider_unhealthy')).toBe('Not answering')
    expect(effectiveModel(pickerModels(caps('public', {}, down), null), 'hosted-sonnet')?.alias).toBe('hosted-haiku')
  })

  it('is empty for an API without capabilities.public (before P6), so turns name no model', () => {
    expect(pickerModels(caps('public'), null)).toEqual([])
    expect(effectiveModel([], null)).toBeNull()
  })

  it('names the reset time in UTC', () => {
    expect(resetTime('2026-11-02T00:00:00Z')).toBe('00:00 UTC')
    expect(resetTime('2026-11-02T06:30:00Z')).toBe('06:30 UTC')
    expect(resetTime(null)).toBe('00:00 UTC')
    expect(modelReasonText('budget_exhausted', '2026-11-02T06:30:00Z')).toContain('It resets at 06:30 UTC.')
  })
})
