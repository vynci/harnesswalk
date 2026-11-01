import { useEffect, useState } from 'react'
import type { HarnessEvent, Payload } from '@/api/types'
import { useApp } from '@/state/store'
import { payloadOf } from './payload'
import type { Loadable } from './useNodeData'

// A stored detail never changes, so one fetch per event is enough (kept for the session).
const cache = new Map<string, Payload | null>()
const MAX_CACHED = 200

function remember(key: string, value: Payload | null) {
  cache.set(key, value)
  if (cache.size > MAX_CACHED) cache.delete(cache.keys().next().value!)
}

/** The payload behind one event's detail_ref (GET /v1/trace/turns/{turn}/details/{event}). */
export function usePayload(event: HarnessEvent | null): Loadable<Payload | null> {
  const api = useApp((s) => s.api)
  const key = event ? `${event.turn_id}/${event.event_id}` : null
  const [state, setState] = useState<{ key: string; value: Loadable<Payload | null> } | null>(null)

  useEffect(() => {
    if (!api || !event || !key || cache.has(key)) return
    let cancelled = false
    api.detail(event.turn_id, event.event_id).then(
      (res) => {
        const payload = payloadOf(res.detail)
        remember(key, payload)
        if (!cancelled) setState({ key, value: { status: 'ok', data: payload } })
      },
      (err: unknown) => !cancelled && setState({ key, value: { status: 'error', error: err instanceof Error ? err.message : String(err) } }),
    )
    return () => {
      cancelled = true
    }
  }, [api, event, key])

  if (!key) return { status: 'idle' }
  if (cache.has(key)) return { status: 'ok', data: cache.get(key)! }
  return state?.key === key ? state.value : { status: 'loading' }
}
