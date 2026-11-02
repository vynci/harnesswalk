import { describe, expect, it } from 'vitest'
import type { KnowledgeDocDetail } from '@/api/types'
import { defaultView, docSegments, hasDocumentView, pageOf } from './docView'

const doc = (over: Partial<KnowledgeDocDetail>): KnowledgeDocDetail => ({
  doc_id: 'd',
  title: 'D',
  path: 'd.md',
  kind: 'markdown',
  version: 'v1',
  scope: 'pack',
  text: '# D\n\nText.',
  chunks: [],
  file: null,
  ...over,
})
const PDF_FILE = { pack: 'healthcare', path: 'docs/d.pdf', url: '/v1/packs/healthcare/files/docs/d.pdf?raw=1', media_type: 'application/pdf' }

describe('the reader view', () => {
  it('opens a markdown document or a PDF with its file on Document, anything else on Chunks', () => {
    expect(defaultView(doc({}))).toBe('document')
    expect(defaultView(doc({ kind: 'pdf', path: 'd.pdf', file: PDF_FILE }))).toBe('document')
    // A PDF without a file (a profile's docs_dir, an older server) has only its chunks.
    expect(hasDocumentView(doc({ kind: 'pdf', file: null }))).toBe(false)
    expect(defaultView(doc({ kind: 'pdf', file: undefined }))).toBe('chunks')
    // Other text: a Document view (code) exists, but the reader opens on the chunks.
    expect(hasDocumentView(doc({ kind: 'text' }))).toBe(true)
    expect(defaultView(doc({ kind: 'text' }))).toBe('chunks')
    expect(defaultView(doc({ text: '  ' }))).toBe('chunks')
  })

  it('reads the page of a PDF chunk', () => {
    expect(pageOf({ heading: 'page 3' })).toBe(3)
    expect(pageOf({ heading: 'Fees' })).toBeNull()
    expect(pageOf(undefined)).toBeNull()
  })
})

describe('docSegments', () => {
  const text = [
    '---',
    'owner: clinic',
    '---',
    '# Billing FAQ',
    '',
    'Intro under the title.',
    '',
    '## Fees',
    '### Late fee',
    'Twenty-five dollars.',
    '',
    '## Long section',
    'many words here',
    '## Last',
    'The end.',
    '## Trailing heading only',
  ].join('\n')
  const chunks = [
    { position: 0, heading: '' },
    { position: 1, heading: 'Late fee' },
    { position: 2, heading: 'Long section' },
    { position: 3, heading: 'Long section' },
    { position: 4, heading: 'Last' },
  ]

  it('cuts where chunking.py cuts: front matter dropped, the title its own, empty headings joined to the next', () => {
    const segs = docSegments(text, chunks)!
    expect(segs.map((s) => s.heading)).toEqual(['', 'Late fee', 'Long section', 'Last'])
    expect(segs.map((s) => s.positions)).toEqual([[0], [1], [2, 3], [4]])
    expect(segs[0]!.text).toBe('# Billing FAQ\n\nIntro under the title.')
    // `## Fees` has no text of its own: it is shown with the section under `### Late fee`.
    expect(segs[1]!.text).toBe('## Fees\n### Late fee\nTwenty-five dollars.')
    // A heading with nothing after it stays visible, at the end of the last section.
    expect(segs[3]!.text).toBe('## Last\nThe end.\n## Trailing heading only')
    expect(segs.map((s) => s.text).join('\n')).not.toContain('owner: clinic')
  })

  it('gives up (null) when the chunks do not line up with the sections', () => {
    expect(docSegments(text, chunks.slice(1))).toBeNull()
    expect(docSegments(text, [...chunks.slice(0, 4), { position: 4, heading: 'Other' }])).toBeNull()
    expect(docSegments('', [])).toBeNull()
  })

  it('takes the chunks in position order', () => {
    expect(docSegments('# T\n\nOne.\n\n## Two\nTwo.', [{ position: 1, heading: 'Two' }, { position: 0, heading: '' }])?.map((s) => s.positions)).toEqual([[0], [1]])
  })
})
