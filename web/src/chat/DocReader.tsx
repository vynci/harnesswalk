// The knowledge document reader: GET /v1/knowledge/docs/{doc}, every chunk with its heading and
// citation string, scrolled to (and marking) the chunk a citation named. Opened from a citation
// chip in the chat or from the document list of the Knowledge inspector.
import { useEffect, useRef, useState } from 'react'
import { FileText } from 'lucide-react'
import type { KnowledgeDocDetail } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { useApp } from '@/state/store'

type Loaded = { key: string; doc?: KnowledgeDocDetail; error?: string }

export function DocReader() {
  const open = useApp((s) => s.doc)
  const closeDoc = useApp((s) => s.closeDoc)
  const api = useApp((s) => s.api)
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const target = useRef<HTMLElement | null>(null)
  const docId = open?.docId ?? null

  useEffect(() => {
    if (!api || !docId) return
    let cancelled = false
    api.knowledgeDoc(docId).then(
      (doc) => !cancelled && setLoaded({ key: docId, doc }),
      (err: unknown) => !cancelled && setLoaded({ key: docId, error: err instanceof Error ? err.message : String(err) }),
    )
    return () => {
      cancelled = true
    }
  }, [api, docId])

  const current = loaded?.key === docId ? loaded : null
  const doc = current?.doc
  useEffect(() => {
    if (doc && open?.chunk !== undefined) target.current?.scrollIntoView({ block: 'center' })
  }, [doc, open?.chunk])

  return (
    <Dialog open={!!open} onOpenChange={(o) => !o && closeDoc()}>
      <DialogContent className="flex max-h-[85vh] flex-col gap-3 sm:max-w-2xl" data-testid="doc-reader">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <FileText className="size-4 text-kind-store" aria-hidden />
            {doc?.title ?? docId}
          </DialogTitle>
          <DialogDescription className="flex flex-wrap items-center gap-1.5 text-xs">
            {doc ? (
              <>
                <code className="font-mono">{doc.path}</code>
                <span>· {doc.chunks.length} chunks · version {doc.version}</span>
                <Badge variant="outline" className="rounded-sm px-1 py-0 text-[10px] font-normal" data-testid="doc-scope">
                  {doc.scope}
                </Badge>
              </>
            ) : current?.error ? (
              <span className="text-destructive">Could not load the document: {current.error}</span>
            ) : (
              'Loading…'
            )}
          </DialogDescription>
        </DialogHeader>
        {doc && (
          <ScrollArea className="min-h-0 flex-1 pr-3">
            <ol className="flex flex-col gap-2.5">
              {doc.chunks.map((c) => {
                const cited = open?.chunk === c.position
                return (
                  <li
                    key={c.position}
                    ref={cited ? (el) => void (target.current = el) : undefined}
                    className={cn('rounded-md border p-3 text-sm', cited && 'border-kind-store bg-kind-store/10 ring-1 ring-kind-store/40')}
                    data-testid="doc-chunk"
                    data-position={c.position}
                    data-cited={cited ? 'true' : 'false'}
                    aria-current={cited ? 'true' : undefined}
                  >
                    <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                      <span className="font-mono">#{c.position}</span>
                      <span className="font-medium text-foreground">{c.heading || 'Introduction'}</span>
                      <span>· {c.tokens} tok</span>
                      {cited && (
                        <Badge variant="outline" className="rounded-sm border-kind-store px-1 py-0 text-[10px] font-normal">
                          cited
                        </Badge>
                      )}
                      <span className="ml-auto italic">{c.citation}</span>
                    </div>
                    <p className="leading-relaxed whitespace-pre-wrap">{c.text}</p>
                  </li>
                )
              })}
            </ol>
          </ScrollArea>
        )}
      </DialogContent>
    </Dialog>
  )
}
