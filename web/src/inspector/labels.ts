import type { NodeInfo } from '@/api/types'

/** Fields every `source` inspector has; anything else is the node's own state worth a tab. */
const SOURCE_FIELDS = new Set(['type', 'path', 'language', 'content', 'last_summary'])

/** Extra fields of a `source` inspector (the memory writer's writes, the builder's last context). */
export function sourceExtras(ins: NodeInfo['inspector']): string[] {
  return ins.type === 'source' ? Object.keys(ins).filter((k) => !SOURCE_FIELDS.has(k)) : []
}

/**
 * The name of the kind-specific inspector tab, or null for a code node with nothing besides its
 * source (the Code tab covers it). Named by what the payload holds, never by node id.
 */
export function detailsTabLabel(ins: NodeInfo['inspector']): string | null {
  switch (ins.type) {
    case 'config':
      return 'Config'
    case 'prompt':
      return 'Prompt'
    case 'store':
      return 'Records'
    case 'queue':
      return 'Queue'
    case 'model':
      return 'Models'
    default: {
      const extras = sourceExtras(ins)
      if (extras.includes('last_writes') || extras.includes('pending')) return 'Writes'
      if (extras.includes('last_context')) return 'Context'
      return extras.length ? 'State' : null
    }
  }
}
