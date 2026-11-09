// The Guide (once the lessons drawer): the chapter list with this viewer's progress, and
// one chapter at a time with its Try buttons. Docked as a pane at the right
// edge of the workspace beside the inspector on a wide screen, a Sheet over it on a narrow one.
import { useEffect, useRef, type ReactNode } from 'react'
import { ArrowRight, BookOpen, CircleDashed, Eye, List, X } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { cn } from '@/lib/utils'
import { useProfile } from '@/state/hooks'
import { usePanels } from '@/state/panels'
import { gapText, profileGap } from '@/state/profiles'
import { useApp } from '@/state/store'
import { GuideChapter } from './GuideChapter'
import { stepEntry, type OutlineEntry } from './outline'
import { lessonRef } from './parse'
import { chapterStatus } from './progress'
import { openChapter, useOutline } from './useLessons'
import { useLessonProgress } from './useProgress'

const num = (n: number) => String(n).padStart(2, '0')

/** Read or not: an icon and the word, so the state is never colour or icon alone. */
function ProgressText({ slug, written }: { slug: string; written: boolean }) {
  const entry = useLessonProgress((s) => s.progress[slug])
  const status = chapterStatus({ [slug]: entry ?? {} }, slug)
  if (!written) return <span className="text-xs text-muted-foreground">Not written yet</span>
  const [Icon, text] = status.read ? [Eye, 'Read'] : [CircleDashed, 'Not started']
  return (
    <span className="flex items-center gap-1 text-xs text-muted-foreground" data-testid="lesson-progress" data-read={status.read}>
      <Icon className="size-3.5" aria-hidden />
      {text}
    </span>
  )
}

/** The chapter list and Next chapter (it fetches the list). */
function Contents({ current, onGo }: { current: string | null; onGo: (entry: OutlineEntry) => void }) {
  const { outline, status } = useOutline()
  const progress = useLessonProgress((s) => s.progress)
  // 0.2.0: a chapter whose node the pack's profile switches off is greyed, still readable.
  const profile = useProfile()
  const profiles = useApp((s) => s.profiles)
  const walk = outline.filter((e) => !e.group)
  const inside = outline.filter((e) => e.group)
  // After the open chapter, else the first written one not read yet.
  const next = current
    ? stepEntry(outline, current, 1)
    : (outline.find((e) => e.written && !progress[e.slug]?.read) ?? outline.find((e) => e.written) ?? null)

  const row = (e: OutlineEntry) => {
    const gap = profileGap(profile, profiles, e.requires)
    return (
    <li key={e.slug}>
      <button
        type="button"
        onClick={() => onGo(e)}
        disabled={!e.nodeId}
        aria-current={e.slug === current ? 'true' : undefined}
        className={cn(
          'flex w-full cursor-pointer flex-col gap-1 rounded-md px-3 py-2.5 text-left transition-colors duration-200 outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/30 disabled:pointer-events-none disabled:opacity-50',
          e.slug === current && 'bg-muted',
          gap && 'opacity-60',
        )}
        data-testid="lesson-row"
        data-slug={e.slug}
        data-profile-off={gap ? 'true' : undefined}
      >
        <span className="flex items-baseline gap-2">
          <span className="font-mono text-xs text-muted-foreground tabular-nums">{num(e.n)}</span>
          <span className={cn('min-w-0 flex-1 text-sm font-medium', !e.written && 'text-muted-foreground')}>{e.title}</span>
          {e.group && <span className="text-xs text-muted-foreground">inside</span>}
        </span>
        {e.hook && <span className="line-clamp-2 pl-7 text-xs text-muted-foreground">{e.hook}</span>}
        {gap && (
          <span className="pl-7 text-xs text-muted-foreground" data-testid="lesson-row-profile">
            {gapText(gap)}
          </span>
        )}
        <span className="pl-7">
          <ProgressText slug={e.slug} written={e.written} />
        </span>
      </button>
    </li>
    )
  }

  return (
    <>
      <ScrollArea className="min-h-0 flex-1" data-testid="guide-contents">
        <p className="px-5 pt-3 text-xs text-muted-foreground">
          One chapter per node, in walk order, then the expert groups. Pick a node on the canvas to open its chapter. Your progress stays in this browser.
        </p>
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
        <footer className="flex flex-col border-t p-4">
          <Button type="button" onClick={() => onGo(next)} disabled={!next.nodeId} data-testid="drawer-next">
            <span className="min-w-0 truncate">
              {current ? 'Next chapter' : 'Start reading'}: {num(next.n)} {next.title}
            </span>
            <ArrowRight aria-hidden />
          </Button>
        </footer>
      )}
    </>
  )
}

/** The node whose chapter the Guide shows, if any. */
function useGuideNode() {
  const nodeId = usePanels((s) => s.guideNode)
  const chapter = usePanels((s) => s.guideSlug)
  const node = useApp((s) => s.diagram?.nodes.find((n) => n.id === nodeId))
  return { node, chapter }
}

/** What the Guide shows: a node's chapter, or the chapter list. */
function GuideBody() {
  const { node, chapter } = useGuideNode()
  // In the list, the chapter of the node open on the canvas is the current one.
  const current = useApp((s) => lessonRef(s.diagram?.nodes.find((n) => n.id === s.selectedNode)?.lesson_url)?.slug ?? null)
  if (node) {
    return (
      <ScrollArea className="min-h-0 flex-1">
        <div className="p-4">
          <GuideChapter key={`${node.id}:${chapter ?? ''}`} node={node} chapter={chapter} />
        </div>
      </ScrollArea>
    )
  }
  return <Contents current={current} onGo={openChapter} />
}

function ContentsButton() {
  const { node } = useGuideNode()
  const openGuide = usePanels((s) => s.openGuide)
  if (!node) return null
  return (
    <Button type="button" variant="ghost" size="xs" className="text-muted-foreground" onClick={() => openGuide(null)} data-testid="guide-contents-button">
      <List aria-hidden />
      All chapters
    </Button>
  )
}

const TITLE = 'Guide'
const DESCRIPTION = 'A chapter per node of the harness, with a demonstration and a few questions.'

function Title({ children }: { children?: ReactNode }) {
  return (
    <span className="flex items-center gap-2">
      <BookOpen className="size-4 text-muted-foreground" aria-hidden />
      {children ?? TITLE}
    </span>
  )
}

/**
 * The Guide docked at the right edge of the workspace (wide screens): not modal, so the inspector
 * and the chat stay usable beside it. Esc or the close button closes it, and focus goes back to
 * the header's Guide button.
 */
export function GuidePane() {
  const closeGuide = usePanels((s) => s.closeGuide)
  const close = useRef<HTMLButtonElement>(null)
  // Opened from the header with no chapter to focus: focus the pane's close button.
  useEffect(() => {
    if (!document.activeElement || document.activeElement === document.body || document.activeElement.id === 'guide-button') close.current?.focus()
  }, [])
  const done = () => {
    closeGuide()
    document.getElementById('guide-button')?.focus()
  }
  return (
    <aside
      className="flex h-full w-full min-w-0 flex-col bg-background"
      aria-labelledby="guide-heading"
      data-testid="guide-drawer"
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation()
          done()
        }
      }}
    >
      <header className="flex items-center gap-2 border-b px-4 py-3">
        <h2 id="guide-heading" className="text-base font-medium">
          <Title />
        </h2>
        <p className="sr-only">{DESCRIPTION}</p>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          <ContentsButton />
          <Kbd aria-hidden>Esc</Kbd>
          <Hint label="Close the Guide">
            <Button ref={close} size="icon-sm" variant="ghost" onClick={done} aria-label="Close guide" aria-keyshortcuts="Escape" data-testid="guide-close">
              <X />
            </Button>
          </Hint>
        </span>
      </header>
      <GuideBody />
    </aside>
  )
}

/** The Guide on a narrow screen: a Sheet over the workspace, closed by its own button or Esc. */
export function GuideSheet() {
  const open = usePanels((s) => s.guideOpen)
  const closeGuide = usePanels((s) => s.closeGuide)
  return (
    <Sheet open={open} onOpenChange={(o) => !o && closeGuide()}>
      <SheetContent
        side="right"
        className="w-[min(28rem,100vw)] gap-0 sm:max-w-none"
        data-testid="guide-drawer"
        onCloseAutoFocus={(e) => {
          e.preventDefault()
          document.getElementById('guide-button')?.focus()
        }}
      >
        <div className="flex items-center gap-2 border-b p-4 pr-14">
          <SheetTitle className="text-base font-medium">
            <Title />
          </SheetTitle>
          <SheetDescription className="sr-only">{DESCRIPTION}</SheetDescription>
          <span className="ml-auto">
            <ContentsButton />
          </span>
        </div>
        <GuideBody />
      </SheetContent>
    </Sheet>
  )
}
