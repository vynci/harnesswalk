// What a link in a chapter points at. Chapters live in `lessons/`, so their relative links are
// read from there: `../harnesswalk/...#Symbol` is a code walk link (the Code tab), `NN-x.md` is
// another chapter (the Lessons tab), `#x` a section of this one, any other repo path opens on
// GitHub and an absolute URL opens as is, both in a new tab.

/** Where repo files are browsed. The API has no repository URL to offer, so it is named once here. */
export const REPO_BLOB_URL = 'https://github.com/vynci/harnesswalk/blob/main/'

export type LessonLink =
  | { kind: 'code'; path: string; symbol: string | null }
  | { kind: 'chapter'; slug: string; anchor: string | null }
  | { kind: 'anchor'; id: string }
  | { kind: 'repo'; href: string; path: string }
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

export function classifyLink(href: string): LessonLink {
  if (/^[a-z][a-z0-9+.-]*:/i.test(href) || href.startsWith('//')) return { kind: 'external', href }
  if (href.startsWith('#')) return { kind: 'anchor', id: decodeURIComponent(href.slice(1)) }
  const [rawPath, rawAnchor] = href.split('#', 2)
  const anchor = rawAnchor ? decodeURIComponent(rawAnchor) : null
  const path = resolve(rawPath ?? '')
  if (path === null) return { kind: 'external', href }
  if (path.startsWith('harnesswalk/') && path.length > 'harnesswalk/'.length) return { kind: 'code', path, symbol: anchor }
  const chapter = path.match(/^lessons\/(\d{2}-[A-Za-z0-9_-]+)\.md$/)
  if (chapter) return { kind: 'chapter', slug: chapter[1]!, anchor }
  return { kind: 'repo', path, href: `${REPO_BLOB_URL}${path}${anchor ? `#${anchor}` : ''}` }
}
