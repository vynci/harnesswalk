// The document reader's logic (DocReader.tsx): which view a document opens in, the page a PDF
// chunk came from, and a markdown document cut where the knowledge store cut it, so the
// Document view can draw a rule with the chunk numbers at each boundary. The split mirrors
// harnesswalk/context/chunking.py (split_markdown): front matter dropped, a section at every
// `#`, `##` and `###` heading, a heading with no text under it joins the next section, and the
// first `#` heading is the title (its section's chunks have the heading ""). A section longer
// than the chunk size is several chunks with its heading; the rule names them all.
import type { KnowledgeChunk, KnowledgeDocDetail } from '@/api/types'

export type ReaderView = 'document' | 'chunks'

/** Whether the Document view has something to show: a PDF needs its file, text needs text. */
export function hasDocumentView(doc: KnowledgeDocDetail): boolean {
  if (doc.kind === 'pdf') return !!doc.file?.url
  return doc.text.trim() !== ''
}

/** The view a document opens in: Document for a PDF or markdown whose file is there, else Chunks. */
export function defaultView(doc: KnowledgeDocDetail): ReaderView {
  return (doc.kind === 'pdf' || doc.kind === 'markdown') && hasDocumentView(doc) ? 'document' : 'chunks'
}

/** The page a PDF chunk came from (chunking.py names each page's section `page N`). */
export function pageOf(chunk: Pick<KnowledgeChunk, 'heading'> | undefined): number | null {
  const m = chunk ? /^page (\d+)$/.exec(chunk.heading) : null
  return m ? Number(m[1]) : null
}

const HEADING = /^(#{1,3})\s+(.+?)\s*#*\s*$/

export interface DocSegment {
  /** The markdown of the section, its heading line included. */
  text: string
  /** The section's heading as the chunks carry it ("" before the first heading and under the title). */
  heading: string
  /** The chunk positions the store cut from this section, in order. */
  positions: number[]
}

interface Section {
  heading: string
  lines: string[]
}

/** The sections of a markdown text as chunking.py finds them, each with its own raw lines. */
function sectionsOf(text: string): Section[] {
  let body = text
  if (body.startsWith('---\n')) {
    const end = body.indexOf('\n---\n', 4)
    body = end >= 0 ? body.slice(end + 5) : ''
  }
  const out: Section[] = []
  let title = ''
  let heading = ''
  let lines: string[] = []
  let hasText = false
  let pending: string[] = [] // heading lines with no text of their own, shown with the next section
  const flush = () => {
    if (hasText) {
      out.push({ heading, lines: [...pending, ...lines] })
      pending = []
    } else pending.push(...lines)
  }
  for (const line of body.split(/\r?\n/)) {
    const m = HEADING.exec(line)
    if (m) {
      flush()
      const level = m[1]!.length
      const name = m[2]!.trim()
      if (level === 1 && !title) {
        title = name
        heading = ''
      } else heading = name
      lines = [line]
      hasText = false
    } else {
      lines.push(line)
      if (line.trim()) hasText = true
    }
  }
  flush()
  const last = out.at(-1)
  if (last && pending.length) last.lines.push(...pending)
  return out
}

/**
 * The document as sections, each with the chunk positions cut from it, or null when the
 * sections and the chunks do not line up (another chunker, a document changed since it was
 * indexed): the reader then shows the document without rules.
 */
export function docSegments(text: string, chunks: Pick<KnowledgeChunk, 'position' | 'heading'>[]): DocSegment[] | null {
  const sections = sectionsOf(text)
  // Consecutive chunks with one heading came from one section (a long section is cut in windows).
  const runs: { heading: string; positions: number[] }[] = []
  for (const c of [...chunks].sort((a, b) => a.position - b.position)) {
    const run = runs.at(-1)
    if (run && run.heading === c.heading) run.positions.push(c.position)
    else runs.push({ heading: c.heading, positions: [c.position] })
  }
  if (sections.length === 0 || runs.length !== sections.length) return null
  if (sections.some((s, i) => s.heading !== runs[i]!.heading)) return null
  return sections.map((s, i) => ({ text: s.lines.join('\n').trim(), heading: s.heading, positions: runs[i]!.positions }))
}
