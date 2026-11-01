import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, X } from 'lucide-react'
import { Hint } from '@/components/hint'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Kbd } from '@/components/ui/kbd'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { opsRole, type OpsRole } from '@/diagram/opsRoles'
import { KIND_HINT, KIND_ICON, planeStyle } from '@/diagram/planes'
import { inspectorArtifact } from '@/state/ops'
import { useDisplayTurn } from '@/state/hooks'
import { useApp } from '@/state/store'
import { CodeView } from './CodeView'
import { DetailsTab } from './DetailsTab'
import { detailsTabLabel } from './labels'
import { EventsTab } from './EventsTab'
import { OverviewTab } from './OverviewTab'
import { useNodeData } from './useNodeData'
import { ArtifactEditor } from './ArtifactEditor'
import { useArtifacts } from './useOps'
import { LessonTab, type CodeTarget } from '@/lessons/LessonTab'
import { LESSONS_TAB, useLessonSource } from '@/lessons/useLessons'

// The LLM Ops panels, each its own chunk, picked by the node's role in the diagram.
const EvalPanel = lazy(() => import('./EvalPanel').then((m) => ({ default: m.EvalPanel })))
const ReleasePanel = lazy(() => import('./ReleasePanel').then((m) => ({ default: m.ReleasePanel })))
const TracePanel = lazy(() => import('./TracePanel').then((m) => ({ default: m.TracePanel })))

const OPS_TAB: Record<OpsRole, string> = { evals: 'Runs', releases: 'Releases', traces: 'Turns' }

function OpsPanel({ role }: { role: OpsRole }) {
  return (
    <Suspense fallback={<p className="text-xs text-muted-foreground">Loading…</p>}>
      {role === 'evals' ? <EvalPanel /> : role === 'releases' ? <ReleasePanel /> : <TracePanel />}
    </Suspense>
  )
}

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
  const { artifacts } = useArtifacts()
  // The tab the reader picked (kept in the store, so the lessons drawer can open a tab); a node
  // with an LLM Ops panel opens on it.
  const pick = useApp((s) => s.inspectorTab)
  const setInspectorTab = useApp((s) => s.setInspectorTab)
  // A code walk link: the node's own file at a line, or another file the chapter links to.
  const [jump, setJump] = useState<{ node: string; line: number | null; key: number } | null>(null)
  const [peek, setPeek] = useState<{ node: string; target: CodeTarget; key: number } | null>(null)
  const jumps = useRef(0)
  const lessonPeek = peek && peek.node === nodeId ? peek : null
  const peekSource = useLessonSource(lessonPeek?.target.slug ?? null, lessonPeek?.target.path ?? null)
  const role = opsRole(diagram, nodeId)
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

  const detailsLabel = info.data ? detailsTabLabel(info.data.inspector) : null
  // A YAML artifact behind a node whose inspector is not the config itself (the context
  // builder, the model layer) gets a Config tab of its own.
  const yamlArtifact =
    info.data && info.data.inspector.type !== 'config' && info.data.inspector.type !== 'prompt' ? inspectorArtifact(info.data.inspector.artifact, artifacts) : undefined
  const tab = pick.node === nodeId ? pick.tab : role ? 'ops' : pick.tab === 'ops' || pick.tab === 'config' ? 'overview' : pick.tab
  const activeTab =
    (tab === 'details' && !detailsLabel) || (tab === 'ops' && !role) || (tab === 'config' && !yamlArtifact) ? 'overview' : tab
  const setTab = (t: string) => setInspectorTab(nodeId, t)
  const onCodeLink = useCallback(
    (target: CodeTarget) => {
      if (!nodeId) return
      const key = ++jumps.current
      if (target.node_id === nodeId) {
        setPeek(null)
        setJump({ node: nodeId, line: target.line, key })
      } else {
        setPeek({ node: nodeId, target, key })
      }
      setInspectorTab(nodeId, 'code')
    },
    [nodeId, setInspectorTab],
  )
  if (!nodeId || !node) return null
  const ownJump = jump && jump.node === nodeId ? jump : null
  const parent = node.parent ? diagram?.nodes.find((n) => n.id === node.parent) : undefined
  const group = diagram?.groups.find((g) => g.id === node.group)
  const KindIcon = KIND_ICON[node.kind]
  const turnOver = turn.phase === 'done' || turn.phase === 'error'
  const canDisable = node.can_disable && canDisableNodes && mode !== undefined && mode !== 'public'

  return (
    <aside
      className="flex h-full w-full min-w-0 flex-col bg-background"
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
            <h2 className="text-base font-medium" data-testid="inspector-title">
              {node.label}
            </h2>
            <p className="text-xs text-muted-foreground">
              {group?.label}
              {parent ? ` · inside ${parent.label}` : ''} · <code className="font-mono">{node.id}</code>
            </p>
          </div>
          <span className="ml-auto flex shrink-0 items-center gap-1">
            <Kbd aria-hidden>Esc</Kbd>
            <Button ref={closeRef} size="icon-sm" variant="ghost" onClick={() => selectNode(null)} aria-label="Close inspector" aria-keyshortcuts="Escape">
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
          {node.lesson_url && activeTab !== LESSONS_TAB && (
            <>
              {' '}
              <Button type="button" variant="link" size="xs" className="h-auto p-0 text-muted-foreground hover:text-foreground" onClick={() => setTab(LESSONS_TAB)} data-testid="read-chapter">
                Read the chapter
              </Button>
            </>
          )}
        </p>
        {canDisable && (
          <label className="flex items-center gap-2 text-xs">
            <Switch checked={disabled.includes(node.id)} onCheckedChange={() => toggleDisabled(node.id)} aria-label={`Disable ${node.label} on the next turn`} />
            Disable on the next turn to see what breaks without it
          </label>
        )}
      </header>
      <Tabs value={activeTab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col gap-0">
        <TabsList className="no-scrollbar mx-4 mt-3 max-w-[calc(100%-2rem)] justify-start overflow-x-auto">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value={LESSONS_TAB}>Lessons</TabsTrigger>
          {role && <TabsTrigger value="ops">{OPS_TAB[role]}</TabsTrigger>}
          {detailsLabel && <TabsTrigger value="details">{detailsLabel}</TabsTrigger>}
          {yamlArtifact && <TabsTrigger value="config">Config</TabsTrigger>}
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
        <TabsContent value={LESSONS_TAB} className="min-h-0">
          <ScrollArea className="h-full">
            <div className="p-4">
              <LessonTab node={node} onCodeLink={onCodeLink} />
            </div>
          </ScrollArea>
        </TabsContent>
        {detailsLabel && info.data && (
          <TabsContent value="details" className="min-h-0">
            <ScrollArea className="h-full">
              <div className="p-4">
                <DetailsTab info={info.data} artifacts={artifacts} />
              </div>
            </ScrollArea>
          </TabsContent>
        )}
        {role && (
          <TabsContent value="ops" className="min-h-0">
            <ScrollArea className="h-full">
              <div className="p-4">
                <OpsPanel role={role} />
              </div>
            </ScrollArea>
          </TabsContent>
        )}
        {yamlArtifact && (
          <TabsContent value="config" className="min-h-0">
            <ScrollArea className="h-full">
              <div className="flex flex-col gap-2 p-4">
                <p className="text-xs text-muted-foreground">
                  {node.artifact_hint ? <code className="font-mono">{node.artifact_hint}</code> : null} · the working set, shipped by a release
                </p>
                <ArtifactEditor artifact={yamlArtifact} language="yaml" />
              </div>
            </ScrollArea>
          </TabsContent>
        )}
        <TabsContent value="code" className="flex min-h-0 flex-col gap-2 p-4">
          {lessonPeek ? (
            <>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <Button type="button" variant="ghost" size="xs" className="-ml-2 text-muted-foreground" onClick={() => setPeek(null)} data-testid="code-back">
                  <ArrowLeft aria-hidden />
                  Back to {source.data?.path ?? node.source_hint}
                </Button>
              </div>
              {peekSource.status === 'loading' && <p className="text-xs text-muted-foreground">Loading source…</p>}
              {(peekSource.status === 'error' || peekSource.status === 'missing') && (
                <p className="text-xs text-destructive">No source available for {lessonPeek.target.path}{peekSource.error ? `: ${peekSource.error}` : ''}</p>
              )}
              {peekSource.data && (
                <>
                  <p className="text-xs text-muted-foreground" data-testid="code-peek-path">
                    <code className="font-mono">{peekSource.data.path}</code> · from chapter {lessonPeek.target.slug.slice(0, 2)}
                    {lessonPeek.target.line ? ` · line ${lessonPeek.target.line}` : ''} · {peekSource.data.line_count} lines · read-only
                    {peekSource.data.truncated ? ' · cut short' : ''}
                  </p>
                  <CodeView
                    code={peekSource.data.source}
                    language={peekSource.data.language}
                    symbols={peekSource.data.symbols}
                    label={`Source of ${peekSource.data.path}`}
                    className="min-h-[300px] flex-1"
                    jumpTo={{ line: lessonPeek.target.line, key: lessonPeek.key }}
                  />
                </>
              )}
            </>
          ) : null}
          {!lessonPeek && source.status === 'loading' && <p className="text-xs text-muted-foreground">Loading source…</p>}
          {!lessonPeek && source.status === 'error' && <p className="text-xs text-destructive">No source available: {source.error}</p>}
          {!lessonPeek && source.data && (
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
                jumpTo={ownJump}
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
