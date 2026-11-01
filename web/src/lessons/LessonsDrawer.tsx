import { useRef, type RefObject } from 'react'
import { ArrowRight, BookOpen, CircleCheck, CircleDashed, Eye } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { useApp } from '@/state/store'
import { stepEntry, type OutlineEntry } from './outline'
import { lessonRef } from './parse'
import { chapterStatus } from './progress'
import { openChapter, requestLessonFocus, useOutline } from './useLessons'
import { useLessonProgress } from './useProgress'

const num = (n: number) => String(n).padStart(2, '0')

/** Read, checked, or neither: an icon and the words, so the state is never colour or icon alone. */
function ProgressText({ slug, written }: { slug: string; written: boolean }) {
  const entry = useLessonProgress((s) => s.progress[slug])
  const status = chapterStatus({ [slug]: entry ?? {} }, slug)
  if (!written) return <span className="text-xs text-muted-foreground">Not written yet</span>
  const [Icon, text] = status.checked
    ? [CircleCheck, 'Read, checked']
    : status.read
      ? [Eye, status.answered ? `Read, ${status.answered} answered` : 'Read']
      : [CircleDashed, 'Not started']
  return (
    <span className="flex items-center gap-1 text-xs text-muted-foreground" data-testid="lesson-progress" data-read={status.read} data-checked={status.checked}>
      <Icon className={cn('size-3.5', status.checked && 'text-state-done')} aria-hidden />
      {text}
    </span>
  )
}

/** The chapter list and Next chapter, mounted while the drawer is open (it fetches the list). */
function DrawerBody({ onGo }: { onGo: (entry: OutlineEntry) => void }) {
  const { outline, status } = useOutline()
  const current = useApp((s) => {
    const node = s.diagram?.nodes.find((n) => n.id === s.selectedNode)
    return lessonRef(node?.lesson_url)?.slug ?? null
  })
  const progress = useLessonProgress((s) => s.progress)
  const walk = outline.filter((e) => !e.group)
  const inside = outline.filter((e) => e.group)
  // After the open chapter, else the first written one not read yet.
  const next = current
    ? stepEntry(outline, current, 1)
    : (outline.find((e) => e.written && !progress[e.slug]?.read) ?? outline.find((e) => e.written) ?? null)

  const row = (e: OutlineEntry) => (
    <li key={e.slug}>
      <button
        type="button"
        onClick={() => onGo(e)}
        disabled={!e.nodeId}
        aria-current={e.slug === current ? 'true' : undefined}
        className={cn(
          'flex w-full cursor-pointer flex-col gap-1 rounded-2xl px-3 py-2.5 text-left transition-colors duration-200 outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/30 disabled:pointer-events-none disabled:opacity-50',
          e.slug === current && 'bg-muted',
        )}
        data-testid="lesson-row"
        data-slug={e.slug}
      >
        <span className="flex items-baseline gap-2">
          <span className="font-mono text-xs text-muted-foreground tabular-nums">{num(e.n)}</span>
          <span className={cn('min-w-0 flex-1 text-sm font-medium', !e.written && 'text-muted-foreground')}>{e.title}</span>
          {e.group && <span className="text-xs text-muted-foreground">inside</span>}
        </span>
        {e.hook && <span className="line-clamp-2 pl-7 text-xs text-muted-foreground">{e.hook}</span>}
        <span className="pl-7">
          <ProgressText slug={e.slug} written={e.written} />
        </span>
      </button>
    </li>
  )

  return (
    <>
      <ScrollArea className="min-h-0 flex-1">
        {status === 'error' && <p className="p-4 text-xs text-destructive">Could not load the chapter list.</p>}
        <nav aria-label="Chapters" className="flex flex-col gap-3 p-2">
          <ol className="flex flex-col gap-0.5">{walk.map(row)}</ol>
          {inside.length > 0 && (
            <>
              <p className="px-3 pt-2 text-xs text-muted-foreground">Inside the expert groups</p>
              <ol className="flex flex-col gap-0.5">{inside.map(row)}</ol>
            </>
          )}
        </nav>
      </ScrollArea>
      {next && (
        <SheetFooter className="border-t p-4">
          <Button type="button" onClick={() => onGo(next)} disabled={!next.nodeId} data-testid="drawer-next">
            <span className="min-w-0 truncate">
              {current ? 'Next chapter' : 'Start reading'}: {num(next.n)} {next.title}
            </span>
            <ArrowRight aria-hidden />
          </Button>
        </SheetFooter>
      )}
    </>
  )
}

/**
 * The lessons drawer (LessonsButton in the header opens it, and loads this chunk on first use):
 * every chapter in reading order with its hook and this viewer's progress, and Next chapter. A
 * Sheet (a Radix Dialog): focus is trapped while it is open, Esc closes it and focus returns to
 * the button. Opening a chapter closes the drawer and moves focus to the chapter instead.
 */
export function LessonsDrawer({ open, onOpenChange, returnFocus }: { open: boolean; onOpenChange: (open: boolean) => void; returnFocus: RefObject<HTMLButtonElement | null> }) {
  const navigated = useRef(false)

  const go = (entry: OutlineEntry) => {
    navigated.current = true
    openChapter(entry)
    onOpenChange(false)
  }

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="left"
        className="w-[min(26rem,100vw)] gap-0 sm:max-w-none"
        data-testid="lessons-drawer"
        onCloseAutoFocus={(e) => {
          e.preventDefault()
          if (navigated.current) {
            navigated.current = false
            requestLessonFocus()
          } else returnFocus.current?.focus()
        }}
      >
        <SheetHeader className="gap-1 border-b p-4 pr-14">
          <SheetTitle className="flex items-center gap-2">
            <BookOpen className="size-4 text-muted-foreground" aria-hidden />
            Lessons
          </SheetTitle>
          <SheetDescription className="text-xs">
            One chapter per node, in walk order, then the expert groups. Your progress stays in this browser.
          </SheetDescription>
        </SheetHeader>
        <DrawerBody onGo={go} />
      </SheetContent>
    </Sheet>
  )
}
