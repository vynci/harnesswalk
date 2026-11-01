import { describe, expect, it } from 'vitest'
import type { ArtifactRef, Capabilities } from '@/api/types'
import { artifactLabel, editBlockReason, inspectorArtifact, releaseMark, savedMark } from './ops'

const caps = (over: Partial<Capabilities> = {}, features: Partial<Capabilities['features']> = {}): Capabilities => ({
  mode: 'dev',
  version: 't',
  providers: [],
  fixtures: [],
  features: { step_mode: true, approvals: true, message_delta: true, cancel: true, byo_key: false, artifact_edit: true, ...features },
  limits: { max_message_chars: 4000, max_steps: 8, session_ttl_seconds: null },
  ...over,
})

const ref = (kind: string, name: string, version = 1): ArtifactRef => ({ kind, name, version, hash: 'h' })
const LIST = [ref('prompt', 'system_prompt'), ref('skill', 'order_status', 2), ref('skill', 'returns'), ref('policy', 'policy'), ref('models', 'models')]

describe('ops helpers', () => {
  it('names artifacts and builds the chat markers with what Restore puts back', () => {
    expect(artifactLabel('prompt', 'system_prompt')).toBe('Prompt')
    expect(artifactLabel('skill', 'returns')).toBe('Skill returns')
    expect(artifactLabel('policy', 'policy')).toBe('Policy')
    expect(savedMark({ kind: 'prompt', name: 'system_prompt', version: 3 }, 't1', 'now')).toMatchObject({
      text: 'Prompt v3 saved',
      afterTurnId: 't1',
      restore: { type: 'artifact', kind: 'prompt', name: 'system_prompt', version: 2 },
    })
    expect(savedMark({ kind: 'skill', name: 'x', version: 1 }, null).restore).toBeUndefined()
    expect(releaseMark('r4', 'r3', false, null)).toMatchObject({ text: 'Release r4 active', restore: { type: 'rollback', releaseId: 'r3' } })
    expect(releaseMark('r1', 'r2', true, null)).toMatchObject({ kind: 'rolled_back', text: 'Rolled back: release r1 active' })
    expect(releaseMark('r1', null, false, null).restore).toBeUndefined()
  })

  it('says why an edit is blocked: public mode, no feature, a running turn', () => {
    expect(editBlockReason(caps(), false)).toBeNull()
    expect(editBlockReason(caps({ mode: 'public' }), false)).toBe('Editing needs a private workspace. Coming later.')
    expect(editBlockReason(caps({}, { artifact_edit: false }), false)).toMatch(/does not offer/)
    expect(editBlockReason(caps({}, { artifact_edit: undefined }), false)).toMatch(/does not offer/)
    expect(editBlockReason(caps(), true)).toMatch(/turn is running/)
  })

  it('takes the artifact the inspector names, with the listing metadata', () => {
    expect(inspectorArtifact({ kind: 'skill', name: 'order_status', version: 1, url: '/v1/artifacts/skill/order_status' }, LIST)).toEqual({ kind: 'skill', name: 'order_status', version: 2 })
    const listed = [{ ...ref('policy', 'policy', 3), updated_by: 'user:dev', served_version: 3 }]
    expect(inspectorArtifact({ kind: 'policy', name: 'policy', version: 3, url: '/v1/artifacts/policy/policy' }, listed)).toEqual({ kind: 'policy', name: 'policy', version: 3, updated_by: 'user:dev', served_version: 3 })
    expect(inspectorArtifact(null, LIST)).toBeUndefined()
    expect(inspectorArtifact(undefined, LIST)).toBeUndefined()
  })
})
