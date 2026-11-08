import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type NodeMouseHandler,
} from '@xyflow/react'
import { useDisplayTurn, useProfile } from '@/state/hooks'
import { offSet } from '@/state/profiles'
import { useApp } from '@/state/store'
import { FlowEdge } from './FlowEdge'
import { HarnessNode } from './HarnessNode'
import { computeLayout } from './layout'
import { opsRoleNode } from './opsRoles'
import { PlaneNode } from './PlaneNode'
import { toFlow, type OpsChip } from './flow'
import { TurnStatusBar } from './TurnStatusBar'
import { CanvasControls } from './CanvasControls'

const nodeTypes = { harness: HarnessNode, plane: PlaneNode }
// Room for the turn status bar above the diagram and the canvas controls below it.
const FIT_PADDING = { top: '48px', right: '14px', bottom: '84px', left: '14px' } as const
const edgeTypes = { flow: FlowEdge }

function Flow() {
  const diagram = useApp((s) => s.diagram)!
  const view = useApp((s) => s.view)
  const collapsedList = useApp((s) => s.collapsed)
  const disabledList = useApp((s) => s.disabled)
  const theme = useApp((s) => s.theme)
  const selectNode = useApp((s) => s.selectNode)
  const toggleCollapsed = useApp((s) => s.toggleCollapsed)
  const [hovered, setHovered] = useState<string | null>(null)
  const latestEval = useApp((s) => s.latestEval)
  // The node that gates releases carries the regressions of the latest eval run.
  const alerts = useMemo(() => {
    const id = opsRoleNode(diagram, 'evals')
    const n = latestEval?.regressions ?? 0
    if (!id || n <= 0) return undefined
    const text = `${n} regression${n === 1 ? '' : 's'}`
    return { [id]: { count: n, text, title: `${text} in the latest ${latestEval!.suite} run (${latestEval!.run_id})` } }
  }, [diagram, latestEval])
  // Neutral chips from the tenant's ops stream: an eval run in progress on the node that runs
  // them, unreleased edits on the node that ships releases. Both found by diagram shape.
  const opsRun = useApp((s) => s.opsRun)
  const liveEval = useApp((s) => s.liveEval)
  const dirty = useApp((s) => s.capabilities?.release?.working_set_dirty === true)
  const opsChips = useMemo(() => {
    const out: Record<string, OpsChip> = {}
    const evalNode = opsRoleNode(diagram, 'evals')
    const run = liveEval ?? opsRun
    if (evalNode && run) {
      const of = run.total ? `${run.done}/${run.total}` : `${run.done}`
      out[evalNode] = { text: of, title: `Eval run in progress: ${run.done}${run.total ? ` of ${run.total}` : ''} cases done`, running: true }
    }
    const releaseNode = opsRoleNode(diagram, 'releases')
    if (releaseNode && dirty) out[releaseNode] = { text: 'unreleased', title: 'The working set has unreleased changes' }
    return out
  }, [diagram, opsRun, liveEval, dirty])
  const pulse = useApp((s) => s.opsPulse?.nodeId ?? null)
  const turn = useDisplayTurn()
  const { fitView } = useReactFlow()

  const layout = useMemo(
    () => computeLayout(diagram, { view, collapsed: new Set(collapsedList) }),
    [diagram, view, collapsedList],
  )
  const disabled = useMemo(() => new Set(disabledList), [disabledList])
  // 0.2.0: the session pack's profile dims what it switches off (same layout, every node drawn).
  const profile = useProfile()
  const profileOff = useMemo(() => offSet(profile), [profile])
  const profileId = profile?.id ?? null
  const { nodes, edges } = useMemo(
    () => toFlow(diagram, layout, turn, { disabled, profileOff, profileId, onToggle: toggleCollapsed, showOps: true, hovered, alerts, opsChips, pulse }),
    [diagram, layout, turn, disabled, profileOff, profileId, toggleCollapsed, hovered, alerts, opsChips, pulse],
  )

  useEffect(() => {
    const id = requestAnimationFrame(() => void fitView({ padding: FIT_PADDING, duration: 250 }))
    return () => cancelAnimationFrame(id)
  }, [layout, fitView])

  // Refit when the panel is resized (split drag, window resize, inspector opening).
  const container = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = container.current
    if (!el || typeof ResizeObserver === 'undefined') return
    let frame = 0
    const ro = new ResizeObserver(() => {
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => void fitView({ padding: FIT_PADDING }))
    })
    ro.observe(el)
    return () => {
      cancelAnimationFrame(frame)
      ro.disconnect()
    }
  }, [fitView])

  const onNodeEnter: NodeMouseHandler = useCallback((_e, node) => setHovered(node.type === 'harness' ? node.id : null), [])
  const onNodeLeave: NodeMouseHandler = useCallback(() => setHovered(null), [])

  const onNodeClick: NodeMouseHandler = useCallback(
    (_e, node) => {
      if (node.type === 'harness') selectNode(node.id)
    },
    [selectNode],
  )

  const colorMode = theme === 'system' ? 'system' : theme

  return (
    <div
      ref={container}
      className="relative h-full w-full"
      onKeyDown={(e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return
        const el = (e.target as HTMLElement).closest<HTMLElement>('.react-flow__node-harness')
        if (el?.dataset.id) {
          e.preventDefault()
          selectNode(el.dataset.id)
        }
      }}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={onNodeClick}
        onNodeMouseEnter={onNodeEnter}
        onNodeMouseLeave={onNodeLeave}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        fitView
        fitViewOptions={{ padding: FIT_PADDING }}
        minZoom={0.2}
        maxZoom={2}
        colorMode={colorMode}
        proOptions={{ hideAttribution: false }}
        aria-label="Harness diagram"
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
        <Controls showInteractive={false} position="bottom-left" />
      </ReactFlow>
      <TurnStatusBar turn={turn} />
      <CanvasControls />
    </div>
  )
}

export function DiagramPanel() {
  return (
    <ReactFlowProvider>
      <Flow />
    </ReactFlowProvider>
  )
}
