// Which LLM Ops panel a node gets (eval runs, releases, the trace query), read from the shape of
// the diagram, never from node ids (the drift rule in web/README.md):
//
// - a release edge into another plane leaves the node that ships artifacts: the releases panel;
// - a release edge to a node inside the ops plane leaves the node that gates releases: eval runs;
// - any other ops store keeps the turns: the trace query;
// - an expert child without a role of its own takes its parent's.
import type { Diagram, DiagramNode } from '@/api/types'

export type OpsRole = 'evals' | 'releases' | 'traces'

const OPS_PLANE = 'ops'

function directRole(diagram: Diagram, node: DiagramNode, byId: Map<string, DiagramNode>): OpsRole | null {
  if (node.group !== OPS_PLANE) return null
  const out = diagram.edges.filter((e) => e.kind === 'release' && e.from === node.id)
  if (out.some((e) => byId.get(e.to)?.group !== OPS_PLANE)) return 'releases'
  if (out.length > 0) return 'evals'
  if (node.kind === 'store') return 'traces'
  return null
}

/** The ops role of a node, its own or its parent's; null outside the ops plane. */
export function opsRole(diagram: Diagram | null | undefined, nodeId: string | null | undefined): OpsRole | null {
  if (!diagram || !nodeId) return null
  const byId = new Map(diagram.nodes.map((n) => [n.id, n]))
  const node = byId.get(nodeId)
  if (!node) return null
  const own = directRole(diagram, node, byId)
  if (own) return own
  const parent = node.parent ? byId.get(node.parent) : undefined
  return parent ? directRole(diagram, parent, byId) : null
}

/** The node that carries a role's badge: the top-level node with that role of its own. */
export function opsRoleNode(diagram: Diagram | null | undefined, role: OpsRole): string | null {
  if (!diagram) return null
  const byId = new Map(diagram.nodes.map((n) => [n.id, n]))
  const top = diagram.nodes.find((n) => !n.parent && directRole(diagram, n, byId) === role)
  return top?.id ?? null
}
