import { lazy, Suspense, useRef, useState } from 'react'
import { GraduationCap } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'

// The drawer (a Sheet with the chapter list) is its own chunk, fetched on the first open.
const LessonsDrawer = lazy(() => import('./LessonsDrawer').then((m) => ({ default: m.LessonsDrawer })))

/** The header's Lessons button, which opens the lessons drawer. */
export function LessonsButton() {
  const [open, setOpen] = useState(false)
  const [used, setUsed] = useState(false)
  const button = useRef<HTMLButtonElement>(null)
  return (
    <>
      <Hint label="Lessons: a chapter per node">
        <Button
          ref={button}
          size="sm"
          variant="ghost"
          className="text-muted-foreground hover:text-foreground"
          aria-label="Lessons"
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => {
            setUsed(true)
            setOpen(true)
          }}
          data-testid="lessons-button"
        >
          <GraduationCap aria-hidden />
          <span className="hidden sm:inline">Lessons</span>
        </Button>
      </Hint>
      {used && (
        <Suspense fallback={null}>
          <LessonsDrawer open={open} onOpenChange={setOpen} returnFocus={button} />
        </Suspense>
      )}
    </>
  )
}
