import { useEffect, useState } from 'react'
import type { NodeInfo, NodeSource } from '@/api/types'
import { useApp } from '@/state/store'

export type Loadable<T> = { status: 'idle' | 'loading' | 'ok' | 'error'; data?: T; error?: string }

interface Keyed<T> {
  key: string
  value: Loadable<T>
}

const sourceCache = new Map<string, NodeSource>()

/** GET /v1/nodes/{id} (refetched whenever the live turn changes phase) and GET /v1/nodes/{id}/source (cached). */
export function useNodeData(nodeId: string | null): { info: Loadable<NodeInfo>; source: Loadable<NodeSource> } {
  const api = useApp((s) => s.api)
  const refreshKey = useApp((s) => {
    const id = s.liveTurnId
    const t = id ? s.turns[id]?.state : undefined
    return `${id ?? ''}:${t?.phase ?? ''}`
  })
  const infoKey = `${nodeId ?? ''}|${refreshKey}`
  const [info, setInfo] = useState<Keyed<NodeInfo> | null>(null)
  const [source, setSource] = useState<Keyed<NodeSource> | null>(null)

  useEffect(() => {
    if (!api || !nodeId) return
    let cancelled = false
    api.node(nodeId).then(
      (data) => !cancelled && setInfo({ key: infoKey, value: { status: 'ok', data } }),
      (err: unknown) =>
        !cancelled && setInfo({ key: infoKey, value: { status: 'error', error: err instanceof Error ? err.message : String(err) } }),
    )
    return () => {
      cancelled = true
    }
  }, [api, nodeId, infoKey])

  const cached = nodeId ? sourceCache.get(nodeId) : undefined
  useEffect(() => {
    if (!api || !nodeId || sourceCache.has(nodeId)) return
    let cancelled = false
    api.nodeSource(nodeId).then(
      (data) => {
        sourceCache.set(nodeId, data)
        if (!cancelled) setSource({ key: nodeId, value: { status: 'ok', data } })
      },
      (err: unknown) =>
        !cancelled && setSource({ key: nodeId, value: { status: 'error', error: err instanceof Error ? err.message : String(err) } }),
    )
    return () => {
      cancelled = true
    }
  }, [api, nodeId])

  // A refetch keeps showing the previous data for the same node until the new answer lands.
  const infoValue: Loadable<NodeInfo> = !nodeId
    ? { status: 'idle' }
    : info?.key === infoKey
      ? info.value
      : info?.value.data?.id === nodeId
        ? info.value
        : { status: 'loading' }
  const sourceValue: Loadable<NodeSource> = !nodeId
    ? { status: 'idle' }
    : cached
      ? { status: 'ok', data: cached }
      : source?.key === nodeId
        ? source.value
        : { status: 'loading' }
  return { info: infoValue, source: sourceValue }
}
