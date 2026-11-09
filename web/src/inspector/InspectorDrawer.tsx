import { lazy, Suspense, useEffect, useMemo, useRef } from 'react'
import { ArrowLeft, BookOpen, X } from 'lucide-react'
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
import { useDisplayTurn, useFeature } from '@/state/hooks'
import { useApp } from '@/state/store'
import { CodeView } from './CodeView'
import { DetailsTab } from './DetailsTab'
import { detailsTabLabel } from './labels'
import { EventsTab } from './EventsTab'
import { OverviewTab } from './OverviewTab'
import { payloadEvent } from './payload'
import { PayloadTab } from './PayloadTab'
import { useNodeData } from './useNodeData'
import { ArtifactEditor } from './ArtifactEditor'
import { useArtifacts } from './useOps'
import { PackFiles } from './PackFiles'
import { usePackDetail } from './usePackDetail'
import { filesForNode } from '@/state/packs'
import { lessonFocusPending, openNodeChapter, useLessonSource } from '@/lessons/useLessons'
import { usePanels } from '@/state/panels'
import { tuneNodeId, tunePart } from './tuneView'
import { useTuneUi } from './tuneUi'
import { OpsEvents } from './OpsEvents'

// The LLM Ops panels, each its own chunk, picked by the node's role in the diagram.
const EvalPanel = lazy(() => import('./EvalPanel').then((m) => ({ default: m.EvalPanel })))
const ReleaseArea = lazy(() => import('./ReleasePanel').then((m) => ({ default: m.ReleaseArea })))
const TracePanel = lazy(() => import('./TracePanel').then((m) => ({ default: m.TracePanel })))

const OPS_TAB: Record<OpsRole, string> = { evals: 'Runs', releases: 'Releases', traces: 'Turns' }

/** The LLM Ops plane: its nodes show the latest ops events heard on the tenant's stream. */
const OPS_GROUP = 'ops'

/** The inspector's tabs; any other value (a stale pick) falls back to Overview. */
const TABS = new Set(['overview', 'payload', 'ops', 'details', 'config', 'files', 'code', 'events'])

/**
 * 0.2.0: the curate node (it writes datasets, `artifact_hint`) keeps the Eval panel and points to
 * the Tune tab, where its datasets are, on the node that writes models.
 */
function CurateLink() {
  const diagram = useApp((s) => s.diagram)
  const setView = useApp((s) => s.setView)
  const openInspector = useApp((s) => s.openInspector)
  const setSection = useTuneUi((s) => s.setSection)
  const target = tuneNodeId(diagram)
  const label = diagram?.nodes.find((n) => n.id === target)?.label ?? 'Tune'
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 rounded-md bg-muted px-3 py-2 text-xs" data-testid="curate-link">
      <p className="min-w-0 flex-1">
        This node turns teacher traffic into datasets (<code className="font-mono">dataset/&lt;name&gt;</code>): golden cases and rewrites run through the whole harness as model-suite eval runs, and only the turns that pass are kept. The datasets, the curations and what
        trains on them are in the Tune tab.
      </p>
      {target && (
        <Button
          size="xs"
          variant="outline"
          onClick={() => {
            setSection('datasets')
            setView('expert')
            openInspector(target, 'ops')
          }}
          data-testid="curate-open-tune"
        >
          Open the datasets in {label}
        </Button>
      )}
    </div>
  )
}

function OpsPanel({ role, nodeId, part }: { role: OpsRole; nodeId: string; part: ReturnType<typeof tunePart> }) {
  return (
    <Suspense fallback={<p className="text-xs text-muted-foreground">Loading…</p>}>
      {part === 'curates' && <CurateLink />}
      {role === 'evals' ? <EvalPanel /> : role === 'releases' ? <ReleaseArea key={nodeId} tuneFirst={part === 'trains'} /> : <TracePanel />}
    </Suspense>
  )
}

/**
 * The node inspector. `compact`: it shares its column with the Guide (stacked), so the node's
 * summary is cut to two lines (the chapter beside it says the rest) and the code view may be short.
 */
export function InspectorDrawer({ compact = false }: { compact?: boolean }) {
  const nodeId = useApp((s) => s.selectedNode)
  const diagram = useApp((s) => s.diagram)
  const selectNode = useApp((s) => s.selectNode)
  const disableNodes = useFeature('disable_nodes')
  const disabled = useApp((s) => s.disabled)
  const toggleDisabled = useApp((s) => s.toggleDisabled)
  const turn = useDisplayTurn()
  const { info, source } = useNodeData(nodeId)
  const { artifacts } = useArtifacts()
  // P5: the session pack's files this node reads (by their role), for the Files tab.
  const { detail: packDetail } = usePackDetail()
  const packFiles = useMemo(() => (packDetail && nodeId ? filesForNode(packDetail.files, nodeId) : []), [packDetail, nodeId])
  // The tab the reader picked (kept in the store, so the Guide can open the Code tab); a node
  // with an LLM Ops panel opens on it.
  const pick = useApp((s) => s.inspectorTab)
  const setInspectorTab = useApp((s) => s.setInspectorTab)
  // A code walk link from the Guide: the node's own file at a line, or another file the chapter
  // links to (a peek, with a way back).
  const codeLink = usePanels((s) => s.codeLink)
  const guideHere = usePanels((s) => s.guideOpen && s.guideNode === nodeId)
  const clearCode = usePanels((s) => s.clearCode)
  const link = codeLink && codeLink.node === nodeId ? codeLink : null
  const lessonPeek = link && link.target.node_id !== nodeId ? link : null
  const ownJump = link && link.target.node_id === nodeId ? { line: link.target.line, key: link.key } : null
  const peekSource = useLessonSource(lessonPeek?.target.slug ?? null, lessonPeek?.target.path ?? null)
  const role = opsRole(diagram, nodeId)
  const closeRef = useRef<HTMLButtonElement>(null)

  const node = diagram?.nodes.find((n) => n.id === nodeId)
  // 0.2.0: a node that writes tune artifacts (its artifact_hint): the Tune tab or the link to it.
  const part = tunePart(node)
  const childIds = useMemo(() => new Set(diagram?.nodes.filter((n) => n.parent === nodeId).map((n) => n.id) ?? []), [diagram, nodeId])
  // The node and its children, for the latest ops events (tune runs, curations, gates).
  const opsIds = useMemo(() => new Set(nodeId ? [nodeId, ...childIds] : []), [nodeId, childIds])
  const events = useMemo(
    () => turn.events.filter((e) => e.node_id === nodeId || (e.node_id && childIds.has(e.node_id))),
    [turn.events, nodeId, childIds],
  )
  // What the node's latest hop carried (tracer.md "Payloads"); a step-mode pause opens on it.
  const payloadEv = useMemo(() => payloadEvent(turn.events, nodeId), [turn.events, nodeId])
  const pausedHere = turn.phase === 'paused' && turn.paused?.reason === 'breakpoint' && turn.paused.nodeId === nodeId

  // A node picked on the canvas (or nothing focused) moves focus here; one the Guide opened, or a
  // Try button that filled the composer, leaves focus where it is.
  useEffect(() => {
    if (!nodeId || lessonFocusPending()) return
    const active = document.activeElement
    if (!active || active === document.body || active.closest('.react-flow, [data-testid=inspector]')) closeRef.current?.focus()
  }, [nodeId])

  const detailsLabel = info.data ? detailsTabLabel(info.data.inspector) : null
  // A YAML artifact behind a node whose inspector is not the config itself (the context
  // builder, the model layer) gets a Config tab of its own.
  const yamlArtifact =
    info.data && info.data.inspector.type !== 'config' && info.data.inspector.type !== 'prompt' ? inspectorArtifact(info.data.inspector.artifact, artifacts) : undefined
  const tab =
    pick.node === nodeId ? pick.tab : role ? 'ops' : pausedHere && payloadEv ? 'payload' : pick.tab === 'ops' || pick.tab === 'config' ? 'overview' : pick.tab
  const activeTab =
    !TABS.has(tab) || (tab === 'payload' && !payloadEv) || (tab === 'details' && !detailsLabel) || (tab === 'ops' && !role) || (tab === 'config' && !yamlArtifact) || (tab === 'files' && packFiles.length === 0)
      ? 'overview'
      : tab
  const setTab = (t: string) => setInspectorTab(nodeId, t)
  if (!nodeId || !node) return null
  const parent = node.parent ? diagram?.nodes.find((n) => n.id === node.parent) : undefined
  const group = diagram?.groups.find((g) => g.id === node.group)
  const KindIcon = KIND_ICON[node.kind]
  const turnOver = turn.phase === 'done' || turn.phase === 'error'

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
        <p className={`text-sm leading-relaxed ${compact ? 'line-clamp-2' : ''}`} title={compact ? node.lesson : undefined} data-testid="inspector-lesson">
          {node.lesson}
          {node.lesson_url && !guideHere && (
            <>
              {' '}
              <Button
                type="button"
                variant="link"
                size="xs"
                className="h-auto p-0 text-muted-foreground hover:text-foreground"
                onClick={() => openNodeChapter(node.id)}
                data-testid="open-in-guide"
              >
                <BookOpen aria-hidden />
                Open in Guide
              </Button>
            </>
          )}
        </p>
        {node.can_disable && disableNodes.enabled && (
          <label className="flex items-center gap-2 text-xs">
            <Switch checked={disabled.includes(node.id)} onCheckedChange={() => toggleDisabled(node.id)} aria-label={`Disable ${node.label} on the next turn`} />
            Disable on the next turn to see what breaks without it
          </label>
        )}
        {/* Private-only in public mode: the switch stays, off and disabled, with the reason. */}
        {node.can_disable && !disableNodes.enabled && (
          <Hint label={disableNodes.reason}>
            <span className="flex w-fit items-center gap-2 text-xs text-muted-foreground" tabIndex={0} data-testid="disable-node-off" aria-label={`Disable ${node.label} on the next turn: ${disableNodes.reason}`}>
              <Switch checked={false} disabled aria-hidden tabIndex={-1} />
              Disable on the next turn to see what breaks without it
            </span>
          </Hint>
        )}
      </header>
      <Tabs value={activeTab} onValueChange={setTab} className="flex min-h-0 flex-1 flex-col gap-0">
        <TabsList className="no-scrollbar mx-4 mt-3 max-w-[calc(100%-2rem)] justify-start overflow-x-auto">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          {payloadEv && (
            <TabsTrigger value="payload" data-testid="payload-tab">
              Payload
            </TabsTrigger>
          )}
          {role && <TabsTrigger value="ops">{part === 'trains' ? 'Tune' : OPS_TAB[role]}</TabsTrigger>}
          {detailsLabel && <TabsTrigger value="details">{detailsLabel}</TabsTrigger>}
          {yamlArtifact && <TabsTrigger value="config">Config</TabsTrigger>}
          {packFiles.length > 0 && <TabsTrigger value="files">Files</TabsTrigger>}
          <TabsTrigger value="code">Code</TabsTrigger>
          <TabsTrigger value="events">Events{events.length ? ` (${events.length})` : ''}</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="min-h-0">
          <ScrollArea className="h-full">
            <div className="p-4">
              <OverviewTab run={turn.nodes[node.id]} info={info.data} />
              {node.group === OPS_GROUP && <OpsEvents ids={opsIds} />}
              {info.status === 'error' && <p className="mt-3 text-xs text-destructive">Could not load node details: {info.error}</p>}
            </div>
          </ScrollArea>
        </TabsContent>
        {payloadEv && (
          <TabsContent value="payload" className="min-h-0">
            <ScrollArea className="h-full">
              <div className="p-4">
                <PayloadTab event={payloadEv} events={turn.events} turnOver={turnOver} />
              </div>
            </ScrollArea>
          </TabsContent>
        )}
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
                <OpsPanel role={role} nodeId={node.id} part={part} />
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
        {packFiles.length > 0 && (
          <TabsContent value="files" className="min-h-0">
            <ScrollArea className="h-full">
              <div className="p-4">
                <PackFiles key={`${node.id}:${packDetail?.id ?? ''}`} files={packFiles} nodeLabel={node.label} />
              </div>
            </ScrollArea>
          </TabsContent>
        )}
        <TabsContent value="code" className="flex min-h-0 flex-col gap-2 p-4">
          {lessonPeek ? (
            <>
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <Button type="button" variant="ghost" size="xs" className="-ml-2 text-muted-foreground" onClick={clearCode} data-testid="code-back">
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
                    className={compact ? 'min-h-24 flex-1' : 'min-h-[300px] flex-1'}
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
                className={compact ? 'min-h-24 flex-1' : 'min-h-[300px] flex-1'}
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
