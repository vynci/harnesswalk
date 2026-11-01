import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { rehypeReplyMarks, safeUrl } from '@/lib/markdown'
import { MARKDOWN_REPLY } from '@/mock/markdown'
import { Markdown } from './markdown'

const html = (text: string, props: Partial<Parameters<typeof Markdown>[0]> = {}) => renderToStaticMarkup(<Markdown text={text} {...props} />)

describe('Markdown', () => {
  it('renders the fixture: headings, lists, a table, code and links', () => {
    const out = html(MARKDOWN_REPLY)
    expect(out).toContain('<h2>Order summary</h2>')
    expect(out).toContain('<strong>A-1001</strong>')
    expect(out).toContain('<em>one</em>')
    expect(out).toMatch(/<ul>[\s\S]*<li>Shipped on 2026-10-30<\/li>[\s\S]*<ul>/)
    expect(out).toMatch(/<ol>[\s\S]*<li>Check the tracking page.<\/li>/)
    // GFM table, in its scroll wrapper, with the column alignment kept.
    expect(out).toContain('data-testid="markdown-table"')
    expect(out).toMatch(/<th[^>]*>Order<\/th>/)
    expect(out).toMatch(/<td[^>]*style="text-align:right"[^>]*>\$42.00<\/td>/)
    // Fenced code on the Code Block, with its language and a copy button.
    expect(out).toContain('data-testid="markdown-code"')
    expect(out).toContain('data-language="json"')
    expect(out).toContain('{&quot;order&quot;: &quot;A-1001&quot;, &quot;status&quot;: &quot;shipped&quot;}')
    expect(out).toContain('<code>UPS</code>')
    // Links open in a new tab without handing over the opener.
    expect(out).toContain('href="https://example.com/returns" target="_blank" rel="noopener noreferrer"')
    expect(out).toContain('(opens in a new tab)')
  })

  it('never renders raw HTML', () => {
    const out = html('Hello <script>alert(1)</script> <b>bold</b> <img src=x onerror=alert(1)>\n\n<div onclick="x()">block</div>')
    expect(out).not.toContain('<script')
    expect(out).not.toContain('<b>')
    expect(out).not.toContain('onerror')
    expect(out).not.toContain('onclick')
    expect(out).not.toContain('<div onclick')
  })

  it('drops unsafe link schemes and keeps safe ones', () => {
    expect(safeUrl('javascript:alert(1)')).toBe('')
    expect(safeUrl('data:text/html,<b>x</b>')).toBe('')
    expect(safeUrl('vbscript:x')).toBe('')
    expect(safeUrl('https://example.com/a?b=1')).toBe('https://example.com/a?b=1')
    expect(safeUrl('mailto:a@example.com')).toBe('mailto:a@example.com')
    expect(safeUrl('#section')).toBe('#section')
    const out = html('[click](javascript:alert(1)) and [ok](https://example.com)')
    expect(out).not.toContain('javascript:')
    expect(out).toContain('<span>click</span>')
  })

  it('is streaming-safe: every prefix of the fixture renders without throwing', () => {
    for (let i = 0; i <= MARKDOWN_REPLY.length; i += 7) expect(() => html(MARKDOWN_REPLY.slice(0, i))).not.toThrow()
    // An unclosed fence is code to the end while it streams.
    const partial = html('Before\n\n```python\nprint("hi")')
    expect(partial).toContain('data-language="python"')
    expect(partial).toContain('print(&quot;hi&quot;)')
  })

  it('marks cited numbers and placeholders outside code, and leaves the rest as text', () => {
    const plugins = [rehypeReplyMarks(new Set([1]))]
    const out = html('Fees are 2% [1], not [2]. Mail [EMAIL_1].\n\n`[1]` in code stays.', { rehypePlugins: plugins })
    expect(out).toContain('data-hw-cite="1"')
    expect(out).not.toContain('data-hw-cite="2"')
    expect(out).toContain('[2]')
    expect(out).toMatch(/<mark[^>]*data-testid="placeholder"[^>]*>\[EMAIL_1\]<\/mark>/)
    expect(out).toContain('<code>[1]</code>')
  })
})
