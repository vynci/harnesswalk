// The knowledge document reader: GET /v1/knowledge/docs/{doc}, opened from a citation chip in the
// chat or from the document lists of the Knowledge inspector. A fixed-height panel: the header
// (title, file, chunk count, version, scope and pack, the view switch) stays put and the body
// scrolls inside it. Two views:
//   Document  the original file: a PDF in the browser's own viewer (`file.url`, the pack files
//             route with ?raw=1), markdown rendered with a rule at every chunk boundary, any
//             other text as code.
//   Chunks    every chunk with its heading, tokens and citation string, as the store cut them.
// A citation opens the reader at its chunk: Chunks marks it, Document scrolls to its section
// (markdown) or opens the PDF at its page.
import { type Ref, useEffect, useRef, useState } from 'react'
import { Download, ExternalLink, FileText } from 'lucide-react'
import type { KnowledgeDocDetail } from '@/api/types'
import { Code } from '@/components/code'
import { Markdown } from '@/components/markdown'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Switch } from '@/components/ui/switch'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { cn } from '@/lib/utils'
import { useApp } from '@/state/store'
import { defaultView, docSegments, hasDocumentView, pageOf, type ReaderView } from './docView'

type Loaded = { key: string; doc?: KnowledgeDocDetail; error?: string }

/** Scroll an element into view inside the reader body (jsdom has no scrollIntoView). */
function reveal(el: Element | null, block: ScrollLogicalPosition) {
  el?.scrollIntoView?.({ block })
}

export function DocReader() {
  const open = useApp((s) => s.doc)
  const closeDoc = useApp((s) => s.closeDoc)
  const api = useApp((s) => s.api)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const docId = open?.docId ?? null
  // Documents are per pack (P5): the client asks for the session pack's.
  const pack = useApp((s) => s.pack)
  const key = `${pack ?? ''}:${docId ?? ''}`

  useEffect(() => {
    if (!api || !docId) return
    let cancelled = false
    api.knowledgeDoc(docId).then(
      (doc) => !cancelled && setLoaded({ key, doc }),
      (err: unknown) => !cancelled && setLoaded({ key, error: err instanceof Error ? err.message : String(err) }),
    )
    return () => {
      cancelled = true
    }
  }, [api, docId, key])

  const current = loaded?.key === key ? loaded : null
  const doc = current?.doc
  return (
    <Dialog open={!!open} onOpenChange={(o) => !o && closeDoc()}>
      <DialogContent className="flex h-[85dvh] flex-col gap-0 overflow-hidden p-0 sm:max-w-[44rem]" data-testid="doc-reader">
        {doc ? (
          <DocPanel key={key} doc={doc} chunk={open?.chunk} resolveUrl={api?.resolveUrl ?? ((u) => u)} />
        ) : (
          <DialogHeader className="border-b px-5 pt-5 pb-3 pr-14">
            <DialogTitle className="flex items-center gap-2">
              <FileText className="size-4 text-kind-store" aria-hidden />
              {docId}
            </DialogTitle>
            <DialogDescription className="text-xs">
              {current?.error ? <span className="text-destructive">Could not load the document: {current.error}</span> : 'Loading…'}
            </DialogDescription>
          </DialogHeader>
        )}
      </DialogContent>
    </Dialog>
  )
}

/** One loaded document: the header and the view. Keyed by document, so the view resets per document. */
function DocPanel({ doc, chunk, resolveUrl }: { doc: KnowledgeDocDetail; chunk?: number; resolveUrl: (url: string) => string }) {
  const [view, setView] = useState<ReaderView>(() => defaultView(doc))
  const [rules, setRules] = useState(true)
  const shown: ReaderView = hasDocumentView(doc) ? view : 'chunks'
  const body = useRef<HTMLDivElement | null>(null)

  // Opened at a chunk: bring it (Chunks) or its section (Document, markdown) into view.
  useEffect(() => {
    if (chunk === undefined) return
    const el = body.current?.querySelector('[data-cited="true"]') ?? null
    reveal(el, shown === 'chunks' ? 'center' : 'start')
  }, [chunk, shown])

  return (
    <>
      <DialogHeader className="shrink-0 gap-2 border-b px-5 pt-5 pb-3 pr-14" data-testid="doc-reader-header">
        <DialogTitle className="flex min-w-0 items-center gap-2 leading-snug">
          <FileText className="size-4 shrink-0 text-kind-store" aria-hidden />
          <span className="min-w-0 truncate" title={doc.title}>
            {doc.title}
          </span>
        </DialogTitle>
        <DialogDescription className="flex flex-wrap items-center gap-1.5 text-xs">
          <code className="font-mono" data-testid="doc-path">
            {doc.path}
          </code>
          <span data-testid="doc-chunk-count">
            · {doc.chunks.length} chunk{doc.chunks.length === 1 ? '' : 's'}
          </span>
          <span>· version {doc.version}</span>
          <Badge variant="outline" className="font-normal" data-testid="doc-scope">
            {doc.scope}
          </Badge>
          {doc.file?.pack && (
            <Badge variant="secondary" className="font-mono font-normal" data-testid="doc-pack">
              {doc.file.pack}
            </Badge>
          )}
        </DialogDescription>
        <ViewSwitch doc={doc} view={shown} onView={setView} rules={rules} onRules={setRules} />
      </DialogHeader>
      <ReaderBody ref={body} doc={doc} view={shown} chunk={chunk} rules={rules} resolveUrl={resolveUrl} />
    </>
  )
}

/**
 * The segmented control (Document, Chunks), and for markdown in the Document view the switch for
 * the chunk boundary rules. Nothing when the document has no Document view.
 */
export function ViewSwitch({
  doc,
  view,
  onView,
  rules,
  onRules,
}: {
  doc: KnowledgeDocDetail
  view: ReaderView
  onView: (view: ReaderView) => void
  rules: boolean
  onRules: (on: boolean) => void
}) {
  if (!hasDocumentView(doc)) return null
  return (
    <div className="flex flex-wrap items-center gap-3 pt-1">
      <ToggleGroup
        type="single"
        variant="outline"
        size="sm"
        spacing={0}
        value={view}
        onValueChange={(v) => v && onView(v as ReaderView)}
        aria-label="Reader view"
        data-testid="doc-view"
        data-view={view}
      >
        <ToggleGroupItem value="document" className="px-3 text-xs">
          Document
        </ToggleGroupItem>
        <ToggleGroupItem value="chunks" className="px-3 text-xs">
          Chunks
        </ToggleGroupItem>
      </ToggleGroup>
      {view === 'document' && doc.kind === 'markdown' && (
        <label className="flex items-center gap-2 text-xs text-muted-foreground">
          <Switch size="sm" checked={rules} onCheckedChange={onRules} data-testid="doc-rules" />
          Chunk boundaries
        </label>
      )}
    </div>
  )
}

/** The reader's body in one view: it scrolls inside the dialog (a PDF fills it instead). */
export function ReaderBody({
  doc,
  view,
  chunk,
  rules,
  resolveUrl,
  ref,
}: {
  doc: KnowledgeDocDetail
  view: ReaderView
  chunk?: number
  rules: boolean
  resolveUrl: (url: string) => string
  ref?: Ref<HTMLDivElement>
}) {
  const pdf = view === 'document' && doc.kind === 'pdf' && doc.file
  return (
    <DialogBody ref={ref} className={cn(pdf ? 'flex flex-col overflow-hidden' : 'px-5 py-4')} data-testid="doc-reader-body" data-view={view}>
      {view === 'chunks' ? (
        <ChunkList doc={doc} chunk={chunk} />
      ) : pdf ? (
        <PdfView url={resolveUrl(pdf.url)} name={doc.path} page={chunk !== undefined ? pageOf(doc.chunks.find((c) => c.position === chunk)) : null} />
      ) : doc.kind === 'markdown' ? (
        <MarkdownDocument doc={doc} chunk={chunk} rules={rules} />
      ) : (
        <Code code={doc.text} language="text" wrap size="sm" label={doc.path} data-testid="doc-document" />
      )}
    </DialogBody>
  )
}

/** Today's view: every chunk as the store cut it, the cited one marked. */
function ChunkList({ doc, chunk }: { doc: KnowledgeDocDetail; chunk?: number }) {
  return (
    <ol className="flex flex-col gap-2.5">
      {doc.chunks.map((c) => {
        const cited = chunk === c.position
        return (
          <li
            key={c.position}
            className={cn('rounded-lg border p-3 text-sm', cited && 'border-transparent bg-kind-store/10')}
            data-testid="doc-chunk"
            data-position={c.position}
            data-cited={cited ? 'true' : 'false'}
            aria-current={cited ? 'true' : undefined}
          >
            <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <span className="font-mono">#{c.position}</span>
              <span className="font-medium text-foreground">{c.heading || 'Introduction'}</span>
              <span>· {c.tokens} tok</span>
              {cited && (
                <Badge variant="secondary" className="font-normal">
                  cited
                </Badge>
              )}
              <span className="ml-auto italic">{c.citation}</span>
            </div>
            <Markdown text={c.text} />
          </li>
        )
      })}
    </ol>
  )
}

/**
 * The markdown as written, with a thin rule naming the chunks at each boundary (when the
 * sections line up with the chunks; otherwise the text alone). The cited chunk's section is marked.
 */
function MarkdownDocument({ doc, chunk, rules }: { doc: KnowledgeDocDetail; chunk?: number; rules: boolean }) {
  const segments = docSegments(doc.text, doc.chunks)
  if (!segments) {
    return (
      <div data-testid="doc-document" data-kind="markdown">
        <Markdown text={doc.text} />
      </div>
    )
  }
  const tokens = new Map(doc.chunks.map((c) => [c.position, c.tokens]))
  return (
    <div className="flex flex-col gap-3" data-testid="doc-document" data-kind="markdown">
      {segments.map((s) => {
        const cited = chunk !== undefined && s.positions.includes(chunk)
        const label = s.positions.length === 1 ? `chunk #${s.positions[0]}` : `chunks ${s.positions.map((p) => `#${p}`).join(', ')}`
        const tok = s.positions.reduce((n, p) => n + (tokens.get(p) ?? 0), 0)
        return (
          <section
            key={s.positions[0]}
            className={cn('scroll-mt-2 rounded-md', cited && 'bg-kind-store/10 px-3 pb-2', cited && !rules && 'pt-2')}
            data-testid="doc-segment"
            data-chunks={s.positions.join(' ')}
            data-cited={cited ? 'true' : 'false'}
            aria-current={cited ? 'true' : undefined}
          >
            {rules && (
              <div className="flex items-center gap-2 py-1 text-[0.7rem] text-muted-foreground" data-testid="doc-boundary" aria-label={label}>
                <span className="font-mono whitespace-nowrap">{label}</span>
                <span className="whitespace-nowrap">· {tok} tok</span>
                {cited && (
                  <Badge variant="secondary" className="h-4 px-1.5 text-[0.7rem] font-normal">
                    cited
                  </Badge>
                )}
                <span className="h-px flex-1 bg-border" aria-hidden />
              </div>
            )}
            <Markdown text={s.text} />
          </section>
        )
      })}
    </div>
  )
}

/**
 * A PDF in the browser's own viewer. An <object> rather than an <iframe>: a browser without an
 * inline PDF viewer (headless Chromium among them) shows the fallback inside it instead of
 * downloading the file.
 */
export function PdfView({ url, name, page }: { url: string; name: string; page?: number | null }) {
  const src = page ? `${url}#page=${page}` : url
  return (
    <object key={src} data={src} type="application/pdf" className="min-h-0 w-full flex-1" aria-label={`${name}, the original PDF`} data-testid="doc-pdf">
      <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-center" data-testid="doc-pdf-fallback">
        <p className="text-sm text-muted-foreground">Your browser cannot display PDFs inline.</p>
        <div className="flex flex-wrap justify-center gap-2">
          <Button asChild size="sm" variant="outline">
            <a href={url} target="_blank" rel="noopener noreferrer" data-testid="doc-pdf-open">
              <ExternalLink aria-hidden /> Open in new tab
            </a>
          </Button>
          <Button asChild size="sm" variant="outline">
            <a href={url} download={name} data-testid="doc-pdf-download">
              <Download aria-hidden /> Download
            </a>
          </Button>
        </div>
      </div>
    </object>
  )
}
