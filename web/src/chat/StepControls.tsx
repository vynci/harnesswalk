import { FastForward, Redo2, StepForward } from 'lucide-react'
import type { ResumeMode } from '@/api/client'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'
import { Item, ItemActions, ItemContent, ItemDescription, ItemMedia, ItemTitle } from '@/components/ui/item'

/** A breakpoint in step mode, as an outline Item: where the turn stopped, Continue, Step over and Next. */
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
  onResume: (mode: ResumeMode) => Promise<void>
}) {
  return (
    <Item variant="outline" size="sm" role="group" aria-label="Step mode controls" data-node={nodeId} data-seq={seq} data-testid="step-controls">
      <ItemMedia variant="icon">
        <StepForward className="text-state-waiting" aria-hidden />
      </ItemMedia>
      <ItemContent className="min-w-0">
        <ItemTitle className="line-clamp-none">Paused at {nodeLabel}</ItemTitle>
        <ItemDescription className="text-xs">Step mode: Next moves one hop, Step over skips the expert children.</ItemDescription>
      </ItemContent>
      <ItemActions>
        <Button size="sm" variant="outline" onClick={() => void onResume('continue')} aria-label="Continue to the end">
          <FastForward aria-hidden /> Continue
        </Button>
        <Hint label="Run on to the next simple-view node, without stopping inside expert children" side="top">
          <Button size="sm" variant="outline" onClick={() => void onResume('step_over')} aria-label="Step over expert children" data-testid="step-over">
            <Redo2 aria-hidden /> Step over
          </Button>
        </Hint>
        <Button size="sm" onClick={() => void onResume('step')} aria-label="Next node" autoFocus>
          <StepForward aria-hidden /> Next
        </Button>
      </ItemActions>
    </Item>
  )
}
