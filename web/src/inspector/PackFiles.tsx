// The session pack's files a node reads (P5), as a File Tree, and the file that is open: a prompt
// or skill opens in the artifact editor (the working set, shipped by a release), a document in the
// document reader or as its text, a PDF as the markdown it was rendered from.
import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { BookOpen } from 'lucide-react'
import type { PackFile, PackFileText } from '@/api/types'
import { Code } from '@/components/code'
import { FileTree } from '@/components/file-tree'
import { Prose } from '@/components/prose'
import { Button } from '@/components/ui/button'
import { inspectorArtifact } from '@/state/ops'
import { artifactOfFile, buildTree, docIdOf, packTitle, pdfSource } from '@/state/packs'
import { useApp } from '@/state/store'
import { ArtifactEditor } from './ArtifactEditor'
import { useArtifacts, useLoad } from './useOps'

function size(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : `${(bytes / 1024).toFixed(1)} KiB`
}

function languageOf(path: string): string {
  if (/\.ya?ml$/i.test(path)) return 'yaml'
  if (/\.jsonl?$/i.test(path)) return 'json'
  if (/\.md$/i.test(path)) return 'markdown'
  return 'text'
}

function Markdown({ text }: { text: string }) {
  return (
    <Prose>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </Prose>
  )
}

/** One file of the pack as text: rendered markdown, or a read-only code block. */
function FileText({ pack, path, label }: { pack: string; path: string; label?: string }) {
  const api = useApp((s) => s.api)
  const file = useLoad<PackFileText>(api ? `file:${pack}:${path}` : null, () => api!.packFile(pack, path))
  if (file.status === 'error') return <p className="text-xs text-destructive">Could not load {path}: {file.error}</p>
  if (!file.data || file.data.path !== path) return <p className="text-xs text-muted-foreground">Loading {path}…</p>
  const text = file.data.text
  return (
    <div className="flex flex-col gap-2" data-testid="pack-file-text" data-path={path} data-kind={file.data.kind}>
      {/\.md$/i.test(path) ? (
        <Markdown text={text} />
      ) : (
        <Code code={text} language={languageOf(path)} lineNumbers size="sm" label={label ?? path} contentClassName="max-h-96 text-xs" />
      )}
    </div>
  )
}

function FileView({ pack, file, files }: { pack: string; file: PackFile; files: PackFile[] }) {
  const { artifacts } = useArtifacts()
  const openDoc = useApp((s) => s.openDoc)
  const target = artifactOfFile(file.path)
  const listed = target ? artifacts.find((a) => a.kind === target.kind && a.name === target.name) : undefined
  const artifact = target && listed ? inspectorArtifact({ kind: target.kind, name: target.name, version: listed.version, url: '' }, artifacts) : undefined
  // A document the knowledge index serves (its doc id is the file name).
  const isDoc = file.path.startsWith('docs/')
  const source = pdfSource(files, file.path)
  return (
    <section className="flex flex-col gap-2 border-t pt-3" aria-label={`File ${file.path}`} data-testid="pack-file" data-path={file.path} data-role={file.role}>
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 truncate font-mono text-xs font-medium">{file.path}</code>
        <span className="text-xs text-muted-foreground tabular-nums">{size(file.bytes)}</span>
        {isDoc && (
          <Button size="xs" variant="outline" className="ml-auto" onClick={() => openDoc(docIdOf(file.path))} data-testid="pack-file-reader">
            <BookOpen aria-hidden /> Open in the reader
          </Button>
        )}
      </div>
      {artifact ? (
        <ArtifactEditor artifact={artifact} language="markdown" renderBody={(text) => <Markdown text={text} />} />
      ) : source ? (
        <>
          <p className="text-xs text-muted-foreground" data-testid="pack-file-pdf-note">
            A PDF, shown as the markdown it was rendered from: <code className="font-mono">{source}</code>. The harness indexes the text it reads from the PDF.
          </p>
          <FileText pack={pack} path={source} />
        </>
      ) : (
        <FileText pack={pack} path={file.path} />
      )}
    </section>
  )
}

/** The Files tab: the files of the session's pack this node reads, as a File Tree. */
export function PackFiles({ files, nodeLabel }: { files: PackFile[]; nodeLabel: string }) {
  const pack = useApp((s) => s.pack)
  const packs = useApp((s) => s.packs)
  const tree = useMemo(() => buildTree(files), [files])
  const [picked, setPicked] = useState<{ pack: string | null; path: string } | null>(null)
  // A file picked on another pack is not open on this one.
  const selected = picked && picked.pack === pack ? files.find((f) => f.path === picked.path) : undefined
  if (!pack) return null
  return (
    <div className="flex flex-col gap-3" data-testid="pack-files" data-pack={pack}>
      <p className="text-xs text-muted-foreground">
        The files of <span className="font-medium text-foreground">{packTitle(packs, pack)}</span> that {nodeLabel} reads, from <code className="font-mono">datasets/{pack}/</code>. Open one to read it
        {files.some((f) => artifactOfFile(f.path)) ? '; prompts and skills open in the editor, as working-set artifacts' : ''}.
      </p>
      <FileTree nodes={tree} selected={selected?.path ?? null} onSelect={(path) => setPicked({ pack, path })} label={`Files of ${packTitle(packs, pack)} for ${nodeLabel}`} describe={(path) => size(files.find((f) => f.path === path)?.bytes ?? 0)} />
      {selected && <FileView key={selected.path} pack={pack} file={selected} files={files} />}
    </div>
  )
}
