import { BookOpen } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'
import { usePanels } from '@/state/panels'
import { useApp } from '@/state/store'

/**
 * The header's Guide button: opens the Guide on the chapter of the node open on the canvas, or
 * where the reader left it (the chapter list the first time), and closes it again.
 */
export function GuideButton() {
  const open = usePanels((s) => s.guideOpen)
  return (
    <Hint label={open ? 'Close the Guide' : 'Guide: a chapter per node'}>
      <Button
        id="guide-button"
        size="sm"
        variant="ghost"
        className={open ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'}
        aria-label="Guide"
        aria-expanded={open}
        onClick={() => {
          const panels = usePanels.getState()
          if (open) panels.closeGuide()
          else panels.openGuide(useApp.getState().selectedNode ?? undefined)
        }}
        data-testid="guide-button"
      >
        <BookOpen aria-hidden />
        <span className="hidden sm:inline">Guide</span>
      </Button>
    </Hint>
  )
}
