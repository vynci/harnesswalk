import { describe, expect, it } from 'vitest'
import { ApiError, createApi } from '@/api/client'
import { openOpsStream, openTuneStream } from '@/api/stream'
import type { OpsEvent } from '@/api/types'
import { MockServer } from './server'
import { TUNE_REPLAY } from './tune'

// The tune half of the mock answers like http_tune.py: a public server replays the recorded
// fake loop (config/fixtures/tune_replay.json, read from the repo) with every POST refused, and
// dev mode acts on the same records, so the Tune tab and its e2e have something real to show.
function setup(pub = false) {
  const server = new MockServer({ stepMs: 0, public: pub ? {} : null })
  return { server, api: createApi(server.transport()) }
}

async function until<T>(read: () => Promise<T>, done: (x: T) => boolean): Promise<T> {
  for (let i = 0; i < 300; i++) {
    const x = await read()
    if (done(x)) return x
    await new Promise((r) => setTimeout(r, 10))
  }
  throw new Error('timed out')
}

describe('mock tune: public mode replays the recording', () => {
  it('answers every GET from the fixture with replay: true', async () => {
    const { api } = setup(true)
    const overview = await api.tune()
    expect(overview.replay).toBe(true)
    expect(overview.trainers.map((t) => t.alias)).toEqual(['fake', 'homelab-trl', 'local-mlx'])
    const datasets = await api.tuneArtifacts('dataset')
    expect(datasets).toMatchObject({ replay: true, items: [{ name: 'approvals', version: 1 }] })
    expect((await api.tuneRuns()).items.map((r) => r.id)).toEqual(TUNE_REPLAY.runs.map((r) => r.id))
    expect((await api.comparison('cmp_1')).verdict?.status).toBe('passed')
    expect((await api.canary()).replay).toBe(true)
    expect((await api.tuneArtifact('model', 'forgets_fake')).history).toHaveLength(2)
  })

  it('refuses every POST with 403 forbidden_in_mode', async () => {
    const { api } = setup(true)
    for (const call of [
      () => api.startCuration({ name: 'x', packs: ['ecommerce'] }),
      () => api.startTuneRun({ dataset: 'approvals', trainer: 'fake', student: 'fake-student' }),
      () => api.startComparison({ candidate: 'model/approvals_fake', against: 'fake-student', packs: ['ecommerce'], canary_packs: [] }),
      () => api.promote({ candidate: 'model/approvals_fake', stage: 'canary' }),
      () => api.tuneRollback(),
    ]) {
      await expect(call()).rejects.toMatchObject({ status: 403, code: 'forbidden_in_mode' })
    }
  })

  it('replays a recorded run on its stream and closes', async () => {
    const { server, api } = setup(true)
    void server
    const events: OpsEvent[] = []
    const why = await new Promise<string>((resolve) => openTuneStream(api.transport, api.tuneRunEventsUrl('tr_1'), { onEvent: (e) => events.push(e), onEnd: resolve }))
    expect(why).toBe('finished')
    expect(events[0]!.type).toBe('tune_run_started')
    expect(events.filter((e) => e.type === 'tune_step')).toHaveLength(34)
    expect(events[events.length - 1]!.type).toBe('tune_run_finished')
  })
})

describe('mock tune: dev mode acts on the records', () => {
  it('refuses to promote a model whose gate failed, with the gate in the body', async () => {
    const { api } = setup()
    expect((await api.tune()).replay).toBe(false)
    const out = await api.promote({ candidate: 'model/forgets_fake', stage: 'canary', fraction: 0.5 })
    expect(out.refused).toMatchObject({ code: 'gate_failed', message: 'model/forgets_fake has no passed gate' })
    expect(out.refused?.gate).toMatchObject({ status: 'failed', reason: 'below_min_gain', regressed: ['sports', '_starter'] })
  })

  it('walks canary, refused full, full once the canary gate passes, and rollback', async () => {
    const { server, api } = setup()
    const canary = await api.promote({ candidate: 'model/approvals_fake', stage: 'canary', fraction: 0.5 })
    expect(canary.result?.setting).toMatchObject({ stage: 'canary', fraction: 0.5, packs: ['ecommerce', 'logistics'], base_alias: 'fake-student', candidate_alias: 'tuned-approvals-fake' })
    expect((await api.canary()).rule?.split.fraction).toBe(0.5)

    server.tune.addCanaryTurns('candidate', 6, 2)
    server.tune.addCanaryTurns('base', 5)
    const refused = await api.promote({ candidate: 'model/approvals_fake', stage: 'full' })
    expect(refused.refused).toMatchObject({ code: 'canary_failed', message: 'candidate_worse: failure rate 0.3333 against 0' })
    expect(refused.refused?.canary?.arms.candidate).toMatchObject({ turns: 6, bad_feedback: 2 })

    const rolled = await api.tuneRollback()
    expect(rolled.result?.setting).toMatchObject({ stage: 'rollback', fraction: 0 })
    const state = await api.canary()
    expect(state.rule?.split.fraction).toBe(0)
    expect(state.history.map((h) => h.stage).slice(0, 2)).toEqual(['rollback', 'canary'])

    await api.promote({ candidate: 'model/approvals_fake', stage: 'canary', fraction: 0.1 })
    server.tune.addCanaryTurns('candidate', 5)
    const full = await api.promote({ candidate: 'model/approvals_fake', stage: 'full' })
    expect(full.result?.setting).toMatchObject({ stage: 'full', fraction: 1 })
  })

  it('trains on the fake trainer: streamed steps, then an adapter and a model', async () => {
    const { api } = setup()
    const heard: OpsEvent[] = []
    const ops = openOpsStream(api.transport, { onEvent: (e) => heard.push(e) })
    await new Promise((r) => setTimeout(r, 5))
    const started = await api.startTuneRun({ dataset: 'approvals', trainer: 'fake', student: 'fake-student', name: 'mine', config: { outcome: 'good' } })
    expect(started).toMatchObject({ kind: 'run', status: 'running', events_url: `/v1/tune/runs/${started.id}/events` })
    await expect(api.startTuneRun({ dataset: 'approvals', trainer: 'fake', student: 'fake-student' })).rejects.toMatchObject({ status: 409, code: 'tune_running' })
    const events: OpsEvent[] = []
    await new Promise((resolve) => openTuneStream(api.transport, api.tuneRunEventsUrl(started.id), { onEvent: (e) => events.push(e), onEnd: resolve }))
    expect(events.filter((e) => e.type === 'tune_step')).toHaveLength(34)
    const run = await until(() => api.tuneRun(started.id), (r) => r.status === 'done')
    expect(run.model).toMatchObject({ name: 'mine', version: 1 })
    expect(run.steps).toHaveLength(34)
    const model = await api.tuneArtifact('model', 'mine')
    expect(model.manifest).toMatchObject({ kind: 'model', alias: 'tuned-mine', gate: null })
    expect(heard.some((e) => e.type === 'tune_step' && e.node_id === 'tune')).toBe(true)
    expect(heard.some((e) => e.type === 'artifact_changed' && e.summary?.kind === 'model')).toBe(true)
    ops.close()

    await expect(api.startTuneRun({ dataset: 'approvals', trainer: 'local-mlx', student: 'fake-student' })).rejects.toMatchObject({ status: 422, code: 'trainer_unavailable' })
  })

  it('compares a model and appends the verdict to it as a new version', async () => {
    const { api } = setup()
    const started = await api.startComparison({ candidate: 'model/forgets_fake', against: 'fake-student', packs: ['ecommerce', 'logistics'], canary_packs: ['sports', '_starter'], samples: 1 })
    const done = await until(() => api.comparison(started.id), (c) => c.status === 'done')
    expect(done.verdict).toMatchObject({ status: 'failed', reason: 'below_min_gain' })
    const model = await api.tuneArtifact('model', 'forgets_fake')
    expect(model.version).toBe(3)
    expect(model.history).toHaveLength(3)
  })

  it('reports a refusal the client did not expect as an ApiError', async () => {
    const { api } = setup()
    await expect(api.startComparison({ candidate: 'model/nope', against: 'fake-student', packs: ['ecommerce'], canary_packs: [] })).rejects.toBeInstanceOf(ApiError)
  })
})
