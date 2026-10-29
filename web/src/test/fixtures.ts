import { EXAMPLES, specDiagram, type ExampleName } from '@/mock/examples'
import { buildEdgeIndex } from '@/state/reducer'

export const diagram = specDiagram()
export const edgeIndex = buildEdgeIndex(diagram.edges)
export const examples = EXAMPLES
export const names = Object.keys(EXAMPLES) as ExampleName[]
