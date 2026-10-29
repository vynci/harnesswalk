import { useMemo, useRef } from 'react'
import { cn } from '@/lib/utils'
import { highlight } from './highlight'

const LINE_PX = 18

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
  const html = useMemo(() => highlight(code, language), [code, language])
  const lines = useMemo(() => code.split('\n').length, [code])
  const scroller = useRef<HTMLDivElement>(null)

  const jump = (line: number) => {
    const el = scroller.current
    if (el) el.scrollTo({ top: Math.max(0, (line - 3) * LINE_PX), behavior: 'smooth' })
  }

  return (
    <div className={cn('flex min-h-0 flex-col gap-2', className)}>
      {symbols && symbols.length > 0 && (
        <nav aria-label="Symbols" className="flex flex-wrap gap-1">
          {symbols.map((s) => (
            <button
              key={`${s.name}:${s.line}`}
              type="button"
              className="rounded border px-1.5 py-0.5 font-mono text-[11px] hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              onClick={() => jump(s.line)}
              title={`Jump to line ${s.line}`}
            >
              {s.name}
              <span className="ml-1 text-muted-foreground">:{s.line}</span>
            </button>
          ))}
        </nav>
      )}
      <div ref={scroller} className="min-h-0 flex-1 overflow-auto rounded-md border bg-muted/40" tabIndex={0} aria-label={label} data-testid="code-view">
        <div className="flex text-[12px]" style={{ lineHeight: `${LINE_PX}px` }}>
          <pre aria-hidden className="sticky left-0 shrink-0 bg-muted/80 px-2 py-2 text-right text-muted-foreground select-none">
            {Array.from({ length: lines }, (_, i) => i + 1).join('\n')}
          </pre>
          <pre className="flex-1 px-3 py-2">
            <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />
          </pre>
        </div>
      </div>
    </div>
  )
}
