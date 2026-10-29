import { memo } from 'react'
import type { Node, NodeProps } from '@xyflow/react'
import type { PlaneId } from '@/api/types'
import { planeStyle } from './planes'

export type PlaneFlowNode = Node<{ plane: PlaneId; label: string; description?: string }, 'plane'>

function PlaneNodeImpl({ data }: NodeProps<PlaneFlowNode>) {
  return (
    <div className="hw-group h-full w-full" style={planeStyle(data.plane)} title={data.description}>
      <div className="hw-plane-label px-3 pt-2 text-xs font-medium">
        {data.label}
      </div>
    </div>
  )
}

export const PlaneNode = memo(PlaneNodeImpl)
