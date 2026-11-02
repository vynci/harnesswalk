// Pure helpers for the dataset packs (P5): their order, the fixture menu of one pack, a pack's
// display name, which of a pack's files an inspector shows, and the file tree built from their paths.
import type { PackFile, PackSummary } from '@/api/types'

/**
 * The fixtures a session on `packId` can pick: the pack's own first, then the shared ones (every
 * name in `capabilities.fixtures` that no pack ships). Other packs' fixtures are left out.
 */
export function fixtureChoices(packs: PackSummary[] | null | undefined, all: string[], packId: string | null): { own: string[]; shared: string[]; all: string[] } {
  if (!packs || packs.length === 0) return { own: [], shared: [...all], all: [...all] }
  const owned = new Set(packs.flatMap((p) => p.fixtures ?? []))
  const mine = packs.find((p) => p.id === packId)?.fixtures ?? []
  const own = mine.filter((f) => all.includes(f))
  const shared = all.filter((f) => !owned.has(f))
  return { own, shared, all: [...own, ...shared] }
}

/**
 * The packs' order in the header picker and the empty chat's chooser (owner's choice): the
 * starter shop first, then the domain packs in a fixed walk; unknown packs follow by title.
 */
export const PACK_ORDER = ['_starter', 'healthcare', 'logistics', 'ecommerce', 'sports', 'finance']

export function orderPacks<T extends { id: string; title?: string }>(packs: T[]): T[] {
  const rank = (p: T) => {
    const i = PACK_ORDER.indexOf(p.id)
    return i === -1 ? PACK_ORDER.length : i
  }
  return [...packs].sort((a, b) => rank(a) - rank(b) || (a.title ?? a.id).localeCompare(b.title ?? b.id))
}

/** How a pack is named in the UI: its title, else its id. */
export function packTitle(packs: PackSummary[] | null | undefined, packId: string | null | undefined): string {
  if (!packId) return 'the configured pack'
  return packs?.find((p) => p.id === packId)?.title || packId
}

/**
 * The pack files a node's inspector shows: the files whose role is the node's id, a role of its
 * own kind (a role `<id>_source` belongs to the node `<id>`), or the last word of the id (a
 * role `x` belongs to a node `y_x`). Roles come from the API, so no node id is named
 * here.
 */
export function filesForNode(files: PackFile[], nodeId: string): PackFile[] {
  return files.filter((f) => f.role === nodeId || f.role.startsWith(`${nodeId}_`) || nodeId.endsWith(`_${f.role}`))
}

export interface TreeFolder {
  type: 'folder'
  name: string
  path: string
  children: TreeNode[]
}

export interface TreeFile {
  type: 'file'
  name: string
  path: string
  file: PackFile
}

export type TreeNode = TreeFolder | TreeFile

/** The files as a tree by their paths: folders first, each level sorted by name. */
export function buildTree(files: PackFile[]): TreeNode[] {
  const root: TreeFolder = { type: 'folder', name: '', path: '', children: [] }
  for (const file of files) {
    const parts = file.path.split('/').filter(Boolean)
    let at = root
    parts.forEach((name, i) => {
      const path = parts.slice(0, i + 1).join('/')
      if (i === parts.length - 1) {
        at.children.push({ type: 'file', name, path, file })
        return
      }
      let next = at.children.find((c): c is TreeFolder => c.type === 'folder' && c.name === name)
      if (!next) {
        next = { type: 'folder', name, path, children: [] }
        at.children.push(next)
      }
      at = next
    })
  }
  const sort = (nodes: TreeNode[]): TreeNode[] =>
    nodes
      .map((n) => (n.type === 'folder' ? { ...n, children: sort(n.children) } : n))
      .sort((a, b) => Number(b.type === 'folder') - Number(a.type === 'folder') || a.name.localeCompare(b.name))
  return sort(root.children)
}

/** `docs/x.pdf` was rendered from `sources/x.md` when the pack ships that file. */
export function pdfSource(files: PackFile[], path: string): string | null {
  if (!/\.pdf$/i.test(path)) return null
  const stem = path.split('/').pop()!.replace(/\.pdf$/i, '')
  return files.find((f) => f.path === `sources/${stem}.md`)?.path ?? null
}

/** The knowledge document id of a `docs/` file: its file name without the extension. */
export function docIdOf(path: string): string {
  return (path.split('/').pop() ?? path).replace(/\.[^.]+$/, '')
}

/**
 * The working-set artifact behind a procedural file: `system_prompt.md` is `prompt/system_prompt`,
 * `skills/x.md` is `skill/x`. Null for any other file.
 */
export function artifactOfFile(path: string): { kind: string; name: string } | null {
  if (path === 'system_prompt.md') return { kind: 'prompt', name: 'system_prompt' }
  const m = path.match(/^skills\/([^/]+)\.md$/)
  return m ? { kind: 'skill', name: m[1]! } : null
}
