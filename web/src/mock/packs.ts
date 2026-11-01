// The dataset packs of the in-browser mock (P5): the starter pack and one domain pack (finance),
// with their cards, their files from the repo (bundled into mock builds only) and their own fake
// fixtures, in the shapes of GET /v1/packs, GET /v1/packs/{id} and GET /v1/packs/{id}/files/{path}.
import type { PackDetail, PackFile, PackSummary } from '@/api/types'
import { repoFile, repoFilesUnder } from './repo'

export const DEFAULT_PACK = '_starter'

/** Fixtures no pack owns: every pack's sessions may pick them. */
export const SHARED_FIXTURES = ['plain_answer']

interface MockPack {
  id: string
  title: string
  domain: string
  feature: string
  summary: string
  demonstrates: { id: string; nodes: string[]; summary: string }
  prompts: string[]
  fixtures: string[]
  tools: string[]
}

/** The list items of a prompts.md (`1. text`), as the API reads them. */
function promptsOf(path: string): string[] {
  const text = repoFile(path) ?? ''
  return [...text.matchAll(/^\s*(?:\d+\.|[-*])\s+(.+)$/gm)].map((m) => m[1]!.trim()).filter((p) => !p.startsWith('*'))
}

const PACKS: MockPack[] = [
  {
    id: '_starter',
    title: 'Example Store',
    domain: 'ecommerce',
    feature: 'A tour of every node',
    summary: 'A small outdoor gear shop that touches every node of the harness once.',
    demonstrates: { id: 'tour', nodes: [], summary: 'Every node, one prompt each.' },
    // The chat's own fallback prompts, so the mock reads the same with or without packs.
    prompts: ['What are your support hours?', 'Where is order A-1001?', 'Please cancel order A-1002.', 'Can I return order A-1003, the Trailhead backpack?'],
    fixtures: ['tool_call', 'approval_pause'],
    tools: ['lookup_order', 'cancel_order'],
  },
  {
    id: 'finance',
    title: 'Hollowmere Capital Partners',
    domain: 'finance',
    feature: 'Citations and the memory writer',
    summary: 'An investor relations assistant for four fictional funds, answering from fund documents with citations.',
    demonstrates: { id: 'citations_and_memory', nodes: [], summary: 'Cited answers, and a remembered preference recalled in a later session.' },
    prompts: promptsOf('datasets/finance/prompts.md'),
    fixtures: ['cite_mgmt_fee_hcp01', 'mem_reporting_currency_set', 'mem_statement_format_set', 'mem_statement_format_recall'],
    tools: ['fund_nav', 'capital_calls'],
  },
]

export const PACK_IDS = PACKS.map((p) => p.id)

/** Every fixture a turn may ask for (capabilities.fixtures): the shared ones and every pack's. */
export function allFixtures(): string[] {
  return [...SHARED_FIXTURES, ...PACKS.flatMap((p) => p.fixtures)]
}

/** A pack file's role, by its path, as the API assigns it. */
export function roleOf(path: string): string {
  if (path === 'system_prompt.md' || path.startsWith('skills/')) return 'procedural'
  if (path.startsWith('docs/')) return 'knowledge'
  if (path.startsWith('sources/')) return 'knowledge_source'
  if (path.startsWith('fixtures/')) return 'fixture'
  const top: Record<string, string> = { 'tools.yaml': 'tools', 'guardrails.yaml': 'guardrails', 'facts.jsonl': 'semantic', 'episodes.jsonl': 'episodic', 'golden.jsonl': 'golden' }
  if (top[path]) return top[path]
  return path.includes('/') ? 'other' : 'card'
}

/**
 * The pack's files: the bundled text files, plus each PDF the pack ships (the mock bundles only
 * text, so a PDF is listed by the markdown it was rendered from, `sources/<name>.md`).
 */
export function packFiles(id: string): (PackFile & { text: string; kind: string })[] {
  const prefix = `datasets/${id}/`
  const files = repoFilesUnder(prefix).map(([path, text]) => ({ path: path.slice(prefix.length), bytes: new TextEncoder().encode(text).length, text }))
  const pdfs = files.filter((f) => f.path.startsWith('sources/')).map((f) => ({ path: `docs/${f.path.slice('sources/'.length).replace(/\.md$/, '.pdf')}`, bytes: Math.round(f.bytes * 1.2), text: f.text }))
  return [...files, ...pdfs]
    .map((f) => ({ ...f, role: roleOf(f.path), kind: f.path.endsWith('.pdf') ? 'pdf_text' : 'text' }))
    .sort((a, b) => a.path.localeCompare(b.path))
}

export function packSummary(id: string, loaded: Set<string>): PackSummary | null {
  const p = PACKS.find((x) => x.id === id)
  if (!p) return null
  const files = packFiles(id)
  const count = (role: string) => files.filter((f) => f.role === role).length
  return {
    id: p.id,
    title: p.title,
    domain: p.domain,
    feature: p.feature,
    prompts: p.prompts,
    counts: { docs: count('knowledge'), facts: 0, episodes: 0, skills: files.filter((f) => f.path.startsWith('skills/')).length, tools: p.tools.length, golden: 0 },
    default: p.id === DEFAULT_PACK,
    loaded: loaded.has(p.id),
    fixtures: [...p.fixtures],
  }
}

export function packDetail(id: string, loaded: Set<string>): PackDetail | null {
  const summary = packSummary(id, loaded)
  const p = PACKS.find((x) => x.id === id)
  if (!summary || !p) return null
  const files = packFiles(id)
  return {
    ...summary,
    summary: p.summary,
    tier: p.id === DEFAULT_PACK ? 'starter' : 'domain',
    demonstrates: p.demonstrates,
    licence: 'CC0-1.0',
    tools: p.tools,
    size_bytes: files.reduce((n, f) => n + f.bytes, 0),
    files: files.map(({ path, bytes, role }) => ({ path, bytes, role })),
  }
}
