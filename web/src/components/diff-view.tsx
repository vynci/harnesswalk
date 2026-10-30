import type { ComponentProps } from 'react'
import { CodeBlock, CodeBlockContent, CodeBlockLine } from '@/components/ui/code-block'
import { cn } from '@/lib/utils'
import type { DiffLine } from '@/lib/diff'

/**
 * A diff on the shadcn Code Block: added and removed lines as its `added` and `removed` line
 * variants (the `+` and `−` gutter marks come from the component), hunk headers muted.
 */
export function DiffView({
  lines,
  label,
  className,
  contentClassName,
  ...props
}: { lines: DiffLine[]; label: string; contentClassName?: string } & Omit<ComponentProps<typeof CodeBlock>, 'children'>) {
  return (
    <CodeBlock size="sm" className={className} data-testid="diff-view" {...props}>
      <CodeBlockContent wrap aria-label={label} className={cn('max-h-80', contentClassName)}>
        {lines.length === 0 ? (
          <CodeBlockLine className="text-muted-foreground">No changes.</CodeBlockLine>
        ) : (
          lines.map((l, i) =>
            l.type === 'hunk' ? (
              <CodeBlockLine key={i} variant="highlighted" className="text-muted-foreground">
                {l.text}
              </CodeBlockLine>
            ) : (
              <CodeBlockLine key={i} variant={l.type === 'same' ? 'default' : l.type} data-diff={l.type}>
                {l.type === 'same' ? l.text : (
                  <>
                    <span className="sr-only">{l.type === 'added' ? 'added: ' : 'removed: '}</span>
                    {l.text}
                  </>
                )}
              </CodeBlockLine>
            ),
          )
        )}
      </CodeBlockContent>
    </CodeBlock>
  )
}
