// Knowledge citations in the chat: the reply's `[n]` markers become chips, and a Sources list
// under the reply names every cited chunk. Both open the document reader at that chunk
// (GET /v1/knowledge/docs/{doc}). The data is `turn_end.summary.citations`, nothing else.
import { BookOpen } from 'lucide-react'
import type { ReactNode } from 'react'
import type { Citation } from '@/api/types'
import {
  Trace,
  TraceCollapsible,
  TraceCollapsibleContent,
  TraceCollapsibleTrigger,
  TraceDescription,
  TraceIcon,
  TraceLine,
  TraceTitle,
} from '@/components/ui/trace'
import { cn } from '@/lib/utils'
import { splitPlaceholders } from '@/state/turnFacts'

/** One `[n]` chip. */
export function CitationChip({ citation, onOpen, className }: { citation: Citation; onOpen: (c: Citation) => void; className?: string }) {
  return (
    <button
      type="button"
      className={cn(
        'mx-0.5 inline-flex h-4 min-w-4 cursor-pointer items-center justify-center rounded-sm bg-kind-store/15 px-1 align-baseline font-mono text-[10px] leading-none font-medium text-foreground ring-1 ring-kind-store/40 ring-inset hover:bg-kind-store/25 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
        className,
      )}
      onClick={() => onOpen(citation)}
      aria-label={`Source ${citation.n}: ${citation.citation}. Open the document at this passage`}
      title={citation.citation}
      data-testid="citation-chip"
      data-n={citation.n}
      data-doc={citation.doc}
      data-chunk={citation.chunk}
    >
      {citation.n}
    </button>
  )
}

const MARKER = /\[(\d{1,3})\]/g

/**
 * Reply text with its `[n]` markers as chips (only the numbers the turn cited) and its
 * redaction placeholders (`[EMAIL_1]`) highlighted, so the reader sees both at a glance.
 */
export function ReplyText({ text, citations, onOpen }: { text: string; citations: Citation[]; onOpen: (c: Citation) => void }) {
  const byN = new Map(citations.map((c) => [c.n, c]))
  const out: ReactNode[] = []
  let key = 0
  for (const part of splitPlaceholders(text)) {
    if (part.placeholder) {
      out.push(
        <mark key={key++} className="rounded-sm bg-state-waiting/15 px-0.5 font-mono text-[0.92em] text-foreground ring-1 ring-state-waiting/40 ring-inset" data-testid="placeholder">
          {part.text}
        </mark>,
      )
      continue
    }
    let last = 0
    for (const m of part.text.matchAll(MARKER)) {
      const c = byN.get(Number(m[1]))
      if (!c) continue
      if (m.index! > last) out.push(part.text.slice(last, m.index))
      out.push(<CitationChip key={key++} citation={c} onOpen={onOpen} />)
      last = m.index! + m[0].length
    }
    if (last < part.text.length) out.push(part.text.slice(last))
  }
  return <>{out}</>
}

/** shadcn's Sources pattern on Trace: "Cited 2 sources", opening to one line per chunk. */
export function Sources({ citations, onOpen }: { citations: Citation[]; onOpen: (c: Citation) => void }) {
  if (citations.length === 0) return <div data-slot="citations" hidden />
  const docs = new Set(citations.map((c) => c.doc)).size
  return (
    <Trace data-slot="citations" className="text-xs" data-testid="sources">
      <TraceCollapsible defaultOpen>
        <TraceCollapsibleTrigger aria-label="Sources the reply cites">
          <TraceLine className="text-xs">
            <TraceIcon>
              <BookOpen />
            </TraceIcon>
            <TraceTitle>
              Cited {citations.length} passage{citations.length === 1 ? '' : 's'}
            </TraceTitle>
            <TraceDescription>
              from {docs} document{docs === 1 ? '' : 's'}
            </TraceDescription>
          </TraceLine>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent className="mt-1.5">
          <Trace className="gap-1.5">
            {citations.map((c) => (
              <button
                key={`${c.n}`}
                type="button"
                onClick={() => onOpen(c)}
                aria-label={`Open source ${c.n}: ${c.citation}`}
                data-testid="source-line"
                data-doc={c.doc}
                data-chunk={c.chunk}
              >
                <TraceLine className="text-xs">
                  <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-sm bg-kind-store/15 px-1 font-mono text-[10px] text-foreground ring-1 ring-kind-store/40 ring-inset" aria-hidden>
                    {c.n}
                  </span>
                  <TraceTitle>{c.citation}</TraceTitle>
                  <TraceDescription className="font-mono">
                    {c.doc}#{c.chunk}
                  </TraceDescription>
                </TraceLine>
              </button>
            ))}
          </Trace>
        </TraceCollapsibleContent>
      </TraceCollapsible>
    </Trace>
  )
}
