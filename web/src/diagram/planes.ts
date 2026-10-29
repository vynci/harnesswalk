import type { CSSProperties } from 'react'
import type { PlaneId } from '@/api/types'

/** CSS variables that colour a node or group by its plane (see index.css). */
export function planeStyle(plane: PlaneId): CSSProperties {
  return {
    ['--plane-stroke' as string]: `var(--plane-${plane})`,
    ['--plane-fill' as string]: `var(--plane-${plane}-fill)`,
    ['--plane-group' as string]: `var(--plane-${plane}-group)`,
  }
}

export const STATE_LABEL: Record<string, string> = {
  idle: 'idle',
  active: 'active',
  waiting: 'waiting',
  done: 'done',
  error: 'error',
  skipped: 'skipped',
}
