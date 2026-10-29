// Knowledge citations in the chat: the reply's `[n]` markers become chips, and a Sources list
// under the reply names every cited chunk. Both open the document reader at that chunk
// (GET /v1/knowledge/docs/{doc}). The data is `turn_end.summary.citations`, nothing else.
import { BookOpen } from 'lucide-react'
import type { ReactNode } from 'react'
import type { Citation } from '@/api/types'
import { Button } from '@/components/ui/button'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/components/ui/hover-card'
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

/** The number of a citation as a small round chip, shared by the inline citation and Sources. */
const NUMBER = 'h-5 min-w-5 rounded-full px-1.5 font-mono text-xs tabular-nums'

/**
 * One `[n]` in the reply: the AI Elements Inline Citation. Hovering shows the passage's citation
 * and where it lives; clicking opens the document reader at that chunk.
 */
export function CitationChip({ citation, onOpen, className }: { citation: Citation; onOpen: (c: Citation) => void; className?: string }) {
  return (
    <HoverCard openDelay={10} closeDelay={100}>
      <HoverCardTrigger asChild>
        <Button
          type="button"
          variant="secondary"
          size="xs"
          className={cn(NUMBER, 'mx-0.5 align-baseline', className)}
          onClick={() => onOpen(citation)}
          aria-label={`Source ${citation.n}: ${citation.citation}. Open the document at this passage`}
          data-testid="citation-chip"
          data-n={citation.n}
          data-doc={citation.doc}
          data-chunk={citation.chunk}
        >
          {citation.n}
        </Button>
      </HoverCardTrigger>
      <HoverCardContent side="top" className="flex w-64 flex-col gap-1 p-3">
        <p className="font-medium">{citation.citation}</p>
        <p className="font-mono text-xs text-muted-foreground">
          {citation.doc}#{citation.chunk}
        </p>
      </HoverCardContent>
    </HoverCard>
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
        <mark key={key++} className="rounded-md bg-state-waiting/15 px-1 font-mono text-[0.92em] text-foreground" data-testid="placeholder">
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

/**
 * The AI Elements Sources pattern on Trace: "Cited 2 passages", closed until opened, one line per
 * chunk opening the document reader (a Dialog) at that passage.
 */
export function Sources({ citations, onOpen }: { citations: Citation[]; onOpen: (c: Citation) => void }) {
  if (citations.length === 0) return <div data-slot="citations" hidden />
  const docs = new Set(citations.map((c) => c.doc)).size
  return (
    <Trace data-slot="citations" data-testid="sources">
      <TraceCollapsible>
        <TraceCollapsibleTrigger>
          <TraceLine>
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
        <TraceCollapsibleContent>
          <Trace>
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
                <TraceLine>
                  <span className={cn(NUMBER, 'inline-flex shrink-0 items-center justify-center bg-secondary text-secondary-foreground')} aria-hidden>
                    {c.n}
                  </span>
                  <TraceTitle>{c.citation}</TraceTitle>
                  <TraceDescription className="font-mono text-xs">
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
