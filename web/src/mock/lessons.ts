// The lesson routes of harnesswalk/control/http_lessons.py, served from the real chapter files
// in lessons/ (bundled into mock builds only, like repo.ts). Same shapes: the list, one chapter
// with its markdown, try and code links (lines found by a regex over the real source), and the
// source of a file the chapter links to.
import { parse } from 'yaml'
import type { Diagram, Lesson, LessonAlso, LessonListItem, LessonSource, LessonTry } from '@/api/types'
import { sourceFor } from './repo'

const files = import.meta.glob('../../../lessons/*.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>

const CHAPTER = /(\d{2})-([a-z0-9_-]+)\.md$/
const FRONT_MATTER = /^---\n([\s\S]*?)\n---\n/
const CODE_LINK = /\]\(\.\.\/(harnesswalk\/[A-Za-z0-9_/.-]+)(?:#([A-Za-z_][A-Za-z0-9_.]*))?\)/g
const LINK_TARGET = /\]\([^)]*\)/g

/** YAML front matter and the markdown after it. */
export function splitFrontMatter(text: string): { meta: Record<string, unknown>; body: string } {
  const m = text.match(FRONT_MATTER)
  if (!m) return { meta: {}, body: text }
  const meta: unknown = parse(m[1]!)
  return { meta: meta && typeof meta === 'object' && !Array.isArray(meta) ? (meta as Record<string, unknown>) : {}, body: text.slice(m[0].length) }
}

function titleAndHook(body: string): { title: string; hook: string } {
  let title = ''
  let hook = ''
  for (const line of body.split('\n')) {
    if (!title && line.startsWith('# ')) title = line.slice(2).trim()
    else if (title && line.startsWith('> ')) {
      hook = line.slice(2).trim()
      break
    } else if (title && line.trim() && !line.startsWith('>')) break
  }
  return { title, hook }
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** The 1-based line of `Name` or `Class.method` in Python source, by indentation. */
export function symbolLine(source: string | undefined, symbol: string | null): number | null {
  if (!source || !symbol) return null
  const lines = source.split('\n')
  let from = 0
  let indent = 0
  let line: number | null = null
  for (const part of symbol.split('.')) {
    const def = new RegExp(`^(\\s*)(?:async\\s+def|def|class)\\s+${escape(part)}\\b|^(\\s*)${escape(part)}\\s*(?::[^=]*)?=`)
    let found = -1
    for (let i = from; i < lines.length; i++) {
      const text = lines[i]!
      // A member is searched for inside its parent's block only.
      if (line !== null && text.trim() && !text.trim().startsWith('#') && text.length - text.trimStart().length <= indent) break
      const m = text.match(def)
      if (!m) continue
      const pad = (m[1] ?? m[3] ?? '').length
      if (line === null ? pad === 0 : pad > indent) {
        found = i
        indent = pad
        break
      }
    }
    if (found < 0) return null
    line = found + 1
    from = found + 1
  }
  return line
}

interface Chapter extends Omit<Lesson, 'prev' | 'next'> {
  file: string
}

function words(body: string): number {
  return body.replace(LINK_TARGET, ']').split(/\s+/).filter(Boolean).length
}

export class MockLessons {
  private readonly chapters: Chapter[]

  constructor(private readonly diagram: Diagram) {
    const byPath = new Map(diagram.nodes.filter((n) => n.source_hint).map((n) => [n.source_hint, n.id]))
    this.chapters = Object.entries(files)
      .filter(([path]) => CHAPTER.test(path))
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([path, text]) => {
        const m = path.match(CHAPTER)!
        const slug = `${m[1]}-${m[2]}`
        const { meta, body } = splitFrontMatter(text)
        const nodeId = typeof meta.node === 'string' ? meta.node : null
        const covers = Array.isArray(meta.covers) && meta.covers.length ? meta.covers.map(String) : nodeId ? [nodeId] : []
        const seen = new Set<string>()
        const code_links: Lesson['code_links'] = []
        for (const [, rel, symbol] of body.matchAll(CODE_LINK)) {
          const key = `${rel}#${symbol ?? ''}`
          if (seen.has(key)) continue
          seen.add(key)
          code_links.push({ path: rel!, symbol: symbol ?? null, line: symbolLine(sourceFor(rel!), symbol ?? null), node_id: byPath.get(rel!) ?? null })
        }
        return {
          file: path,
          slug,
          n: Number(m[1]),
          ...titleAndHook(body),
          node_id: nodeId,
          covers,
          group: nodeId ? !covers.includes(nodeId) : false,
          words: words(body),
          url: `/v1/lessons/${slug}`,
          markdown: body.replace(/^\n+/, ''),
          // P5 integration: the pack is spelled out (`_starter` when the chapter names none).
          try: meta.try && typeof meta.try === 'object' ? ({ pack: '_starter', ...(meta.try as LessonTry) } as LessonTry) : null,
          also: Array.isArray(meta.also) ? (meta.also.filter((a) => a && typeof a === 'object') as LessonAlso[]) : [],
          code_links,
        }
      })
  }

  list(): { lessons: LessonListItem[]; walk_order: string[] } {
    return {
      lessons: this.chapters.map(({ slug, n, title, hook, node_id, covers, group, words: w, url }) => ({ slug, n, title, hook, node_id, covers, group, words: w, url })),
      walk_order: this.diagram.walk_order,
    }
  }

  get(slug: string): Lesson | null {
    const i = this.chapters.findIndex((c) => c.slug === slug)
    if (i < 0) return null
    const { file: _f, ...chapter } = this.chapters[i]!
    return { ...chapter, prev: this.chapters[i - 1]?.slug ?? null, next: this.chapters[i + 1]?.slug ?? null }
  }

  /** A linked file, or a reason it is not served: 'lesson' (no such chapter) or 'source'. */
  source(slug: string, path: string): LessonSource | 'lesson' | 'source' {
    const chapter = this.chapters.find((c) => c.slug === slug)
    if (!chapter) return 'lesson'
    if (!chapter.code_links.some((l) => l.path === path)) return 'source'
    const source = sourceFor(path)
    if (source === undefined) return 'source'
    const lines = source.split('\n')
    const symbols: { name: string; line: number }[] = []
    lines.forEach((line, i) => {
      const m = line.match(/^(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)/)
      if (m) symbols.push({ name: m[1]!, line: i + 1 })
    })
    return { path, language: path.endsWith('.py') ? 'python' : 'text', line_count: lines.length, source, truncated: false, symbols }
  }
}
