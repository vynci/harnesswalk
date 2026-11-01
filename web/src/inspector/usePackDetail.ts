// The session pack's card and file tree (GET /v1/packs/{id}, P5 integration), fetched once per
// pack: pack files do not change while the app runs.
import type { Api } from '@/api/client'
import type { PackDetail } from '@/api/types'
import { useApp } from '@/state/store'
import { useLoad } from './useOps'

const details = new WeakMap<Api, Map<string, Promise<PackDetail>>>()

/** GET /v1/packs/{id}, once per pack (pack files do not change while the app runs). */
function loadDetail(api: Api, pack: string): Promise<PackDetail> {
  let cache = details.get(api)
  if (!cache) details.set(api, (cache = new Map()))
  let p = cache.get(pack)
  if (!p) {
    p = api.packDetail(pack)
    cache.set(pack, p)
    p.catch(() => cache.delete(pack))
  }
  return p
}

/** The session pack's card and file tree, or nothing without `features.packs`. */
export function usePackDetail(): { pack: string | null; detail: PackDetail | null; error?: string } {
  const api = useApp((s) => s.api)
  const pack = useApp((s) => s.pack)
  const on = useApp((s) => s.capabilities?.features.packs === true)
  const out = useLoad(api && on && pack ? `pack:${pack}` : null, () => loadDetail(api!, pack!))
  return { pack, detail: out.data && out.data.id === pack ? out.data : null, error: out.error }
}

