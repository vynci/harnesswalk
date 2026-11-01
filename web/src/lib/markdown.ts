// Markdown helpers shared by the renderer (components/markdown.tsx) and the chat's reply: the
// link filter and the rehype plugin that turns citation markers and placeholders into elements.
import { defaultUrlTransform } from 'react-markdown'

/** The few hast shapes the code renderer and the plugins read. */
export interface HastNode {
  type: string
  tagName?: string
  value?: string
  properties?: Record<string, unknown>
  children?: HastNode[]
}

export function textOf(node: HastNode | undefined): string {
  if (!node) return ''
  if (node.type === 'text') return node.value ?? ''
  return (node.children ?? []).map(textOf).join('')
}

/** Only http(s), mailto and in-page links survive; `javascript:` and friends become empty. */
export function safeUrl(url: string): string {
  const out = defaultUrlTransform(url)
  if (!out) return ''
  if (/^(https?:|mailto:|#)/i.test(out)) return out
  // A relative path has no scheme: keep it (the doc reader's own links).
  return /^[a-z][a-z0-9+.-]*:/i.test(out) ? '' : out
}

/** A citation marker or a redaction placeholder, in one pass over a text run. */
const MARKS = /\[(\d{1,3})\]|\[[A-Z][A-Z_]*_\d+\]/g

const PLACEHOLDER_CLASS = 'rounded-md bg-state-waiting/15 px-1 font-mono text-[0.92em] text-foreground'

/**
 * A rehype plugin for a reply: outside code, every `[n]` the turn cited becomes a span the chip
 * renderer picks up (`data-hw-cite`), and every redaction placeholder a highlighted mark.
 */
export function rehypeReplyMarks(cited: ReadonlySet<number>) {
  return () => (tree: HastNode) => {
    const walk = (node: HastNode) => {
      if (!node.children) return
      if (node.type === 'element' && (node.tagName === 'code' || node.tagName === 'pre')) return
      const next: HastNode[] = []
      for (const child of node.children) {
        if (child.type !== 'text' || !child.value) {
          walk(child)
          next.push(child)
          continue
        }
        const value = child.value
        let last = 0
        for (const m of value.matchAll(MARKS)) {
          const n = m[1] !== undefined ? Number(m[1]) : null
          if (n !== null && !cited.has(n)) continue
          if (m.index! > last) next.push({ type: 'text', value: value.slice(last, m.index) })
          next.push(
            n !== null
              ? { type: 'element', tagName: 'span', properties: { dataHwCite: String(n) }, children: [{ type: 'text', value: m[0] }] }
              : { type: 'element', tagName: 'mark', properties: { className: PLACEHOLDER_CLASS.split(' '), dataTestid: 'placeholder' }, children: [{ type: 'text', value: m[0] }] },
          )
          last = m.index! + m[0].length
        }
        if (last === 0) next.push(child)
        else if (last < value.length) next.push({ type: 'text', value: value.slice(last) })
      }
      node.children = next
    }
    walk(tree)
  }
}

