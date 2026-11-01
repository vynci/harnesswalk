import { memo, type ReactNode } from 'react'
import ReactMarkdown, { type Components, type Options } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CodeBlock, CodeBlockActions, CodeBlockContent, CodeBlockCopyButton, CodeBlockHeader, CodeBlockLine, CodeBlockTitle } from '@/components/ui/code-block'
import { useHighlightedCode } from '@/hooks/use-highlighted-code'
import { safeUrl, textOf, type HastNode } from '@/lib/markdown'
import { cn } from '@/lib/utils'
import { Prose } from './prose'

// GitHub-flavoured markdown for model replies and knowledge documents: react-markdown with
// remark-gfm, fenced code on the shadcn Code Block, links in a new tab, and no raw HTML (it is
// dropped, never rendered). It re-parses the whole text on every render, so a reply that is
// still streaming renders as far as it has arrived: an unclosed fence is code to the end, a
// half-written table is a paragraph until its delimiter row lands.

/** A fenced block: language title, copy button, highlighted lines (shiki loads on first use). */
function FencedCode({ code, language }: { code: string; language: string }) {
  const lines = useHighlightedCode(code, language)
  return (
    <CodeBlock size="sm" className="not-first:mt-[0.6em]" data-language={language} data-testid="markdown-code">
      <CodeBlockHeader>
        <CodeBlockTitle className="font-mono text-xs">{language === 'text' ? 'code' : language}</CodeBlockTitle>
        <CodeBlockActions>
          <CodeBlockCopyButton value={code} />
        </CodeBlockActions>
      </CodeBlockHeader>
      <CodeBlockContent className="max-h-80" aria-label={`${language} code`}>
        {lines.map((line, i) => (
          <CodeBlockLine key={i}>{line}</CodeBlockLine>
        ))}
      </CodeBlockContent>
    </CodeBlock>
  )
}

function languageOf(code: HastNode | undefined): string {
  const cls = code?.properties?.className
  const list = Array.isArray(cls) ? cls.map(String) : typeof cls === 'string' ? cls.split(/\s+/) : []
  const lang = list.find((c) => c.startsWith('language-'))?.slice('language-'.length)
  return lang || 'text'
}

const LINK = 'underline underline-offset-4 hover:text-foreground'

const BASE: Components = {
  a: ({ href, children }) => {
    const url = href ? safeUrl(href) : ''
    if (!url) return <span>{children}</span>
    if (url.startsWith('#')) {
      return (
        <a href={url} className={LINK}>
          {children}
        </a>
      )
    }
    return (
      <a href={url} target="_blank" rel="noopener noreferrer" className={LINK}>
        {children}
        <span className="sr-only"> (opens in a new tab)</span>
      </a>
    )
  },
  pre: ({ node }) => {
    const code = (node as HastNode | undefined)?.children?.find((c) => c.type === 'element' && c.tagName === 'code')
    return <FencedCode code={textOf(code)} language={languageOf(code)} />
  },
  table: ({ children }) => (
    <div className="overflow-x-auto rounded-lg ring-1 ring-foreground/10" data-testid="markdown-table">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  ),
  th: ({ children, style }) => (
    <th className="border-b bg-muted/50 px-3 py-1.5 font-medium" style={style}>
      {children}
    </th>
  ),
  td: ({ children, style }) => (
    <td className="border-b px-3 py-1.5 align-top tabular-nums [tr:last-child>&]:border-b-0" style={style}>
      {children}
    </td>
  ),
  img: ({ alt }) => <span className="text-muted-foreground">[image{alt ? `: ${alt}` : ''}]</span>,
}

const MARKDOWN = cn(
  '[&_h1]:mt-[0.9em] [&_h2]:mt-[0.9em] [&_h3]:mt-[0.8em] [&>:first-child]:mt-0',
  '[&_li+li]:mt-0.5 [&_li>p]:mt-0 [&_blockquote]:border-l-2 [&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground',
  '[&_hr]:my-3 [&_hr]:border-border',
)

export type MarkdownProps = {
  text: string
  className?: string
  /** Extra hast plugins (the chat's citation chips and placeholders). */
  rehypePlugins?: Options['rehypePlugins']
  /** Element renderers merged over the defaults. */
  components?: Components
} & { children?: never }

/**
 * Markdown on the Prose wrapper. Memoised on its inputs, so a streaming reply re-renders only
 * when its text grows. Raw HTML in the source is skipped, never rendered.
 */
export const Markdown = memo(function Markdown({ text, className, rehypePlugins, components }: MarkdownProps): ReactNode {
  return (
    <Prose className={cn(MARKDOWN, className)} data-testid="markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={rehypePlugins} components={components ? { ...BASE, ...components } : BASE} skipHtml urlTransform={safeUrl}>
        {text}
      </ReactMarkdown>
    </Prose>
  )
})
