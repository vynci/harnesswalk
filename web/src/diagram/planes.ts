import type { CSSProperties } from 'react'
import { Code, Cpu, Database, ListOrdered, MessageSquareText, SlidersHorizontal, type LucideIcon } from 'lucide-react'
import type { EdgeKind, NodeKind, PlaneId } from '@/api/types'

/** CSS variables that colour a node or group by its plane (see index.css). */
export function planeStyle(plane: PlaneId): CSSProperties {
  return {
    ['--plane-stroke' as string]: `var(--plane-${plane})`,
    ['--plane-fill' as string]: `var(--plane-${plane}-fill)`,
    ['--plane-group' as string]: `var(--plane-${plane}-group)`,
    ['--plane-ink' as string]: `var(--plane-${plane}-ink)`,
  }
}

/** One icon per node kind (purpose), drawn in the kind's colour (index.css `--kind-*`). */
export const KIND_ICON: Record<NodeKind, LucideIcon> = {
  code: Code,
  config: SlidersHorizontal,
  prompt: MessageSquareText,
  store: Database,
  queue: ListOrdered,
  model: Cpu,
}

export const KIND_HINT: Record<NodeKind, string> = {
  code: 'code: the inspector shows its source',
  config: 'config: rules in a config file',
  prompt: 'prompt: text that carries intent',
  store: 'store: records it keeps',
  queue: 'queue: items waiting for someone',
  model: 'model: calls a language model',
}

/** The stroke colour of an edge: its kind, or for a request its source plane (lighter when it replies). */
export function edgeColor(kind: EdgeKind, sourcePlane: PlaneId, reverse: boolean): string {
  if (kind !== 'request') return `var(--edge-${kind})`
  const plane = `var(--plane-${sourcePlane})`
  return reverse ? `color-mix(in oklab, ${plane}, var(--edge-reply-mix) var(--edge-reply-amount))` : plane
}

export const STATE_LABEL: Record<string, string> = {
  idle: 'idle',
  active: 'active',
  waiting: 'waiting',
  done: 'done',
  error: 'error',
  skipped: 'skipped',
}
