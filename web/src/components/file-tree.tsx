// The AI Elements File Tree pattern: folders are Collapsibles with a ghost button trigger whose
// ChevronRight turns 90 degrees when open, files are ghost buttons. It is an ARIA tree: one tab
// stop (roving tabindex), arrow keys move and open or close folders, Home and End jump, and
// Enter or Space opens the file.
import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { ChevronRight, FileText, Folder, FolderOpen } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { cn } from '@/lib/utils'
import type { TreeNode } from '@/state/packs'

export interface FileTreeProps {
  nodes: TreeNode[]
  /** The open file's path, marked with aria-selected. */
  selected?: string | null
  onSelect: (path: string) => void
  /** Folders open at first (default: every folder). */
  defaultOpen?: string[]
  label: string
  /** A short muted note after a file's name (its size, say). */
  describe?: (path: string) => ReactNode
  className?: string
}

function folders(nodes: TreeNode[]): string[] {
  return nodes.flatMap((n) => (n.type === 'folder' ? [n.path, ...folders(n.children)] : []))
}

export function FileTree({ nodes, selected = null, onSelect, defaultOpen, label, describe, className }: FileTreeProps) {
  const all = useMemo(() => folders(nodes), [nodes])
  const [open, setOpen] = useState<Set<string>>(() => new Set(defaultOpen ?? all))
  const [active, setActive] = useState<string | null>(null)
  const tree = useRef<HTMLUListElement>(null)
  const first = nodes[0]?.path ?? null
  const focusable = active ?? selected ?? first

  const items = () => [...(tree.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? [])].filter((el) => el.offsetParent !== null || el === document.activeElement)
  const focus = (el: HTMLElement | undefined) => {
    if (!el) return
    setActive(el.dataset.path ?? null)
    el.focus()
  }
  const toggle = (path: string, to?: boolean) =>
    setOpen((prev) => {
      const next = new Set(prev)
      const want = to ?? !next.has(path)
      if (want) next.add(path)
      else next.delete(path)
      return next
    })

  const onKeyDown = (e: KeyboardEvent<HTMLUListElement>) => {
    const list = items()
    const current = e.target as HTMLElement
    const i = list.indexOf(current)
    if (i < 0) return
    const path = current.dataset.path ?? ''
    const isFolder = current.dataset.folder === 'true'
    switch (e.key) {
      case 'ArrowDown':
        focus(list[i + 1])
        break
      case 'ArrowUp':
        focus(list[i - 1])
        break
      case 'Home':
        focus(list[0])
        break
      case 'End':
        focus(list[list.length - 1])
        break
      case 'ArrowRight':
        if (isFolder && !open.has(path)) toggle(path, true)
        else if (isFolder) focus(list[i + 1])
        break
      case 'ArrowLeft': {
        if (isFolder && open.has(path)) toggle(path, false)
        else {
          const parent = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : null
          if (parent) focus(list.find((el) => el.dataset.path === parent))
        }
        break
      }
      default:
        return
    }
    e.preventDefault()
  }

  const render = (list: TreeNode[], depth: number): ReactNode =>
    list.map((n) => {
      const pad = { paddingLeft: `${0.5 + depth * 1}rem` }
      if (n.type === 'folder') {
        const isOpen = open.has(n.path)
        const Icon = isOpen ? FolderOpen : Folder
        return (
          <li key={n.path} role="none">
            <Collapsible open={isOpen} onOpenChange={(o) => toggle(n.path, o)}>
              <CollapsibleTrigger asChild>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  role="treeitem"
                  aria-level={depth + 1}
                  tabIndex={focusable === n.path ? 0 : -1}
                  onFocus={() => setActive(n.path)}
                  className="h-7 w-full justify-start gap-1.5 font-normal text-muted-foreground hover:text-foreground"
                  style={pad}
                  data-path={n.path}
                  data-folder="true"
                  data-testid="file-tree-folder"
                >
                  <ChevronRight className={cn('size-4 transition-transform duration-200 motion-reduce:transition-none', isOpen && 'rotate-90')} aria-hidden />
                  <Icon className="size-4" aria-hidden />
                  <span className="truncate font-mono text-xs">{n.name}</span>
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent asChild>
                <ul role="group">{render(n.children, depth + 1)}</ul>
              </CollapsibleContent>
            </Collapsible>
          </li>
        )
      }
      const isSelected = selected === n.path
      return (
        <li key={n.path} role="none">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            role="treeitem"
            aria-level={depth + 1}
            aria-selected={isSelected}
            tabIndex={focusable === n.path ? 0 : -1}
            onFocus={() => setActive(n.path)}
            onClick={() => onSelect(n.path)}
            className={cn('h-7 w-full justify-start gap-1.5 font-normal text-muted-foreground hover:text-foreground', isSelected && 'bg-muted text-foreground')}
            style={{ paddingLeft: `${0.5 + depth * 1 + 1.25}rem` }}
            data-path={n.path}
            data-testid="file-tree-file"
          >
            <FileText className="size-4" aria-hidden />
            <span className="min-w-0 truncate font-mono text-xs">{n.name}</span>
            {describe && <span className="ml-auto shrink-0 text-xs text-muted-foreground/70 tabular-nums">{describe(n.path)}</span>}
          </Button>
        </li>
      )
    })

  return (
    <ul ref={tree} role="tree" aria-label={label} className={cn('flex flex-col', className)} onKeyDown={onKeyDown} data-testid="file-tree">
      {render(nodes, 0)}
    </ul>
  )
}
