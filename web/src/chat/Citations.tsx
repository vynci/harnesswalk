// Knowledge citations in the chat: the reply (markdown) has its `[n]` markers as chips, and a Sources list
// under the reply names every cited chunk. Both open the document reader at that chunk
// (GET /v1/knowledge/docs/{doc}). The data is `turn_end.summary.citations`, nothing else.
import { BookOpen } from 'lucide-react'
import { lazy, Suspense, useMemo } from 'react'
import type { Components } from 'react-markdown'
import type { Citation } from '@/api/types'
import { rehypeReplyMarks } from '@/lib/markdown'
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

/** The number of a citation as a small round chip, shared by the inline citation and Sources. */
const NUMBER = 'h-5 min-w-5 rounded-xs px-1.5 font-mono text-xs tabular-nums'

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

// The markdown renderer (react-markdown, remark-gfm) is its own chunk, fetched at start-up in
// parallel with the API boot; until it lands a reply shows as plain text.
const markdownChunk = import('@/components/markdown')
const Markdown = lazy(() => markdownChunk.then((m) => ({ default: m.Markdown })))

/**
 * The assistant's reply as GitHub-flavoured markdown, with the turn's citation chips and the
 * redaction placeholders inside it. While it streams there are no citations yet, so the markers
 * stay text until the turn ends.
 */
export function ReplyMarkdown({ text, citations, onOpen }: { text: string; citations: Citation[]; onOpen: (c: Citation) => void }) {
  const plugins = useMemo(() => [rehypeReplyMarks(new Set(citations.map((c) => c.n)))], [citations])
  const components = useMemo<Components>(() => {
    const byN = new Map(citations.map((c) => [c.n, c]))
    return {
      span: ({ node: _node, ...props }) => {
        const n = (props as Record<string, unknown>)['data-hw-cite']
        const c = n !== undefined ? byN.get(Number(n)) : undefined
        return c ? <CitationChip citation={c} onOpen={onOpen} /> : <span {...props} />
      },
    }
  }, [citations, onOpen])
  return (
    <Suspense fallback={<p className="whitespace-pre-wrap">{text}</p>}>
      <Markdown text={text} rehypePlugins={plugins} components={components} />
    </Suspense>
  )
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
