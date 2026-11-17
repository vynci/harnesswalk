import { useMemo } from 'react'
import type { HarnessProfile } from '@/api/types'
import { featureState, pickerModels, type FeatureName, type FeatureState, type PickerModel } from './features'
import { profileOf } from './profiles'
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

/**
 * Whether a feature's controls are live, and the tooltip when they are not (features.ts). Every
 * private-only control reads this one source, and renders disabled with the reason, never hidden.
 */
export function useFeature(name: FeatureName): FeatureState {
  const capabilities = useApp((s) => s.capabilities)
  return useMemo(() => featureState(capabilities, name), [capabilities, name])
}

/** The public model picker's entries, with the reader's key applied (empty before P6). */
export function usePickerModels(): PickerModel[] {
  const capabilities = useApp((s) => s.capabilities)
  const byoKey = useApp((s) => s.byoKey)
  return useMemo(() => pickerModels(capabilities, byoKey), [capabilities, byoKey])
}

/** 0.2.0: the harness profile the session's pack runs with, or null (an older API). */
export function useProfile(): HarnessProfile | null {
  const packs = useApp((s) => s.packs)
  const profiles = useApp((s) => s.profiles)
  const pack = useApp((s) => s.pack)
  return useMemo(() => profileOf(packs, profiles, pack), [packs, profiles, pack])
}
