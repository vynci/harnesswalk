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
import { useDisplayTurn } from '@/state/hooks'
import { useApp } from '@/state/store'
import { FlowEdge } from './FlowEdge'
import { HarnessNode } from './HarnessNode'
import { computeLayout } from './layout'
import { PlaneNode } from './PlaneNode'
import { toFlow } from './flow'
import { TurnStatusBar } from './TurnStatusBar'
import { Legend } from './Legend'

const nodeTypes = { harness: HarnessNode, plane: PlaneNode }
// Room for the turn status bar above the diagram and the legend below it.
const FIT_PADDING = { top: '48px', right: '14px', bottom: '132px', left: '14px' } as const
const edgeTypes = { flow: FlowEdge }

function Flow() {
  const diagram = useApp((s) => s.diagram)!
  const view = useApp((s) => s.view)
  const collapsedList = useApp((s) => s.collapsed)
  const disabledList = useApp((s) => s.disabled)
  const theme = useApp((s) => s.theme)
  const selectNode = useApp((s) => s.selectNode)
  const toggleCollapsed = useApp((s) => s.toggleCollapsed)
  const showTelemetry = useApp((s) => s.showTelemetry)
  const setShowTelemetry = useApp((s) => s.setShowTelemetry)
  const [hovered, setHovered] = useState<string | null>(null)
  const turn = useDisplayTurn()
  const { fitView } = useReactFlow()

  const layout = useMemo(
    () => computeLayout(diagram, { view, collapsed: new Set(collapsedList) }),
    [diagram, view, collapsedList],
  )
  const disabled = useMemo(() => new Set(disabledList), [disabledList])
  const { nodes, edges } = useMemo(
    () => toFlow(diagram, layout, turn, { disabled, onToggle: toggleCollapsed, showOps: showTelemetry, hovered }),
    [diagram, layout, turn, disabled, toggleCollapsed, showTelemetry, hovered],
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
      <Legend key={view} view={view} showTelemetry={showTelemetry} onShowTelemetry={setShowTelemetry} />
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
