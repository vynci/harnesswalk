import { FastForward, StepForward } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ButtonGroup } from '@/components/ui/button-group'
import { Marker, MarkerContent, MarkerIcon } from '@/components/ui/marker'

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
    <div
      data-node={nodeId}
      data-seq={seq}
      className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-state-waiting bg-card px-3 py-2"
      data-testid="step-controls"
    >
      <Marker className="w-auto text-xs text-foreground">
        <MarkerIcon>
          <StepForward className="size-3.5 text-state-waiting" />
        </MarkerIcon>
        <MarkerContent>
          Paused at <span className="font-medium">{nodeLabel}</span>
        </MarkerContent>
      </Marker>
      <ButtonGroup className="ml-auto" aria-label="Step mode controls">
        <Button size="sm" onClick={() => void onResume('step')} aria-label="Next node" autoFocus>
          <StepForward aria-hidden /> Next
        </Button>
        <Button size="sm" variant="outline" onClick={() => void onResume('continue')} aria-label="Continue to the end">
          <FastForward aria-hidden /> Continue
        </Button>
      </ButtonGroup>
    </div>
  )
}
