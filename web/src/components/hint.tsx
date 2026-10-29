import type { ReactElement, ReactNode } from 'react'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

/**
 * A tooltip on a control: the shadcn Tooltip with its trigger as the child itself. Use it on
 * every control without a visible label (it still needs an aria-label of its own) and for the
 * longer explanation of a labelled one.
 */
export function Hint({ label, side = 'bottom', children }: { label: ReactNode; side?: 'top' | 'right' | 'bottom' | 'left'; children: ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side={side} className="max-w-72">
        {label}
      </TooltipContent>
    </Tooltip>
  )
}
