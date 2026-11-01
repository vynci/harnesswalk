// The empty chat's suggested prompts, from the active pack's `prompts.md` once the API lists
// packs (GET /v1/packs with `features.packs`, P5). Until then, and whenever the lookup fails,
// the caller keeps its `_starter` prompts.
import { useEffect, useState } from 'react'
import type { Api } from '@/api/client'
import type { PackSummary } from '@/api/types'
import { useApp } from '@/state/store'

/** A pack's `prompts`, as strings or `{prompt}` objects, as a clean list of strings. */
export function normalizePrompts(prompts: unknown): string[] {
  if (!Array.isArray(prompts)) return []
  const out: string[] = []
  for (const p of prompts) {
    const text = typeof p === 'string' ? p : p && typeof p === 'object' && typeof (p as { prompt?: unknown }).prompt === 'string' ? (p as { prompt: string }).prompt : ''
    if (text.trim() && !out.includes(text.trim())) out.push(text.trim())
  }
  return out
}

/** The pack whose prompts to show: the one named, else the API's default, else `_starter`, else the first listed. */
export function pickPack(packs: PackSummary[], packId: string | null | undefined): PackSummary | undefined {
  return packs.find((p) => p.id === packId) ?? packs.find((p) => p.default) ?? packs.find((p) => p.id === '_starter') ?? packs[0]
}

const listed = new WeakMap<Api, Promise<PackSummary[]>>()

/**
 * The active pack's prompts, or null to keep the fallback. The pack picker plugs in here: pass
 * the session's pack id (from the store, once pack selection per session lands) as `packId`.
 */
export function usePackPrompts(packId: string | null = null): string[] | null {
  const api = useApp((s) => s.api)
  const enabled = useApp((s) => s.capabilities?.features.packs === true)
  const [packs, setPacks] = useState<PackSummary[] | null>(null)
  useEffect(() => {
    if (!api || !enabled) return
    let cancelled = false
    let p = listed.get(api)
    if (!p) {
      p = api.packs()
      listed.set(api, p)
      p.catch(() => listed.delete(api))
    }
    p.then(
      (list) => !cancelled && setPacks(list),
      () => !cancelled && setPacks(null),
    )
    return () => {
      cancelled = true
    }
  }, [api, enabled])
  if (!enabled || !packs) return null
  const prompts = normalizePrompts(pickPack(packs, packId)?.prompts)
  return prompts.length ? prompts : null
}
