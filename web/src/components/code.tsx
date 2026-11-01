import type { ComponentProps, Ref } from 'react'
import { CodeBlock, CodeBlockContent, CodeBlockLine } from '@/components/ui/code-block'
import { useHighlightedCode } from '@/hooks/use-highlighted-code'

/**
 * Highlighted, read-only code on the shadcn Code Block: one CodeBlockLine per line, coloured by
 * shiki through `useHighlightedCode` (languages load on first use, see src/lib/highlighter.tsx).
 */
export function Code({
  code,
  language,
  lineNumbers = false,
  wrap = false,
  size = 'default',
  label,
  className,
  contentClassName,
  contentRef,
  highlightLine,
  ...props
}: {
  code: string
  language: string
  lineNumbers?: boolean
  wrap?: boolean
  size?: 'default' | 'sm'
  /** The accessible name of the scrollable code (it takes focus for keyboard scrolling). */
  label?: string
  contentClassName?: string
  contentRef?: Ref<HTMLPreElement>
  /** A 1-based line drawn highlighted (a code walk link's symbol). */
  highlightLine?: number | null
} & Omit<ComponentProps<typeof CodeBlock>, 'children' | 'size'>) {
  const lines = useHighlightedCode(code, language)
  return (
    <CodeBlock size={size} className={className} data-language={language} {...props}>
      <CodeBlockContent ref={contentRef} lineNumbers={lineNumbers} wrap={wrap} aria-label={label} className={contentClassName}>
        {lines.map((line, i) => (
          <CodeBlockLine key={i} variant={i + 1 === highlightLine ? 'highlighted' : 'default'} data-target={i + 1 === highlightLine ? '' : undefined}>
            {line}
          </CodeBlockLine>
        ))}
      </CodeBlockContent>
    </CodeBlock>
  )
}
