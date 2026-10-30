import { afterEach, describe, expect, it } from 'vitest'
import { MockServer } from '@/mock/server'
import { useApp } from './store'

// The store's P4 actions against the in-browser mock: what the chat and the diagram read after
// a save, an eval run, a release, a rollback and feedback.
const initial = useApp.getState()

async function boot() {
  const server = new MockServer({ stepMs: 0 })
  await useApp.getState().boot(server.transport(), { mock: true, sessionId: 'sess_demo', seedMessages: server.seededMessages() })
  return server
}

async function until(check: () => boolean, ms = 3000) {
  const end = Date.now() + ms
  while (!check()) {
    if (Date.now() > end) throw new Error('timed out')
    await new Promise((r) => setTimeout(r, 10))
  }
}

afterEach(() => useApp.setState(initial, true))

describe('store: LLM Ops actions', () => {
  it('reads the release state, the latest eval run and the feedback of past turns at boot', async () => {
    await boot()
    const s = useApp.getState()
    expect(s.capabilities?.release).toEqual({ active_release_id: 'r1', working_set_dirty: false, use_working_set: true })
    await until(() => useApp.getState().latestEval !== null)
    expect(useApp.getState().latestEval?.regressions).toBe(0)
    // The session listing has no feedback field, so each finished turn is read once.
    await until(() => useApp.getState().turns.turn_plain_answer?.summary?.feedback !== undefined)
    expect(useApp.getState().turns.turn_plain_answer?.summary?.feedback?.rating).toBe('good')
    expect(useApp.getState().turns.turn_plain_answer?.summary?.scores?.faithfulness).toBe(0.92)
  })

  it('a save adds a marker after the last turn and marks the working set dirty; Restore saves the old body', async () => {
    await boot()
    const before = (await useApp.getState().api!.artifact('skill', 'returns')).body
    const saved = await useApp.getState().saveArtifact('skill', 'returns', before + '\nExtra line.\n', 'try it')
    expect(saved?.version).toBe(2)
    const s = useApp.getState()
    expect(s.marks.at(-1)).toMatchObject({ kind: 'saved', text: 'Skill returns v2 saved', afterTurnId: 'turn_approval_pause' })
    await until(() => useApp.getState().capabilities?.release?.working_set_dirty === true)
    expect(await useApp.getState().restoreArtifact('skill', 'returns', 1)).toBe(true)
    expect((await useApp.getState().api!.artifact('skill', 'returns')).body).toBe(before)
    expect(useApp.getState().marks.at(-1)?.text).toBe('Skill returns v3 saved')
  })

  it('an eval run reports progress, then the finished run feeds the diagram badge', async () => {
    await boot()
    const body = (await useApp.getState().api!.artifact('skill', 'order_status')).body
    await useApp.getState().saveArtifact('skill', 'order_status', body.replaceAll('lookup_order', 'find_order'))
    const runId = await useApp.getState().startEval({ pack: '_starter', suite: 'golden' })
    expect(runId).toBeTruthy()
    expect(useApp.getState().liveEval).toMatchObject({ runId, suite: 'golden', done: 0 })
    await until(() => useApp.getState().liveEval === null)
    expect(useApp.getState().latestEval).toMatchObject({ run_id: runId, regressions: 3 })
  })

  it('the judge suite on the fake provider says why it cannot start', async () => {
    await boot()
    expect(await useApp.getState().startEval({ pack: '_starter', suite: 'judge' })).toBeNull()
    expect(useApp.getState().notices.at(-1)?.text).toMatch(/judge suite needs a configured model/)
  })

  it('release and rollback move the active release and leave markers whose Restore goes back', async () => {
    await boot()
    const body = (await useApp.getState().api!.artifact('prompt', 'system_prompt')).body
    await useApp.getState().saveArtifact('prompt', 'system_prompt', body + '\nBe kind.\n')
    const r2 = await useApp.getState().createRelease('kinder')
    expect(r2?.release_id).toBe('r2')
    expect(useApp.getState().capabilities?.release).toEqual({ active_release_id: 'r2', working_set_dirty: false, use_working_set: true })
    expect(useApp.getState().marks.at(-1)).toMatchObject({ kind: 'released', text: 'Release r2 active', restore: { type: 'rollback', releaseId: 'r1' } })
    expect(await useApp.getState().rollback('r1')).toBe(true)
    expect(useApp.getState().capabilities?.release?.active_release_id).toBe('r1')
    expect(useApp.getState().marks.at(-1)).toMatchObject({ kind: 'rolled_back', restore: { type: 'rollback', releaseId: 'r2' } })
    // One marker per action: the capability refresh does not add another for the same release.
    expect(useApp.getState().marks.filter((m) => m.kind !== 'saved')).toHaveLength(2)
  })

  it('a release made elsewhere shows up as a marker when the capabilities are read again', async () => {
    const server = await boot()
    await createApiRelease(server)
    await useApp.getState().refreshCapabilities()
    expect(useApp.getState().marks.at(-1)).toMatchObject({ text: 'Release r2 active', restore: { type: 'rollback', releaseId: 'r1' } })
  })

  it('feedback is stored on the turn and survives the next summary read', async () => {
    await boot()
    expect(await useApp.getState().sendFeedback('turn_tool_call', 'bad', 'wrong date')).toBe(true)
    expect(useApp.getState().turns.turn_tool_call?.feedback).toMatchObject({ rating: 'bad', note: 'wrong date' })
    const summary = await useApp.getState().api!.turn('turn_tool_call')
    expect(summary.feedback).toMatchObject({ rating: 'bad', note: 'wrong date' })
  })

  it('resumes with step_over: the next pause is a simple-view node', async () => {
    await boot()
    useApp.getState().setStepMode(true)
    await useApp.getState().send('What are your support hours?')
    const paused = () => useApp.getState().turns[useApp.getState().liveTurnId!]?.state?.paused?.nodeId
    await until(() => paused() === 'gateway')
    for (const next of ['input_guardrails', 'context_builder', 'memory']) {
      await useApp.getState().resume('step')
      await until(() => paused() === next)
    }
    await useApp.getState().resume('step_over')
    await until(() => paused() === 'loop')
    await useApp.getState().resume('continue')
    await until(() => useApp.getState().turns[useApp.getState().liveTurnId ?? '']?.state?.phase === 'done' || useApp.getState().liveTurnId === null)
  })
})

/** A release made by another client (the CLI, another browser). */
async function createApiRelease(server: MockServer) {
  const res = await server.transport().fetch('/v1/releases', { method: 'POST', body: JSON.stringify({ note: 'from the CLI' }) })
  expect(res.status).toBe(201)
}
