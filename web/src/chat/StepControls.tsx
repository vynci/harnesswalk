import { FastForward, StepForward } from 'lucide-react'
import { Button } from '@/components/ui/button'

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
      className="flex flex-wrap items-center gap-2 rounded-lg border border-dashed border-[var(--state-waiting)] bg-card px-3 py-2"
      role="group"
      aria-label="Step mode controls"
      data-testid="step-controls"
    >
      <span className="text-xs">
        Paused at <span className="font-medium">{nodeLabel}</span>
      </span>
      <span className="ml-auto flex gap-2">
        <Button size="sm" onClick={() => void onResume('step')} aria-label="Next node" autoFocus>
          <StepForward aria-hidden /> Next
        </Button>
        <Button size="sm" variant="outline" onClick={() => void onResume('continue')} aria-label="Continue to the end">
          <FastForward aria-hidden /> Continue
        </Button>
      </span>
    </div>
  )
}
