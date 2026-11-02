import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { KnowledgeDocDetail } from '@/api/types'
import { PdfView, ReaderBody, ViewSwitch } from './DocReader'
import type { ReaderView } from './docView'

const MD: KnowledgeDocDetail = {
  doc_id: 'fees',
  title: 'Fees',
  path: 'fees.md',
  kind: 'markdown',
  version: 'abc123',
  scope: 'pack',
  text: '# Fees\n\nWhat we charge.\n\n## Late fee\n\nTwenty-five dollars.\n\n## Refunds\n\nWithin 30 days.',
  chunks: [
    { position: 0, heading: '', text: 'What we charge.', tokens: 4, citation: 'Fees' },
    { position: 1, heading: 'Late fee', text: 'Twenty-five dollars.', tokens: 5, citation: 'Fees, Late fee' },
    { position: 2, heading: 'Refunds', text: 'Within 30 days.', tokens: 4, citation: 'Fees, Refunds' },
  ],
  file: { pack: 'healthcare', path: 'docs/fees.md', url: '/v1/packs/healthcare/files/docs/fees.md?raw=1', media_type: 'text/markdown; charset=utf-8' },
}
const PDF: KnowledgeDocDetail = {
  ...MD,
  doc_id: 'billing_faq',
  path: 'billing_faq.pdf',
  kind: 'pdf',
  text: 'Billing FAQ page one.\n\nPage two.',
  chunks: [
    { position: 0, heading: 'page 1', text: 'Billing FAQ page one.', tokens: 5, citation: 'Billing FAQ, page 1' },
    { position: 1, heading: 'page 2', text: 'Page two.', tokens: 2, citation: 'Billing FAQ, page 2' },
  ],
  file: { pack: 'healthcare', path: 'docs/billing_faq.pdf', url: '/v1/packs/healthcare/files/docs/billing_faq.pdf?raw=1', media_type: 'application/pdf' },
}

const base = (u: string) => `http://api.test${u}`
const body = (doc: KnowledgeDocDetail, view: ReaderView, extra: { chunk?: number; rules?: boolean } = {}) =>
  renderToStaticMarkup(<ReaderBody doc={doc} view={view} chunk={extra.chunk} rules={extra.rules ?? true} resolveUrl={base} />)
const control = (doc: KnowledgeDocDetail, view: ReaderView) =>
  renderToStaticMarkup(<ViewSwitch doc={doc} view={view} onView={() => {}} rules onRules={() => {}} />)

describe('the reader view switch', () => {
  it('offers Document and Chunks, the current one pressed, and the boundary switch for markdown', () => {
    const doc = control(MD, 'document')
    expect(doc).toContain('data-view="document"')
    expect(doc).toMatch(/aria-checked="true"[^>]*>Document</)
    expect(doc).toMatch(/aria-checked="false"[^>]*>Chunks</)
    expect(doc).toContain('Chunk boundaries')
    const chunks = control(MD, 'chunks')
    expect(chunks).toMatch(/aria-checked="true"[^>]*>Chunks</)
    expect(chunks).not.toContain('Chunk boundaries')
    expect(control(PDF, 'document')).not.toContain('Chunk boundaries')
  })

  it('is not there for a document without a Document view (a PDF without its file)', () => {
    expect(control({ ...PDF, file: null }, 'chunks')).toBe('')
  })

  it('Document on markdown: the text rendered, a rule per chunk, the cited section marked', () => {
    const out = body(MD, 'document', { chunk: 1 })
    expect(out).toContain('data-view="document"')
    expect(out).toContain('<h1>Fees</h1>')
    expect(out).toContain('<h2>Late fee</h2>')
    expect(out.match(/data-testid="doc-boundary"/g)).toHaveLength(3)
    expect(out).toContain('chunk #1')
    expect(out).toMatch(/data-chunks="1" data-cited="true"/)
    expect(out).toMatch(/data-chunks="0" data-cited="false"/)
    expect(out).not.toContain('data-testid="doc-chunk"')
    expect(body(MD, 'document', { rules: false })).not.toContain('doc-boundary')
  })

  it('Chunks: every chunk with its heading and citation, the cited one marked', () => {
    const out = body(MD, 'chunks', { chunk: 2 })
    expect(out).toContain('data-view="chunks"')
    expect(out.match(/data-testid="doc-chunk"/g)).toHaveLength(3)
    expect(out).toMatch(/data-position="2" data-cited="true"/)
    expect(out).toContain('Fees, Refunds')
    expect(out).toContain('Introduction')
  })

  it('Document on a PDF: the browser viewer on the file URL, at the cited page', () => {
    const out = body(PDF, 'document')
    expect(out).toContain('type="application/pdf"')
    expect(out).toContain('data="http://api.test/v1/packs/healthcare/files/docs/billing_faq.pdf?raw=1"')
    expect(body(PDF, 'document', { chunk: 1 })).toContain('billing_faq.pdf?raw=1#page=2"')
  })

  it('other text is code', () => {
    const out = body({ ...MD, kind: 'text', text: 'plain words' }, 'document')
    expect(out).toContain('data-testid="doc-document"')
    expect(out).toContain('plain words')
    expect(out).not.toContain('<h1>')
  })
})

describe('the PDF fallback', () => {
  it('says the browser cannot show it and links to open and download the file', () => {
    const out = renderToStaticMarkup(<PdfView url="/f.pdf" name="billing_faq.pdf" />)
    expect(out).toContain('data-testid="doc-pdf-fallback"')
    expect(out).toContain('Your browser cannot display PDFs inline.')
    expect(out).toMatch(/<a href="\/f.pdf" target="_blank" rel="noopener noreferrer"[^>]*>.*Open in new tab<\/a>/)
    expect(out).toMatch(/<a href="\/f.pdf" download="billing_faq.pdf"[^>]*>.*Download<\/a>/)
    // The fallback is the <object>'s content: a browser with a PDF viewer never shows it.
    expect(out.indexOf('<object')).toBeLessThan(out.indexOf('doc-pdf-fallback'))
  })
})
