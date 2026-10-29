import { describe, expect, it } from 'vitest'
import { detailsTabLabel, sourceExtras } from './labels'
import { isAuditPayload, isDocList, isProposalList, isScopedRecords } from './shapes'

describe('inspector views are picked by payload shape', () => {
  it('a code node with its own state gets a tab named after what it holds', () => {
    const base = { type: 'source' as const, path: 'x.py', language: 'python', content: '', last_summary: null }
    expect(detailsTabLabel(base)).toBeNull()
    expect(detailsTabLabel({ ...base, last_writes: [], pending: [], settings: {} })).toBe('Writes')
    expect(detailsTabLabel({ ...base, last_context: { slices: {} } })).toBe('Context')
    expect(detailsTabLabel({ ...base, anything: 1 })).toBe('State')
    expect(sourceExtras({ ...base, last_writes: [] })).toEqual(['last_writes'])
    expect(detailsTabLabel({ type: 'store' })).toBe('Records')
  })

  it('audit chains, proposals, documents and scoped records', () => {
    expect(isAuditPayload({ type: 'store', verify: { ok: true, records: 0 }, records: [] })).toBe(true)
    expect(isAuditPayload({ type: 'store', counts: {} })).toBe(false)
    expect(isProposalList([{ proposal_id: 'p', store: 'semantic', status: 'pending' }])).toBe(true)
    expect(isProposalList([{ approval_id: 'a' }])).toBe(false)
    expect(isDocList([{ doc_id: 'returns_policy', title: 'Returns' }])).toBe(true)
    expect(isDocList([])).toBe(false)
    expect(isScopedRecords([{ id: 'f', scope: 'pack' }])).toBe(true)
    expect(isScopedRecords([{ id: 'f' }])).toBe(false)
  })
})
