import { describe, expect, it } from 'vitest'
import { diagram } from '@/test/fixtures'
import { opsRole, opsRoleNode } from './opsRoles'

// The ops panels are picked by the diagram's shape (release edges and the ops plane), so the
// UI names no node id. These are the spec's nodes as they are today.
describe('ops roles from the diagram', () => {
  it('gives each LLM Ops node its panel and nothing outside the ops plane', () => {
    const roles = Object.fromEntries(diagram.nodes.map((n) => [n.id, opsRole(diagram, n.id)]))
    const ops = diagram.nodes.filter((n) => n.group === 'ops').map((n) => n.id)
    for (const n of diagram.nodes) if (!ops.includes(n.id)) expect(roles[n.id], n.id).toBeNull()
    expect(Object.fromEntries(ops.map((id) => [id, roles[id]]))).toEqual({
      trace: 'traces',
      cost: 'traces',
      eval: 'evals',
      online_eval: 'evals',
      offline_eval: 'evals',
      diagnose: 'evals',
      release: 'releases',
      artifacts: 'releases',
    })
  })

  it('puts a role badge on the top-level node only', () => {
    expect(opsRoleNode(diagram, 'evals')).toBe('eval')
    expect(opsRoleNode(diagram, 'releases')).toBe('release')
    expect(opsRoleNode(diagram, 'traces')).toBe('trace')
    expect(opsRole(null, 'eval')).toBeNull()
  })
})
