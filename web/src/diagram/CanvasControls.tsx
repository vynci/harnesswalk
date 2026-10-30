import { Hint } from '@/components/hint'
import { Card } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { Switch } from '@/components/ui/switch'
import { useApp } from '@/state/store'
import { Legend } from './Legend'

/**
 * The diagram's own controls, one compact card at the bottom right of the canvas: the Expert
 * view and Telemetry switches and the Legend button. In the expert view every edge is drawn, so
 * Telemetry shows on and disabled there (the card keeps its size when the view flips).
 */
export function CanvasControls() {
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const showTelemetry = useApp((s) => s.showTelemetry)
  const setShowTelemetry = useApp((s) => s.setShowTelemetry)
  const expert = view === 'expert'
  return (
    <Card
      size="sm"
      role="group"
      aria-label="Diagram controls"
      className="absolute right-3 bottom-8 z-10 flex-row items-center gap-3 rounded-2xl bg-card/95 py-1.5 pr-1.5 pl-3 text-xs"
      data-testid="canvas-controls"
    >
      <Hint label="Expand each group into its parts and draw every edge." side="top">
        <label className="flex cursor-pointer items-center gap-1.5">
          <Switch size="sm" checked={expert} onCheckedChange={(on) => setView(on ? 'expert' : 'simple')} aria-label="Expert view" data-testid="view-toggle" />
          Expert view
        </label>
      </Hint>
      <Hint
        label={
          expert
            ? 'The expert view always draws every edge to and from LLM Ops.'
            : 'Draw every edge to and from LLM Ops (telemetry, eval, release) instead of one telemetry bus. Hovering a node shows its own.'
        }
        side="top"
      >
        <label className="flex cursor-pointer items-center gap-1.5 has-disabled:cursor-default has-disabled:text-muted-foreground">
          <Switch size="sm" checked={expert || showTelemetry} disabled={expert} onCheckedChange={setShowTelemetry} aria-label="Telemetry" data-testid="telemetry-toggle" />
          Telemetry
        </label>
      </Hint>
      <Separator orientation="vertical" className="my-1" />
      <Legend />
    </Card>
  )
}
