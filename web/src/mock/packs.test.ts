import { describe, expect, it } from 'vitest'
import { createApi } from '@/api/client'
import { MockServer } from './server'

// P5 in the mock: the packs and their files, sessions on a pack, `?pack=` on every pack-scoped
// route, a domain pack's golden set (checks, after, modes) and diagnose.
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

describe('mock packs', () => {
  it('lists the packs with their own fixtures, and the capabilities hold every fixture', async () => {
    const { api } = setup()
    const caps = await api.capabilities()
    expect(caps.features).toMatchObject({ packs: true, diagnose: true })
    expect(caps.packs).toEqual({ default: '_starter', ids: ['_starter', 'finance', 'healthcare', 'logistics', 'ecommerce', 'sports'] })
    expect(caps.repo_url).toBe('https://github.com/vynci/harnesswalk')
    const packs = await api.packs()
    expect(packs.map((p) => [p.id, p.default])).toEqual([
      ['_starter', true],
      ['finance', false],
      ['healthcare', false],
      ['logistics', false],
      ['ecommerce', false],
      ['sports', false],
    ])
    const finance = packs.find((p) => p.id === 'finance')!
    expect(finance.feature).toBe('Citations and the memory writer')
    expect(finance.fixtures!.every((f) => caps.fixtures.includes(f))).toBe(true)
    expect((finance.prompts as string[])[0]).toBe('What is the management fee for Hollowmere Growth Partners III?')
    // The other domain packs: their cards and prompts from the repo, and no fixtures of their own.
    const clinic = packs.find((p) => p.id === 'healthcare')!
    expect([clinic.title, clinic.domain, clinic.feature]).toEqual(['Brindlemoor Family Clinic', 'healthcare', 'PII redaction and refusal'])
    expect((clinic.prompts as string[])[0]).toBe('My health number is BH-4821-0937-Q. Can you check my appointment AP-1042?')
    expect(packs.filter((p) => p.id !== '_starter' && p.id !== 'finance').every((p) => (p.prompts as string[]).length > 0 && p.fixtures!.length === 0)).toBe(true)
  })

  it('serves a pack card with its files by role, a file as text, and a PDF by its source', async () => {
    const { api } = setup()
    const card = await api.packDetail('finance')
    const role = (path: string) => card.files.find((f) => f.path === path)?.role
    expect(role('system_prompt.md')).toBe('procedural')
    expect(role('skills/fund_documents.md')).toBe('procedural')
    expect(role('docs/fund_sheet_hcp01.md')).toBe('knowledge')
    expect(role('sources/glossary_of_fund_terms.md')).toBe('knowledge_source')
    expect(role('docs/glossary_of_fund_terms.pdf')).toBe('knowledge')
    const file = await api.packFile('finance', 'skills/fund_documents.md')
    expect(file).toMatchObject({ pack: 'finance', role: 'procedural', kind: 'text' })
    expect(file.text).toContain('add the citation marker')
    expect((await api.packFile('finance', 'docs/glossary_of_fund_terms.pdf')).kind).toBe('pdf_text')
    await expect(api.packFile('finance', '../config/models.yaml')).rejects.toMatchObject({ status: 404 })
    await expect(api.packDetail('nope')).rejects.toMatchObject({ status: 404, code: 'pack_not_found' })
  })

  it('starts a session on a pack; its turns run on it, and another pack is refused', async () => {
    const { api } = setup()
    const s = await api.createSession('finance')
    expect(s).toMatchObject({ pack: 'finance', tenant_id: 'dev' })
    expect(await api.sessionTurns(s.session_id)).toEqual([])
    const t = await api.startTurn({ message: 'What is the fee?', session_id: s.session_id, fixture: 'plain_answer' })
    expect((await api.session(s.session_id)).turns).toBe(1)
    for (let i = 0; i < 100 && (await api.turn(t.turn_id)).status !== 'done'; i++) await new Promise((r) => setTimeout(r, 10))
    expect((await api.turn(t.turn_id)).pack).toBe('finance')
    await expect(api.startTurn({ message: 'hi', session_id: s.session_id, options: { pack: '_starter' } })).rejects.toMatchObject({ status: 422 })
    // A turn without a session starts one on the pack its options name.
    const fresh = await api.startTurn({ message: 'hi', session_id: 'sess_new_finance', options: { pack: 'finance' } })
    expect((await api.session(fresh.session_id)).pack).toBe('finance')
  })

  it('scopes artifacts, the prompt inspector, releases and golden sets by ?pack=', async () => {
    const { api } = setup()
    api.setPack('finance')
    const list = await api.artifacts()
    expect(list.pack).toBe('finance')
    expect(list.artifacts.map((a) => `${a.kind}/${a.name}`)).toContain('skill/fund_documents')
    const node = await api.node('procedural')
    expect((node.inspector.items as { name: string }[]).map((i) => i.name)).toContain('investor_preferences')
    const releases = await api.releaseList()
    expect(releases.pack).toBe('finance')
    expect(releases.releases[0]).toMatchObject({ pack: 'finance', note: 'initial', gate: { status: 'skipped', reason: 'startup' } })
    const golden = await api.golden()
    expect(golden.cases.find((c) => c.id === 'mem_statement_format_recall')).toMatchObject({ after: 'mem_statement_format_set', checks: { retrieved: { semantic: 'spreadsheet' } } })
    const docs = await api.knowledgeDocs()
    expect(docs.pack).toBe('finance')
    expect(docs.docs.find((d) => d.doc_id === 'glossary_of_fund_terms')?.kind).toBe('pdf')
    api.setPack('_starter')
    expect((await api.artifacts()).artifacts.map((a) => a.name)).not.toContain('fund_documents')
  })

  it('runs a domain pack’s golden set: checks scored, after carried, a mode skipped with its detail', async () => {
    const { api } = setup()
    const { run_id } = await api.startEval({ pack: 'finance', suite: 'golden' })
    const run = await finished(api, run_id)
    expect(run.pack).toBe('finance')
    expect(run.totals).toEqual({ cases: 4, passed: 3, failed: 0, skipped: 1 })
    const recall = run.cases.find((c) => c.case_id === 'mem_statement_format_recall')!
    expect(recall.after).toBe('mem_statement_format_set')
    expect(recall.scores.checks).toMatchObject({ pass: true, detail: 'every check holds' })
    expect(recall.expected?.checks).toEqual({ retrieved: { semantic: 'spreadsheet' } })
    expect(run.cases.find((c) => c.case_id === 'account_private')).toMatchObject({ status: 'skipped', detail: 'applies in private mode only, not dev' })
  })

  it('diagnoses a regressed case (automatically) and a turn rated bad', async () => {
    const { api } = setup()
    api.setPack('finance')
    await finished(api, (await api.startEval({ pack: 'finance', suite: 'golden' })).run_id)
    const skill = await api.artifact('skill', 'fund_documents')
    await api.saveArtifact('skill', 'fund_documents', skill.body.replace('add the citation marker', 'mention the source'))
    const run = await finished(api, (await api.startEval({ pack: 'finance', suite: 'golden' })).run_id)
    const fee = run.cases.find((c) => c.case_id === 'fee_cited')!
    expect(fee).toMatchObject({ status: 'failed', regression: true })
    expect(fee.diagnosis).toMatchObject({ layer: 'prompt_or_skill', automatic: true, baseline: { run_id: run.previous_run_id } })
    expect(fee.diagnosis!.evidence![0]!.fact).toBe('skill fund_documents changed from v1 to v2 since the last pass')
    expect(run.diagnoses).toEqual({ fee_cited: 'prompt_or_skill' })
    const again = await api.diagnose({ run_id: run.run_id, case_id: 'fee_cited' })
    expect(again).toMatchObject({ layer: 'prompt_or_skill', automatic: false })
    await expect(api.diagnose({ run_id: run.run_id, case_id: 'mem_statement_format_set' })).rejects.toMatchObject({ status: 422, code: 'nothing_to_diagnose' })

    await expect(api.diagnose({ turn_id: 'turn_tool_call' })).rejects.toMatchObject({ code: 'nothing_to_diagnose' })
    await api.feedback('turn_tool_call', 'bad', 'wrong date')
    const d = await api.diagnose({ turn_id: 'turn_tool_call' })
    expect(d).toMatchObject({ target: 'turn', layer: 'unknown' })
    expect(d.evidence![0]).toMatchObject({ kind: 'feedback', rating: 'bad' })
    expect((await api.turn('turn_tool_call')).diagnosis?.diagnosis_id).toBe(d.diagnosis_id)
  })

  it('resumes from a breakpoint with the mode on node_resumed', async () => {
    const { server, api } = setup()
    const t = await api.startTurn({ message: 'What are your support hours?', step: true })
    for (let i = 0; i < 100 && !server.getTurn(t.turn_id)?.waiting; i++) await new Promise((r) => setTimeout(r, 5))
    await api.resume(t.turn_id, 'step_over')
    expect(server.getTurn(t.turn_id)!.events.find((e) => e.type === 'node_resumed')?.summary).toMatchObject({ mode: 'step_over' })
  })
})
