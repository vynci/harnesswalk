import type { NodeInfo } from '@/api/types'

/** The name of the kind-specific inspector tab, or null for code nodes (the Code tab covers them). */
export function detailsTabLabel(kind: NodeInfo['inspector']['type']): string | null {
  switch (kind) {
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
    default:
      return null
  }
}
