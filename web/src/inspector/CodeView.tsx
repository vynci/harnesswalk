import { useRef } from 'react'
import { Code } from '@/components/code'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const reduceMotion = () => !!globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Source on the shadcn Code Block with line numbers, plus a row of symbols that jump to their line. */
export function CodeView({
  code,
  language,
  symbols,
  className,
  label,
}: {
  code: string
  language: string
  symbols?: { name: string; line: number }[]
  className?: string
  label: string
}) {
  const scroller = useRef<HTMLPreElement>(null)

  const jump = (line: number) => {
    const pre = scroller.current
    const el = pre?.querySelectorAll<HTMLElement>('[data-slot=code-block-line]')[line - 1]
    if (!pre || !el) return
    const top = el.offsetTop - pre.offsetTop - 2 * el.offsetHeight
    pre.scrollTo({ top: Math.max(0, top), behavior: reduceMotion() ? 'auto' : 'smooth' })
  }

  return (
    <div className={cn('flex min-h-0 flex-col gap-2', className)}>
      {symbols && symbols.length > 0 && (
        <nav aria-label="Symbols" className="flex flex-wrap gap-1">
          {symbols.map((s) => (
            <Button
              key={`${s.name}:${s.line}`}
              type="button"
              variant="outline"
              size="xs"
              className="h-5 px-1.5 font-mono text-[11px] font-normal"
              onClick={() => jump(s.line)}
              title={`Jump to line ${s.line}`}
            >
              {s.name}
              <span className="-ml-1 text-muted-foreground">:{s.line}</span>
            </Button>
          ))}
        </nav>
      )}
      <Code
        code={code}
        language={language}
        lineNumbers
        size="sm"
        label={label}
        contentRef={scroller}
        className="min-h-0 flex-1"
        contentClassName="h-full text-[12px] leading-[18px]"
        data-testid="code-view"
      />
    </div>
  )
}
