import { FastForward, StepForward } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Item, ItemActions, ItemContent, ItemDescription, ItemMedia, ItemTitle } from '@/components/ui/item'

/** A breakpoint in step mode, as an outline Item: where the turn stopped, Continue and Next. */
export function StepControls({
  nodeId,
  nodeLabel,
  seq,
  onResume,
}: {
  nodeId: string
  nodeLabel: string
  /** The seq of the node_paused event, so each pause is told apart from the last. */
  seq: number
  onResume: (mode: 'step' | 'continue') => Promise<void>
}) {
  return (
    <Item variant="outline" size="sm" role="group" aria-label="Step mode controls" data-node={nodeId} data-seq={seq} data-testid="step-controls">
      <ItemMedia variant="icon">
        <StepForward className="text-state-waiting" aria-hidden />
      </ItemMedia>
      <ItemContent className="min-w-0">
        <ItemTitle className="line-clamp-none">Paused at {nodeLabel}</ItemTitle>
        <ItemDescription className="text-xs">Step mode: Next moves one hop.</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button size="sm" variant="outline" onClick={() => void onResume('continue')} aria-label="Continue to the end">
          <FastForward aria-hidden /> Continue
        </Button>
        <Button size="sm" onClick={() => void onResume('step')} aria-label="Next node" autoFocus>
          <StepForward aria-hidden /> Next
        </Button>
      </ItemActions>
    </Item>
  )
}
