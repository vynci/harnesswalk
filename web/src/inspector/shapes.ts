// Shape tests for inspector payloads: which view draws a field is decided by what it holds,
// never by the node it came from (the drift rule in web/README.md).
import type { MemoryProposal } from '@/api/types'

export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v)
}
/** An audit export: records with `hash` and `prev_hash`, and a `verify` result. */
export function isAuditPayload(ins: Record<string, unknown>): boolean {
  return isRecord(ins.verify) && Array.isArray(ins.records)
}
export function isProposalList(v: unknown): v is MemoryProposal[] {
  return Array.isArray(v) && v.every((p) => isRecord(p) && typeof p.proposal_id === 'string')
}
export function isDocList(v: unknown): boolean {
  return Array.isArray(v) && v.length > 0 && v.every((d) => isRecord(d) && typeof d.doc_id === 'string')
}
export function isScopedRecords(v: unknown): v is Record<string, unknown>[] {
  return Array.isArray(v) && v.every((r) => isRecord(r) && 'scope' in r)
}

