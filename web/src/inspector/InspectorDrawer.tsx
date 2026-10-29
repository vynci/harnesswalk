import { useEffect, useMemo, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { KIND_HINT, KIND_ICON, planeStyle } from '@/diagram/planes'
import { useDisplayTurn } from '@/state/hooks'
import { useApp } from '@/state/store'
import { CodeView } from './CodeView'
import { DetailsTab } from './DetailsTab'
import { detailsTabLabel } from './labels'
import { EventsTab } from './EventsTab'
import { OverviewTab } from './OverviewTab'
import { useNodeData } from './useNodeData'

export function InspectorDrawer() {
  const nodeId = useApp((s) => s.selectedNode)
  const diagram = useApp((s) => s.diagram)
  const selectNode = useApp((s) => s.selectNode)
  const mode = useApp((s) => s.capabilities?.mode)
  const canDisableNodes = useApp((s) => s.capabilities?.features.disable_nodes !== false)
  const disabled = useApp((s) => s.disabled)
  const toggleDisabled = useApp((s) => s.toggleDisabled)
  const turn = useDisplayTurn()
  const { info, source } = useNodeData(nodeId)
  const [tab, setTab] = useState('overview')
  const closeRef = useRef<HTMLButtonElement>(null)

  const node = diagram?.nodes.find((n) => n.id === nodeId)
  const childIds = useMemo(() => new Set(diagram?.nodes.filter((n) => n.parent === nodeId).map((n) => n.id) ?? []), [diagram, nodeId])
  const events = useMemo(
    () => turn.events.filter((e) => e.node_id === nodeId || (e.node_id && childIds.has(e.node_id))),
    [turn.events, nodeId, childIds],
  )

  useEffect(() => {
    if (nodeId) closeRef.current?.focus()
  }, [nodeId])

  const detailsLabel = info.data ? detailsTabLabel(info.data.inspector.type) : null
  const activeTab = tab === 'details' && !detailsLabel ? 'overview' : tab
  if (!nodeId || !node) return null
  const parent = node.parent ? diagram?.nodes.find((n) => n.id === node.parent) : undefined
  const group = diagram?.groups.find((g) => g.id === node.group)
  const KindIcon = KIND_ICON[node.kind]
  const turnOver = turn.phase === 'done' || turn.phase === 'error'
  const canDisable = node.can_disable && canDisableNodes && mode !== undefined && mode !== 'public'

  return (
    <aside
      className="absolute inset-y-0 right-0 z-30 flex w-full max-w-[460px] flex-col border-l bg-background shadow-xl"
      aria-label={`Inspector: ${node.label}`}
      data-testid="inspector"
      onKeyDown={(e) => {
        if (e.key === 'Escape') selectNode(null)
      }}
      style={planeStyle(node.group)}
    >
      <header className="flex flex-col gap-2 border-b p-4" style={{ borderTop: '3px solid var(--plane-stroke)' }}>
        <div className="flex items-start gap-2">
          <div className="min-w-0">
            <h2 className="text-base font-semibold" data-testid="inspector-title">
              {node.label}
            </h2>
            <p className="text-xs text-muted-foreground">
              {group?.label}
              {parent ? ` · inside ${parent.label}` : ''} · <code className="font-mono">{node.id}</code>
            </p>
          </div>
          <span className="ml-auto flex shrink-0 items-center gap-1">
            <Kbd aria-hidden>Esc</Kbd>
            <Button ref={closeRef} size="icon" variant="ghost" className="size-8" onClick={() => selectNode(null)} aria-label="Close inspector" aria-keyshortcuts="Escape">
              <X />
            </Button>
          </span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          <Hint label={KIND_HINT[node.kind]}>
            <Badge variant="kind" data-kind={node.kind}>
              <KindIcon aria-hidden />
              {node.kind}
            </Badge>
          </Hint>
          <Badge variant="outline">{node.view} view</Badge>
          {node.implemented === false && <Badge variant="secondary">not yet built</Badge>}
          {node.stub && <Badge variant="secondary">stub</Badge>}
        </div>
        <p className="text-sm leading-relaxed" data-testid="inspector-lesson">
          {node.lesson}
        </p>
        {canDisable && (
          <label className="flex items-center gap-2 text-xs">
            <Switch checked={disabled.includes(node.id)} onCheckedChange={() => toggleDisabled(node.id)} aria-label={`Disable ${node.label} on the next turn`} />
            Disable on the next turn to see what breaks without it
          </label>
        )}
      </header>
      <Tabs value={activeTab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col gap-0">
        <TabsList className="mx-4 mt-3">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          {detailsLabel && <TabsTrigger value="details">{detailsLabel}</TabsTrigger>}
          <TabsTrigger value="code">Code</TabsTrigger>
          <TabsTrigger value="events">Events{events.length ? ` (${events.length})` : ''}</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="min-h-0">
          <ScrollArea className="h-full">
            <div className="p-4">
              <OverviewTab run={turn.nodes[node.id]} info={info.data} />
              {info.status === 'error' && <p className="mt-3 text-xs text-destructive">Could not load node details: {info.error}</p>}
            </div>
          </ScrollArea>
        </TabsContent>
        {detailsLabel && info.data && (
          <TabsContent value="details" className="min-h-0">
            <ScrollArea className="h-full">
              <div className="p-4">
                <DetailsTab info={info.data} />
              </div>
            </ScrollArea>
          </TabsContent>
        )}
        <TabsContent value="code" className="flex min-h-0 flex-col gap-2 p-4">
          {source.status === 'loading' && <p className="text-xs text-muted-foreground">Loading source…</p>}
          {source.status === 'error' && <p className="text-xs text-destructive">No source available: {source.error}</p>}
          {source.data && (
            <>
              <p className="text-xs text-muted-foreground">
                <code className="font-mono">{source.data.path}</code> · {source.data.line_count} lines · read-only
              </p>
              <CodeView
                code={source.data.source}
                language={source.data.language}
                symbols={source.data.symbols}
                label={`Source of ${source.data.path}`}
                className="min-h-[300px] flex-1"
              />
            </>
          )}
        </TabsContent>
        <TabsContent value="events" className="min-h-0">
          <ScrollArea className="h-full">
            <div className="p-4">
              <EventsTab events={events} nodeId={node.id} turnOver={turnOver} />
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </aside>
  )
}
