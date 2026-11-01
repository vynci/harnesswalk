import { lazy, Suspense } from 'react'
import { useDefaultLayout } from 'react-resizable-panels'
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable'
import { TooltipProvider } from '@/components/ui/tooltip'
import { ChatPanel } from '@/chat/ChatPanel'
import { ReplayBar } from '@/replay/ReplayBar'
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
 * a modal Sheet, so step mode keeps it open). Its width is kept per viewer too.
 */
function Workspace() {
  const selected = useApp((s) => s.selectedNode)
  const open = !!selected
  const layout = useDefaultLayout({
    id: 'inspector',
    panelIds: open ? ['diagram-pane', 'inspector-pane'] : ['diagram-pane'],
    storage: layoutStorage,
    onlySaveAfterUserInteractions: true,
  })
  return (
    <ResizablePanelGroup orientation="horizontal" defaultLayout={layout.defaultLayout} onLayoutChanged={layout.onLayoutChanged}>
      <ResizablePanel id="diagram-pane" minSize="30" className="relative min-w-0">
        <Suspense fallback={<p className="p-4 text-sm text-muted-foreground" aria-busy="true">Loading the diagram…</p>}>
          <DiagramPanel />
        </Suspense>
        <ReplayBar />
      </ResizablePanel>
      {open && (
        <>
          <ResizableHandle withHandle aria-label="Resize diagram and inspector" />
          <ResizablePanel id="inspector-pane" defaultSize={440} minSize={320} maxSize="70" className="min-w-0">
            <Suspense fallback={<div className="h-full bg-background p-4 text-sm text-muted-foreground">Loading…</div>}>
              <InspectorDrawer />
            </Suspense>
          </ResizablePanel>
        </>
      )}
    </ResizablePanelGroup>
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
