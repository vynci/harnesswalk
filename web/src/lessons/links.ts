// What a link in a chapter points at. Chapters live in `lessons/`, so their relative links are
// read from there: `../harnesswalk/...#Symbol` is a code walk link (the Code tab), `NN-x.md` is
// another chapter (in the Guide), `#x` a section of this one, any other repo path opens in the
// repository the API names (`capabilities.repo_url`) and an absolute URL opens as is, both in a
// new tab.

/** Where repo files are browsed: `${repo_url}/blob/main/`, or null when the API names no repository. */
export function repoBlobUrl(repoUrl: string | null | undefined): string | null {
  const base = (repoUrl ?? '').trim().replace(/\/+$/, '')
  return base ? `${base}/blob/main/` : null
}

export type LessonLink =
  | { kind: 'code'; path: string; symbol: string | null }
  | { kind: 'chapter'; slug: string; anchor: string | null }
  | { kind: 'anchor'; id: string }
  /** `href` is null when the API names no repository: the path is shown, not linked. */
  | { kind: 'repo'; href: string | null; path: string }
  | { kind: 'external'; href: string }

/** `href` resolved against `lessons/`, or null when it climbs out of the repo. */
function resolve(href: string): string | null {
  const parts = ['lessons']
  for (const seg of href.split('/')) {
    if (seg === '' || seg === '.') continue
    if (seg === '..') {
      if (!parts.length) return null
      parts.pop()
    } else parts.push(seg)
  }
  return parts.join('/')
}

export function classifyLink(href: string, repoUrl?: string | null): LessonLink {
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('//')) return { kind: 'external', href }
  if (href.startsWith('#')) return { kind: 'anchor', id: decodeURIComponent(href.slice(1)) }
  const [rawPath, rawAnchor] = href.split('#', 2)
  const anchor = rawAnchor ? decodeURIComponent(rawAnchor) : null
  const path = resolve(rawPath ?? '')
  if (path === null) return { kind: 'external', href }
  if (path.startsWith('harnesswalk/') && path.length > 'harnesswalk/'.length) return { kind: 'code', path, symbol: anchor }
  const chapter = path.match(/^lessons\/(\d{2}-[A-Za-z0-9_-]+)\.md$/)
  if (chapter) return { kind: 'chapter', slug: chapter[1]!, anchor }
  const blob = repoBlobUrl(repoUrl)
  return { kind: 'repo', path, href: blob ? `${blob}${path}${anchor ? `#${anchor}` : ''}` : null }
}
