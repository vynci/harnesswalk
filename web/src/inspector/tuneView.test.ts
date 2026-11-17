import { describe, expect, it } from 'vitest'
import type { Capabilities, Comparison, OpsEvent } from '@/api/types'
import { TUNE_REPLAY } from '@/mock/tune'
import { initialTurnState, reduceEvent } from '@/state/reducer'
import { diagram } from '@/test/fixtures'
import { armBucketText, routerRules, ruleView } from './routerView'
import {
  armText,
  canaryRuleText,
  cellText,
  chartGeometry,
  compareRows,
  latestOpsEvents,
  lossSeries,
  opsEventText,
  parseFraction,
  promptArmText,
  runSteps,
  stepsOf,
  toolSplitRows,
  tuneBlockReason,
  tuneKinds,
  tuneNodeId,
  tunePart,
  verdictText,
} from './tuneView'

// The recorded fake loop the public site replays (config/fixtures/tune_replay.json).
const byId = (id: string) => TUNE_REPLAY.comparisons.find((c) => c.id === id) as Comparison
const passed = byId('cmp_1')
const forgets = byId('cmp_2')

describe('the comparison table', () => {
  it('reads pack x arm with target packs first, as the CLI renders it', () => {
    const rows = compareRows(passed)
    expect(rows.map((r) => [r.pack, r.role])).toEqual([
      ['ecommerce', 'target'],
      ['logistics', 'target'],
      ['sports', 'canary'],
      ['_starter', 'canary'],
    ])
    const ecommerce = rows[0]!.cells
    expect(ecommerce.map((c) => [c.arm, c.text, c.delta])).toEqual([
      ['base', '13/24', null],
      ['candidate', '24/24', 11],
      ['prompt_arm', '13/24', 0],
    ])
    expect(rows.every((r) => !r.regressed)).toBe(true)
  })

  it('marks the canary packs a forgetting candidate regressed on', () => {
    const rows = compareRows(forgets)
    expect(rows.filter((r) => r.regressed).map((r) => r.pack)).toEqual(['sports', '_starter'])
    expect(verdictText(forgets)).toMatch(/^Gate failed \(below_min_gain\)\. Gain on the target packs 1 cases \(needs 2\)/)
    expect(verdictText(passed)).toMatch(/^Gate passed\. Gain on the target packs 15 cases/)
  })

  it('shows a spread when the samples differ, and n/a for a missing cell', () => {
    expect(cellText({ passed: 12.5, min: 12, max: 13, scored: 24, tool_split: {} })).toBe('12.5/24 (12-13)')
    expect(cellText(null)).toBe('n/a')
  })

  it('sums the tool_calls split per arm over the target packs only', () => {
    const rows = toolSplitRows(passed)
    const byBucket = Object.fromEntries(rows.map((r) => [r.bucket, r.values]))
    // ecommerce 2 + logistics 4 write-tool misses on the base, none on the candidate.
    expect(byBucket['write tool not called']).toEqual({ base: 6, candidate: 0, prompt_arm: 6 })
    expect(byBucket['none called']).toEqual({ base: 9, candidate: 0, prompt_arm: 9 })
    const forget = Object.fromEntries(toolSplitRows(forgets).map((r) => [r.bucket, r.values]))
    expect(forget['called when none expected']).toEqual({ base: 0, candidate: 14, prompt_arm: 0 })
  })

  it('says what the prompt arm gained and whether it would pass', () => {
    expect(promptArmText(passed)).toBe('Prompt arm: gain 0, canary ok; it would not pass the same gate.')
    expect(promptArmText({ verdict: null })).toBeNull()
  })
})

describe('the loss series', () => {
  const run = TUNE_REPLAY.runs.find((r) => r.id === 'tr_1')!

  it('reads train loss per step and the validation points from tune_step events', () => {
    const s = lossSeries(stepsOf(run.events))
    expect(s.total).toBe(34)
    expect(s.train).toHaveLength(34)
    const first = run.events.find((e) => e.type === 'tune_step')!.summary as { loss: number }
    expect(s.train[0]).toEqual({ step: 1, loss: first.loss }) // the recorded loss of step 1
    expect(s.eval.length).toBeGreaterThan(0)
    expect(s.eval.every((p) => p.loss > 0)).toBe(true)
    expect(s.last?.step).toBe(34)
  })

  it('merges the record steps and the live stream without counting a step twice', () => {
    const live = run.events.slice(0, 5)
    const s = lossSeries(runSteps({ steps: run.steps, events: [] }, live))
    expect(s.train).toHaveLength(34)
    expect(lossSeries(runSteps(null, live)).train.map((p) => p.step)).toEqual([1, 2, 3, 4])
  })

  it('keeps the worker phases after training out of the curve', () => {
    const s = lossSeries([
      { run_id: 'r', step: 1, total_steps: 2, loss: 1, phase: 'train' },
      { run_id: 'r', step: 2, total_steps: 2, loss: 0.5, phase: 'train' },
      { run_id: 'r', step: 2, total_steps: 2, phase: 'quantize' },
    ])
    expect(s.train).toHaveLength(2)
    expect(s.phase).toBe('quantize')
  })

  it('draws within the chart and starts the y axis at 0', () => {
    const g = chartGeometry(lossSeries(run.steps ?? []))
    expect(g.yMin).toBe(0)
    expect(g.yMax).toBeGreaterThanOrEqual(2.0116)
    expect(g.path.startsWith('M')).toBe(true)
    for (const d of g.dots) {
      expect(d.cx).toBeGreaterThanOrEqual(g.pad.left)
      expect(d.cx).toBeLessThanOrEqual(g.width - g.pad.right)
    }
    expect(chartGeometry(lossSeries([])).path).toBe('')
  })
})

describe('the canary', () => {
  const story = TUNE_REPLAY.story.find((s) => s.step === 'chat') as { canary: typeof TUNE_REPLAY.canary }

  it('says the rule in words at a fraction, rolled back and full', () => {
    expect(canaryRuleText(story.canary.rule)).toBe('50% of the turns of ecommerce, logistics go to tuned-approvals-fake, the rest to fake-student.')
    expect(canaryRuleText(TUNE_REPLAY.canary.rule)).toMatch(/^Rolled back: no turn of ecommerce, logistics goes to tuned-approvals-fake/)
    expect(canaryRuleText({ split: { base_alias: 'a', candidate_alias: 'b', fraction: 1 } })).toMatch(/^Full: every turn of every pack goes to b/)
    expect(canaryRuleText(null)).toMatch(/^No canary rule/)
  })

  it('reads each arm', () => {
    // The split is by turn-id hash, so the recorded turns per arm vary between recordings.
    const { candidate, base } = story.canary.arms
    expect(armText(candidate)).toBe(`${candidate.turns} of ${candidate.turns} failed (100%), ${candidate.turns} thumbs down`)
    expect(armText(base)).toBe(`0 of ${base.turns} failed (0%), 0 thumbs down`)
    expect(armText(TUNE_REPLAY.canary.arms.base)).toBe('no turns yet')
  })

  it('parses a fraction or a percentage, and refuses one outside [0, 1]', () => {
    expect(parseFraction('0.5')).toBe(0.5)
    expect(parseFraction('10%')).toBe(0.1)
    expect(parseFraction('1.5')).toBeNull()
    expect(parseFraction('')).toBeNull()
  })
})

describe('actions in public mode and on a replay', () => {
  const caps = (mode: Capabilities['mode']) => ({ mode }) as Capabilities

  it('turns every action off on a recorded replay or a public server, with the reason', () => {
    expect(tuneBlockReason(caps('dev'), true)).toMatch(/never trains/)
    expect(tuneBlockReason(caps('public'), false)).toMatch(/never trains/)
    expect(tuneBlockReason(caps('dev'), false, { id: 'tr_3', kind: 'run' })).toMatch(/tr_3/)
    expect(tuneBlockReason(caps('dev'), false, null)).toBeNull()
    expect(tuneBlockReason(caps('private'), false)).toBeNull()
  })
})

describe('the tune nodes, by what they write', () => {
  it('finds the node that trains and the one that curates without naming them', () => {
    const parts = Object.fromEntries(diagram.nodes.filter((n) => tunePart(n)).map((n) => [n.id, tunePart(n)]))
    expect(parts).toEqual({ curate: 'curates', tune: 'trains' })
    expect(tuneNodeId(diagram)).toBe('tune')
    expect(tuneKinds({ artifact_hint: 'adapter/<name>, model/<name>' })).toEqual(['adapter', 'model'])
    expect(tunePart({ artifact_hint: 'config/models.yaml' })).toBeNull()
  })
})

describe('ops events of the tune nodes', () => {
  const run = TUNE_REPLAY.runs.find((r) => r.id === 'tr_1')!
  const cur = TUNE_REPLAY.curations[0]!

  it('folds a run of steps into its last one, newest first', () => {
    const latest = latestOpsEvents(run.events, new Set(['tune']))
    expect(latest.map((e) => e.type)).toEqual(['tune_run_finished', 'tune_step', 'tune_run_started'])
    const last = latest[1]!.summary as { loss: number; eval_loss: number }
    expect(opsEventText(latest[1]!)).toBe(`step 34 of 34, loss ${last.loss}, eval ${last.eval_loss}`)
    expect(opsEventText(latest[0]!)).toBe('run tr_1 done: model/approvals_fake@v1')
    expect(latestOpsEvents(run.events, new Set(['eval']))).toEqual([])
  })

  it('reads a curation and an unknown type', () => {
    expect(opsEventText(cur.events[1]!)).toBe('ecommerce/cancel_approved_qf20430 passed, kept')
    expect(opsEventText(cur.events[cur.events.length - 1]!)).toBe('cu_1 finished, 142 kept: dataset/approvals@v1')
    expect(opsEventText({ type: 'something_new' } as unknown as OpsEvent)).toBe('something_new')
  })

  it('does not break the turn reducer, whatever node or type it names', () => {
    const start = initialTurnState()
    for (const ev of [run.events[0]!, run.events[1]!, cur.events[0]!, { ...run.events[1]!, type: 'brand_new' }]) {
      const next = reduceEvent(start, { ...ev, turn_id: 'turn_x', seq: 1 } as never)
      expect(next.nodes).toEqual(start.nodes)
      expect(next.phase).toBe(start.phase)
    }
  })
})

describe('router rules', () => {
  const effective = {
    rules: [
      { id: 'public', when: { mode: 'public' }, use: ['hosted-haiku'] },
      { id: 'canary', when: { pack: ['ecommerce', 'logistics'] }, split: { base_alias: 'fake-student', candidate_alias: 'tuned-approvals-fake', fraction: 0.5 } },
      { id: 'default', when: {}, use: ['local-8b', 'fake-small'] },
    ],
    providers: ['fake'],
  }

  it('renders a split rule without a use list', () => {
    const rules = routerRules(effective)
    expect(rules.map((r) => r.id)).toEqual(['public', 'canary', 'default'])
    expect(rules[1]).toMatchObject({ when: 'pack: ecommerce, logistics', target: '50% to tuned-approvals-fake, the rest to fake-student' })
    expect(rules[2]).toMatchObject({ when: 'always', target: 'local-8b, fake-small', split: null })
    expect(ruleView({ id: 'odd' })).toBeNull()
  })

  it('puts a canary kept beside the list after the public rule, and ignores policy rules', () => {
    const beside = routerRules({ rules: [effective.rules[0], effective.rules[2]], canary: { when: { pack: 'ecommerce' }, split: { base_alias: 'a', candidate_alias: 'b', fraction: 0 } } })
    expect(beside.map((r) => r.id)).toEqual(['public', 'canary', 'default'])
    expect(routerRules({ rules: [{ id: 'dev.read.allow', decision: 'allow' }] })).toEqual([])
  })

  it('names the arm and bucket of a call the canary routed', () => {
    expect(armBucketText({ rule: 'canary', arm: 'candidate', bucket: 0.4649 })).toBe('candidate arm, bucket 0.4649')
    expect(armBucketText({ rule: 'default' })).toBeNull()
  })
})
