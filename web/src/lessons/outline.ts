// The chapters in reading order, from the diagram and whatever GET /v1/lessons lists: one per
// simple node in walk order (01 to 15), then the expert group chapters (16 on). A chapter the
// diagram names but the API does not serve yet stays in the outline, marked not written.
import type { Diagram, LessonListItem, View } from '@/api/types'
import { lessonRef } from './parse'

export interface OutlineEntry {
  slug: string
  n: number
  title: string
  hook: string
  /** An expert group chapter, about the nodes inside a parent. */
  group: boolean
  /** The API serves it. */
  written: boolean
  /** The node to select when the chapter opens: the node, or a group's first covered node. */
  nodeId: string | null
  /** The view that node is drawn in. */
  view: View
  /** 0.2.0: the nodes the chapter needs switched on (`requires`), empty when it names none. */
  requires: string[]
}

const numberOf = (slug: string) => Number(slug.slice(0, 2)) || 0

export function buildOutline(diagram: Diagram | null, list: LessonListItem[] | null | undefined): OutlineEntry[] {
  const nodes = diagram?.nodes ?? []
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const served = new Map((list ?? []).map((l) => [l.slug, l]))
  const entries = new Map<string, OutlineEntry>()
  const order = diagram ? [...diagram.walk_order, ...nodes.map((n) => n.id).filter((id) => !diagram.walk_order.includes(id))] : []
  for (const id of order) {
    const node = byId.get(id)
    const ref = lessonRef(node?.lesson_url)
    if (!node || !ref || entries.has(ref.slug)) continue
    const item = served.get(ref.slug)
    const group = item?.group ?? node.view === 'expert'
    const parent = node.parent ? byId.get(node.parent) : undefined
    const target = item ? (group ? item.covers.find((c) => byId.has(c)) : item.node_id) : node.id
    const targetNode = byId.get(target ?? '') ?? node
    entries.set(ref.slug, {
      slug: ref.slug,
      n: item?.n ?? numberOf(ref.slug),
      title: item?.title || (group && parent ? parent.label : node.label),
      hook: item?.hook || (group ? '' : (node.lesson_summary ?? '')),
      group,
      written: !!item,
      nodeId: targetNode.id,
      view: targetNode.view,
      requires: item?.requires ?? [],
    })
  }
  // A served chapter the diagram does not point at still gets a row.
  for (const item of list ?? []) {
    if (entries.has(item.slug)) continue
    const target = (item.group ? item.covers.find((c) => byId.has(c)) : item.node_id) ?? null
    const node = target ? byId.get(target) : undefined
    entries.set(item.slug, {
      slug: item.slug,
      n: item.n,
      title: item.title,
      hook: item.hook,
      group: item.group,
      written: true,
      nodeId: node?.id ?? null,
      view: node?.view ?? 'simple',
      requires: item.requires ?? [],
    })
  }
  return [...entries.values()].sort((a, b) => a.n - b.n || a.slug.localeCompare(b.slug))
}

/** The entry after (or before, with -1) the chapter `slug`. */
export function stepEntry(outline: OutlineEntry[], slug: string | null, delta: 1 | -1): OutlineEntry | null {
  const i = outline.findIndex((e) => e.slug === slug)
  if (i < 0) return null
  return outline[i + delta] ?? null
}
