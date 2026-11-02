import { Hint } from '@/components/hint'
import { Card } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { Switch } from '@/components/ui/switch'
import { useApp } from '@/state/store'
import { Legend } from './Legend'

/**
 * The diagram's own controls, one compact card at the top right of the canvas: the Advanced view
 * switch and the Legend button. Telemetry edges (traces, eval, release) are always drawn.
 */
export function CanvasControls() {
  const view = useApp((s) => s.view)
  const setView = useApp((s) => s.setView)
  const expert = view === 'expert'
  return (
    <Card
      size="sm"
      role="group"
      aria-label="Diagram controls"
      className="absolute top-3 right-3 z-10 flex-row items-center gap-3 bg-card/95 py-1.5 pr-1.5 pl-3 text-xs"
      data-testid="canvas-controls"
    >
      <Hint label="Expand each group into its parts and draw every edge." side="bottom">
        <label className="flex cursor-pointer items-center gap-1.5">
          <Switch size="sm" checked={expert} onCheckedChange={(on) => setView(on ? 'expert' : 'simple')} aria-label="Advanced view" data-testid="view-toggle" />
          Advanced view
        </label>
      </Hint>
      <Separator orientation="vertical" className="my-1" />
      <Legend />
    </Card>
  )
}
