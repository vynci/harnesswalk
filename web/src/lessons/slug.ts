// Heading ids the way GitHub makes them, so a node's `lesson_url` anchor (`#online-eval`) finds
// its `### Online eval` section: lowercase, punctuation dropped, spaces to hyphens, and a
// repeated heading gets `-1`, `-2`.

/** The GitHub slug of one heading's text. */
export function headingSlug(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{M}\p{N}\p{Pc} -]/gu, '')
    .replace(/ /g, '-')
}

/** A slugger for one document: the same text twice gets `-1` the second time. */
export function createSlugger(): (text: string) => string {
  // github-slugger's rule: count by the original slug, skip any slug already taken.
  const seen = new Map<string, number>()
  return (text) => {
    const original = headingSlug(text)
    let slug = original
    while (seen.has(slug)) {
      const n = (seen.get(original) ?? 0) + 1
      seen.set(original, n)
      slug = `${original}-${n}`
    }
    seen.set(slug, 0)
    return slug
  }
}

// The few hast shapes the plugin reads; react-markdown hands it a full hast root.
interface HastNode {
  type: string
  tagName?: string
  value?: string
  properties?: Record<string, unknown>
  children?: HastNode[]
}

function textOf(node: HastNode): string {
  if (node.type === 'text') return node.value ?? ''
  return (node.children ?? []).map(textOf).join('')
}

/** A rehype plugin: every h1 to h6 gets its GitHub slug as `id`. */
export function rehypeHeadingIds() {
  return (tree: HastNode) => {
    const slug = createSlugger()
    const walk = (node: HastNode) => {
      if (node.type === 'element' && node.tagName && /^h[1-6]$/.test(node.tagName)) {
        node.properties = { ...node.properties, id: slug(textOf(node)) }
      }
      for (const child of node.children ?? []) walk(child)
    }
    walk(tree)
  }
}
