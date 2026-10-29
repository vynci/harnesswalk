import { useMemo } from 'react'
import { initialTurnState, reduceEvents, type TurnState } from './reducer'
import { selectDisplayTurnId, useApp } from './store'

const EMPTY = initialTurnState()

/** The reduced state the diagram and the inspector show right now. */
export function useDisplayTurn(): TurnState {
  const replay = useApp((s) => s.replay)
  const edgeIndex = useApp((s) => s.edgeIndex)
  const turnId = useApp(selectDisplayTurnId)
  const liveState = useApp((s) => (turnId ? s.turns[turnId]?.state : undefined))
  return useMemo(() => {
    if (replay) return reduceEvents(replay.events.slice(0, replay.cursor + 1), edgeIndex)
    return liveState ?? EMPTY
  }, [replay, edgeIndex, liveState])
}
