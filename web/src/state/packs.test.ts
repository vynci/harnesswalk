import { describe, expect, it } from 'vitest'
import { afterNote, gateText, layerLabel } from '@/inspector/p5View'
import { artifactOfFile, buildTree, docIdOf, filesForNode, fixtureChoices, packTitle, pdfSource } from './packs'

const packs = [
  { id: '_starter', title: 'Example Store', fixtures: ['tool_call'] },
  { id: 'finance', title: 'Hollowmere Capital Partners', fixtures: ['cite_fee', 'mem_set'] },
  { id: 'sports', fixtures: ['table'] },
]
const all = ['plain_answer', 'tool_call', 'cite_fee', 'mem_set', 'table']

describe('pack helpers', () => {
  it('lists a pack’s own fixtures first, then the shared ones, and hides other packs’', () => {
    expect(fixtureChoices(packs, all, 'finance')).toEqual({ own: ['cite_fee', 'mem_set'], shared: ['plain_answer'], all: ['cite_fee', 'mem_set', 'plain_answer'] })
    expect(fixtureChoices(packs, all, '_starter').all).toEqual(['tool_call', 'plain_answer'])
    // Without the pack list every fixture is offered, as before P5.
    expect(fixtureChoices(null, all, 'finance').all).toEqual(all)
  })

  it('names a pack by its title, else its id', () => {
    expect(packTitle(packs, 'finance')).toBe('Hollowmere Capital Partners')
    expect(packTitle(packs, 'sports')).toBe('sports')
    expect(packTitle(packs, null)).toBe('the configured pack')
  })

  it('matches files to a node by their role, and builds a folders-first tree', () => {
    const files = [
      { path: 'system_prompt.md', bytes: 10, role: 'procedural' },
      { path: 'skills/b.md', bytes: 10, role: 'procedural' },
      { path: 'skills/a.md', bytes: 10, role: 'procedural' },
      { path: 'docs/x.pdf', bytes: 10, role: 'knowledge' },
      { path: 'sources/x.md', bytes: 10, role: 'knowledge_source' },
      { path: 'guardrails.yaml', bytes: 10, role: 'guardrails' },
      { path: 'pack.yaml', bytes: 10, role: 'card' },
    ]
    expect(filesForNode(files, 'procedural').map((f) => f.path)).toEqual(['system_prompt.md', 'skills/b.md', 'skills/a.md'])
    expect(filesForNode(files, 'knowledge').map((f) => f.path)).toEqual(['docs/x.pdf', 'sources/x.md'])
    expect(filesForNode(files, 'input_guardrails').map((f) => f.path)).toEqual(['guardrails.yaml'])
    expect(filesForNode(files, 'loop')).toEqual([])
    const tree = buildTree(filesForNode(files, 'procedural'))
    expect(tree.map((n) => `${n.type}:${n.name}`)).toEqual(['folder:skills', 'file:system_prompt.md'])
    expect(tree[0]!.type === 'folder' && tree[0]!.children.map((c) => c.path)).toEqual(['skills/a.md', 'skills/b.md'])
    expect(pdfSource(files, 'docs/x.pdf')).toBe('sources/x.md')
    expect(pdfSource(files, 'docs/y.pdf')).toBeNull()
    expect(pdfSource(files, 'docs/x.md')).toBeNull()
    expect(docIdOf('docs/fund_sheet_hcp01.md')).toBe('fund_sheet_hcp01')
    expect(artifactOfFile('system_prompt.md')).toEqual({ kind: 'prompt', name: 'system_prompt' })
    expect(artifactOfFile('skills/returns.md')).toEqual({ kind: 'skill', name: 'returns' })
    expect(artifactOfFile('docs/returns.md')).toBeNull()
  })
})

describe('P5 panel text', () => {
  it('says what a gate did', () => {
    expect(gateText({ status: 'passed', suite: 'golden', pass_rate: 1, min_pass_rate: 1, run_ids: ['run_0004'] })).toBe('Gate passed on the golden suite (pass rate 1, needs 1), run run_0004')
    expect(gateText({ status: 'failed', reason: 'below_min_pass_rate', suite: 'golden', pass_rate: 0.9583, min_pass_rate: 1, run_ids: ['run_0001'] })).toBe(
      'Gate failed on the golden suite: the pass rate is below the minimum (pass rate 0.9583, needs 1), run run_0001',
    )
    expect(gateText({ status: 'skipped', reason: 'forced', forced: true, suite: 'golden' })).toBe('Gate skipped: released anyway, without the gate; the audit log records it as release_forced')
    expect(gateText({ status: 'skipped', reason: 'startup' })).toBe('Gate skipped: made by the harness at start-up')
  })

  it('names layers and explains a remember and recall pair', () => {
    expect(layerLabel('prompt_or_skill')).toBe('prompt or skill')
    expect(layerLabel('something_new')).toBe('something new')
    expect(afterNote('mem_set')).toBe('Runs after mem_set: it reads what that case remembered, so run alone it has nothing to recall.')
  })
})
