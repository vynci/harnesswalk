import { lazy, Suspense } from 'react'
import { Group, Panel, Separator } from 'react-resizable-panels'
import { TooltipProvider } from '@/components/ui/tooltip'
import { ChatPanel } from '@/chat/ChatPanel'
import { ReplayBar } from '@/replay/ReplayBar'
import { useApp } from '@/state/store'
import { Header } from './Header'
import { Notices } from './Notices'
import { useThemeClass } from './useTheme'

// React Flow and the layout router are the biggest part of the app: their own chunk, fetched in
// parallel with the API boot.
const diagramChunk = import('@/diagram/DiagramPanel')
const DiagramPanel = lazy(() => diagramChunk.then((m) => ({ default: m.DiagramPanel })))
// The inspector carries markdown and syntax highlighting; load it on first use.
const InspectorDrawer = lazy(() => import('@/inspector/InspectorDrawer').then((m) => ({ default: m.InspectorDrawer })))

export function App() {
  useThemeClass()
  const ready = useApp((s) => s.ready)
  const bootError = useApp((s) => s.bootError)

  return (
    <TooltipProvider>
      <div className="flex h-full flex-col">
        <Header />
        {bootError ? (
          <main className="m-auto max-w-md p-6 text-center">
            <h2 className="text-lg font-semibold">Cannot reach the harness API</h2>
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
            <Group orientation="horizontal" className="h-full">
              <Panel defaultSize="34" minSize="22" className="min-w-0">
                <ChatPanel />
              </Panel>
              <Separator
                className="w-1.5 bg-border transition-colors hover:bg-ring/60 focus-visible:bg-ring focus-visible:outline-none data-[separator=active]:bg-ring"
                aria-label="Resize chat and diagram"
              />
              <Panel minSize="35" className="relative min-w-0">
                <Suspense fallback={<p className="p-4 text-sm text-muted-foreground" aria-busy="true">Loading the diagram…</p>}>
                  <DiagramPanel />
                </Suspense>
                <ReplayBar />
                <InspectorSlot />
              </Panel>
            </Group>
          </main>
        )}
        <Notices />
      </div>
    </TooltipProvider>
  )
}

function InspectorSlot() {
  const selected = useApp((s) => s.selectedNode)
  if (!selected) return null
  return (
    <Suspense fallback={<div className="absolute inset-y-0 right-0 z-30 w-full max-w-[460px] border-l bg-background p-4 text-sm text-muted-foreground">Loading…</div>}>
      <InspectorDrawer />
    </Suspense>
  )
}
