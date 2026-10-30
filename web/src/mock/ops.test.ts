import { describe, expect, it } from 'vitest'
import { createApi } from '@/api/client'
import { openEvalStream } from '@/api/stream'
import type { EvalStreamEvent } from '@/api/types'
import { parseUnifiedDiff } from '@/lib/diff'
import { MockServer } from './server'

// The ops half of the mock follows phase-4 CONTRACT.md, so the UI's tests and the mock e2e walk
// the gate: edit a skill, run golden, see the regression; release, roll back, see the old version.
function setup() {
  const server = new MockServer({ stepMs: 0 })
  return { server, api: createApi(server.transport()) }
}

async function finished(api: ReturnType<typeof setup>['api'], runId: string) {
  for (let i = 0; i < 200; i++) {
    const run = await api.evalRun(runId)
    if (run.status !== 'running') return run
    await new Promise((r) => setTimeout(r, 10))
  }
  throw new Error('the run did not finish')
}

describe('mock ops: artifacts, evals and releases', () => {
  it('walks the gate: a changed skill makes golden cases regress, a rollback restores the old version', async () => {
    const { api } = setup()
    let caps = await api.capabilities()
    expect(caps.features.evals && caps.features.release && caps.features.artifact_edit && caps.features.feedback).toBe(true)
    expect(caps.release).toEqual({ active_release_id: 'r1', working_set_dirty: false })

    const [seeded] = await api.evalRuns()
    expect(seeded).toMatchObject({ suite: 'golden', status: 'done', regressions: 0, totals: { cases: 12, passed: 9, failed: 0, skipped: 3 } })

    const skill = await api.artifact('skill', 'order_status')
    expect(skill.version).toBe(1)
    const saved = await api.saveArtifact('skill', 'order_status', skill.body.replaceAll('lookup_order', 'find_order'))
    expect(saved.version).toBe(2)
    const lines = parseUnifiedDiff(saved.diff)
    expect(lines.some((l) => l.type === 'removed' && l.text.includes('lookup_order'))).toBe(true)
    expect(lines.some((l) => l.type === 'added' && l.text.includes('find_order'))).toBe(true)
    caps = await api.capabilities()
    expect(caps.release?.working_set_dirty).toBe(true)

    const { run_id } = await api.startEval({ pack: '_starter', suite: 'golden' })
    const run = await finished(api, run_id)
    expect(run.previous_run_id).toBe(seeded!.run_id)
    expect(run.totals).toEqual({ cases: 12, passed: 6, failed: 3, skipped: 3 })
    expect(run.regressions).toBe(3)
    const broken = run.cases.filter((c) => c.regression).map((c) => c.case_id)
    expect(broken).toEqual(['status_shipped', 'status_arrival', 'status_unknown'])
    const shipped = run.cases.find((c) => c.case_id === 'status_shipped')!
    expect(shipped.scores.tool_calls).toMatchObject({ pass: false, value: 'none' })
    expect(shipped.previous_status).toBe('passed')
    expect(shipped.actual?.path).not.toContain('tools')
    // The case's turn is stored, so the UI can open it in replay.
    expect((await api.turn(shipped.turn_id!)).status).toBe('done')

    const r2 = await api.createRelease('find_order experiment')
    expect(r2).toMatchObject({ release_id: 'r2', active: true, note: 'find_order experiment' })
    const detail = await api.release('r2')
    expect(detail.diff_from_previous).toEqual([expect.objectContaining({ kind: 'skill', name: 'order_status', from_version: 1, to_version: 2 })])
    expect((await api.releases()).map((r) => [r.release_id, r.active])).toEqual([
      ['r2', true],
      ['r1', false],
    ])

    await api.rollback('r1')
    caps = await api.capabilities()
    expect(caps.release).toEqual({ active_release_id: 'r1', working_set_dirty: false })
    const restored = await api.artifact('skill', 'order_status')
    expect(restored.version).toBe(3)
    expect(restored.body).toContain('lookup_order')
  })

  it('rejects an edit while a turn runs, invalid YAML, and the judge suite on the fake provider', async () => {
    const { api } = setup()
    const policy = await api.artifact('policy', 'policy')
    await expect(api.saveArtifact('policy', 'policy', 'rules: [unclosed')).rejects.toMatchObject({ status: 422, code: 'invalid_artifact' })
    await api.startTurn({ message: 'What are your support hours?', session_id: 'sess_x', step: true })
    await expect(api.saveArtifact('policy', 'policy', policy.body + '\n')).rejects.toMatchObject({ status: 409, code: 'turn_running' })
    await expect(api.startEval({ pack: '_starter', suite: 'judge' })).rejects.toMatchObject({ status: 422, code: 'judge_unavailable' })
  })

  it('streams eval progress and closes after eval_run_finished', async () => {
    const { server, api } = setup()
    const { run_id } = await api.startEval({ pack: '_starter', suite: 'golden' })
    const seen: EvalStreamEvent['type'][] = []
    const why = await new Promise<string>((resolve) => {
      openEvalStream(server.transport(), run_id, { onEvent: (ev) => seen.push(ev.type), onEnd: resolve })
    })
    expect(why).toBe('finished')
    expect(seen[0]).toBe('eval_run_started')
    expect(seen.filter((t) => t === 'eval_case_finished')).toHaveLength(12)
    expect(seen[seen.length - 1]).toBe('eval_run_finished')
  })
})

describe('mock ops: feedback, scores and the trace query', () => {
  it('stores feedback on a turn, and the seeded turn carries a judge score', async () => {
    const { api } = setup()
    const seeded = await api.turn('turn_plain_answer')
    expect(seeded.feedback).toMatchObject({ rating: 'good' })
    expect(seeded.scores).toEqual({ faithfulness: 0.92, relevance: 0.88 })
    await api.feedback('turn_tool_call', 'bad', 'wrong date')
    expect((await api.turn('turn_tool_call')).feedback).toMatchObject({ rating: 'bad', note: 'wrong date' })
  })

  it('lists turns with receipts, filtered by session and node, and serves a per-node receipt', async () => {
    const { api } = setup()
    const { turns } = await api.traceTurns({ session: 'sess_demo' })
    expect(turns.map((t) => t.turn_id).sort()).toEqual(['turn_approval_pause', 'turn_plain_answer', 'turn_tool_call'])
    const tools = await api.traceTurns({ session: 'sess_demo', node: 'tools' })
    expect(tools.turns.map((t) => t.turn_id).sort()).toEqual(['turn_approval_pause', 'turn_tool_call'])
    const page = await api.traceTurns({ limit: 1 })
    expect(page.turns).toHaveLength(1)
    expect(page.next).toBe(page.turns[0]!.turn_id)
    const receipt = await api.receipt('turn_tool_call')
    expect(receipt.total).toEqual({ input_tokens: 1917, output_tokens: 45, usd: 0 })
    expect(receipt.by_node.model_layer).toEqual(receipt.total)
    expect(receipt.by_model['fake-small']).toEqual(receipt.total)
    expect(receipt.latency_ms.loop).toBe(391)
  })
})
