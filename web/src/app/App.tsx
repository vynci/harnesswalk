import { lazy, Suspense, useEffect, useState, useSyncExternalStore } from 'react'
import { useDefaultLayout, usePanelRef } from 'react-resizable-panels'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { TooltipProvider } from '@/components/ui/tooltip'
import { ChatPanel } from '@/chat/ChatPanel'
import { ReplayBar } from '@/replay/ReplayBar'
import { usePanels } from '@/state/panels'
import { layoutStorage } from '@/state/persist'
import { useApp } from '@/state/store'
import { Header } from './Header'
import { Notices } from './Notices'
import { useSandbox } from './useSandbox'
import { useThemeClass } from './useTheme'

// React Flow and the layout router are the biggest part of the app: their own chunk, fetched in
// parallel with the API boot.
const diagramChunk = import('@/diagram/DiagramPanel')
const DiagramPanel = lazy(() => diagramChunk.then((m) => ({ default: m.DiagramPanel })))
// The inspector carries markdown and syntax highlighting; load it on first use.
const DocReader = lazy(() => import('@/chat/DocReader').then((m) => ({ default: m.DocReader })))
const InspectorDrawer = lazy(() => import('@/inspector/InspectorDrawer').then((m) => ({ default: m.InspectorDrawer })))
// The Guide (chapters, markdown, the questionnaire): fetched the first time it opens.
const guideChunk = () => import('@/lessons/GuideDrawer')
const GuidePane = lazy(() => guideChunk().then((m) => ({ default: m.GuidePane })))
const GuideSheet = lazy(() => guideChunk().then((m) => ({ default: m.GuideSheet })))

/**
 * Where the Guide goes: beside the inspector on a very wide screen, under it in the same right
 * column on a laptop (the diagram keeps its width), and a Sheet over the page on a narrow one.
 */
type GuideMode = 'side' | 'stack' | 'sheet'
const SIDE = '(min-width: 1680px)'
const STACK = '(min-width: 1024px)'

function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (change) => {
      const mq = globalThis.matchMedia?.(query)
      mq?.addEventListener('change', change)
      return () => mq?.removeEventListener('change', change)
    },
    () => globalThis.matchMedia?.(query).matches ?? true,
  )
}

function useGuideMode(): GuideMode {
  const side = useMedia(SIDE)
  const stack = useMedia(STACK)
  return side ? 'side' : stack ? 'stack' : 'sheet'
}

const paneFallback = (text: string) => <div className="h-full bg-background p-4 text-sm text-muted-foreground">{text}</div>

function InspectorSlot({ compact = false }: { compact?: boolean }) {
  return (
    <Suspense fallback={paneFallback('Loading…')}>
      <InspectorDrawer compact={compact} />
    </Suspense>
  )
}

function GuideSlot() {
  return (
    <Suspense fallback={paneFallback('Loading the Guide…')}>
      <GuidePane />
    </Suspense>
  )
}

/**
 * The right column on a laptop: the inspector over the Guide, the split kept per viewer. A code
 * walk link in the chapter grows the inspector, so the code it opens has room.
 */
function Stack() {
  const layout = useDefaultLayout({ id: 'guide-stack', storage: layoutStorage, onlySaveAfterUserInteractions: true })
  const inspector = usePanelRef()
  const codeKey = usePanels((s) => s.codeLink?.key)
  useEffect(() => {
    const panel = inspector.current
    if (codeKey && panel && panel.getSize().asPercentage < 65) panel.resize('65')
  }, [codeKey, inspector])
  return (
    <ResizablePanelGroup orientation="vertical" defaultLayout={layout.defaultLayout} onLayoutChanged={layout.onLayoutChanged}>
      <ResizablePanel id="stack-inspector" panelRef={inspector} defaultSize="45" minSize="20" className="min-h-0">
        <InspectorSlot compact />
      </ResizablePanel>
      <ResizableHandle withHandle aria-label="Resize inspector and guide" />
      <ResizablePanel id="stack-guide" defaultSize="55" minSize="20" className="min-h-0">
        <GuideSlot />
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}

export function App() {
  useThemeClass()
  useSandbox()
  const ready = useApp((s) => s.ready)
  const bootError = useApp((s) => s.bootError)

  return (
    <TooltipProvider>
      <div className="flex h-full flex-col">
        <Header />
        {bootError ? (
          <main className="m-auto max-w-md p-6 text-center">
            <h2 className="text-lg font-medium">Cannot reach the harness API</h2>
            <p className="mt-2 text-sm text-muted-foreground">{bootError}</p>
            <p className="mt-4 text-sm text-muted-foreground">
              Start it with <code className="font-mono">make run</code>, or try the UI without an API using{' '}
              <code className="font-mono">pnpm dev:mock</code>.
            </p>
          </main>
        ) : !ready ? (
          <main className="m-auto text-sm text-muted-foreground" aria-busy="true">
            Loading the diagram…
          </main>
        ) : (
          <main className="min-h-0 flex-1">
            <Split />
          </main>
        )}
        <Notices />
        <DocSlot />
      </div>
    </TooltipProvider>
  )
}

/** Chat on the left, the workspace (diagram and inspector) on the right, sizes kept per viewer. */
function Split() {
  const layout = useDefaultLayout({ id: 'split', storage: layoutStorage, onlySaveAfterUserInteractions: true })
  return (
    <ResizablePanelGroup orientation="horizontal" defaultLayout={layout.defaultLayout} onLayoutChanged={layout.onLayoutChanged}>
      <ResizablePanel id="chat-pane" defaultSize="34" minSize="22" className="min-w-0">
        <ChatPanel />
      </ResizablePanel>
      <ResizableHandle withHandle aria-label="Resize chat and diagram" />
      <ResizablePanel id="workspace-pane" minSize="35" className="min-w-0">
        <Workspace />
      </ResizablePanel>
    </ResizablePanelGroup>
  )
}

/**
 * The diagram, with the inspector docked beside it as a resizable pane while a node is open (not
 * a modal Sheet, so step mode keeps it open), and the Guide while it is open: in the same right
 * column under the inspector, or beside it on a very wide screen. Widths are kept per viewer.
 */
function Workspace() {
  const selected = useApp((s) => s.selectedNode)
  const guideOpen = usePanels((s) => s.guideOpen)
  const mode = useGuideMode()
  const open = !!selected
  const side = guideOpen && mode === 'side'
  const stacked = guideOpen && mode === 'stack'
  // The right column: the inspector, the Guide, or both stacked.
  const right = open || stacked
  // The Sheet stays mounted once used, so it closes with its animation and returns focus.
  const [sheetUsed, setSheetUsed] = useState(false)
  if (guideOpen && mode === 'sheet' && !sheetUsed) setSheetUsed(true)
  const layout = useDefaultLayout({
    id: 'inspector',
    panelIds: ['diagram-pane', ...(right ? ['inspector-pane'] : []), ...(side ? ['guide-pane'] : [])],
    storage: layoutStorage,
    onlySaveAfterUserInteractions: true,
  })
  return (
    <>
      <ResizablePanelGroup orientation="horizontal" defaultLayout={layout.defaultLayout} onLayoutChanged={layout.onLayoutChanged}>
        <ResizablePanel id="diagram-pane" minSize={side && open ? '20' : '30'} className="relative min-w-0">
          <Suspense fallback={<p className="p-4 text-sm text-muted-foreground" aria-busy="true">Loading the diagram…</p>}>
            <DiagramPanel />
          </Suspense>
          <ReplayBar />
        </ResizablePanel>
        {right && (
          <>
            <ResizableHandle withHandle aria-label={open ? 'Resize diagram and inspector' : 'Resize diagram and guide'} />
            <ResizablePanel id="inspector-pane" defaultSize={side ? 380 : 440} minSize={320} maxSize="70" className="min-w-0">
              {stacked && open ? <Stack /> : stacked ? <GuideSlot /> : <InspectorSlot />}
            </ResizablePanel>
          </>
        )}
        {side && (
          <>
            <ResizableHandle withHandle aria-label={open ? 'Resize inspector and guide' : 'Resize diagram and guide'} />
            <ResizablePanel id="guide-pane" defaultSize={420} minSize={320} maxSize="50" className="min-w-0">
              <GuideSlot />
            </ResizablePanel>
          </>
        )}
      </ResizablePanelGroup>
      {sheetUsed && mode === 'sheet' && (
        <Suspense fallback={null}>
          <GuideSheet />
        </Suspense>
      )}
    </>
  )
}

/** The knowledge document reader, loaded the first time a citation or document is opened. */
function DocSlot() {
  const doc = useApp((s) => s.doc)
  if (!doc) return null
  return (
    <Suspense fallback={null}>
      <DocReader />
    </Suspense>
  )
}
