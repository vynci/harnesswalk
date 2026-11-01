// The empty chat's suggested prompts, from the session pack's `prompts` (GET /v1/packs with
// `features.packs`, P5; the store loads the list at start-up). Until then, and whenever the
// lookup failed, the caller keeps its `_starter` prompts.
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

/** The prompts of a pack (the session's when `packId` is the store's `pack`), or null to keep the fallback. */
export function usePackPrompts(packId: string | null = null): string[] | null {
  const enabled = useApp((s) => s.capabilities?.features.packs === true)
  const packs = useApp((s) => s.packs)
  if (!enabled || !packs) return null
  const prompts = normalizePrompts(pickPack(packs, packId)?.prompts)
  return prompts.length ? prompts : null
}
