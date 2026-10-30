// Data hooks for the LLM Ops inspector panels. Each refetches when the store bumps
// `inspectorVersion` (after a save, a release, a rollback or a finished eval run).
import { useEffect, useState } from 'react'
import type { ArtifactRef } from '@/api/types'
import { useApp } from '@/state/store'
import type { Loadable } from './useNodeData'

/** Run `load` whenever `key` changes; keeps the last data while the next answer is on its way. */
export function useLoad<T>(key: string | null, load: () => Promise<T>): Loadable<T> & { reload: () => void } {
  const [state, setState] = useState<{ key: string; value: Loadable<T> } | null>(null)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (key === null) return
    let cancelled = false
    load().then(
      (data) => !cancelled && setState({ key, value: { status: 'ok', data } }),
      (err: unknown) => !cancelled && setState({ key, value: { status: 'error', error: err instanceof Error ? err.message : String(err) } }),
    )
    return () => {
      cancelled = true
    }
    // `load` is a fresh closure every render; the key says when to call it again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick])
  const value: Loadable<T> = key === null ? { status: 'idle' } : state ? (state.key === key ? state.value : { ...state.value, status: 'loading' }) : { status: 'loading' }
  return { ...value, reload: () => setTick((t) => t + 1) }
}

/** The working set (GET /v1/artifacts), when the API versions artifacts. */
export function useArtifacts(): { pack: string | null; artifacts: ArtifactRef[] } {
  const api = useApp((s) => s.api)
  const on = useApp((s) => !!(s.capabilities?.features.artifact_edit || s.capabilities?.features.release))
  const version = useApp((s) => s.inspectorVersion)
  const out = useLoad(api && on ? `artifacts:${version}` : null, () => api!.artifacts())
  return { pack: out.data?.pack ?? null, artifacts: out.data?.artifacts ?? [] }
}
