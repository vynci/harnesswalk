// Pure helpers for the harness profiles (0.2.0, spec/harness.md section 10): the profile a pack
// runs with, what it switches off, and the lowest rung that has a set of nodes on. Everything
// comes from GET /v1/packs and GET /v1/profiles, so no node id or rung is named here.
import type { HarnessProfile, PackSummary } from '@/api/types'

/** The profile the server runs `packId` with, or null (an older API, or an unknown profile). */
export function profileOf(packs: PackSummary[] | null | undefined, profiles: HarnessProfile[] | null | undefined, packId: string | null | undefined): HarnessProfile | null {
  if (!packs || !profiles || !packId) return null
  const id = packs.find((p) => p.id === packId)?.profile
  return (id && profiles.find((p) => p.id === id)) || null
}

/** The nodes a profile switches off on this server, as a set (empty without a profile). */
export function offSet(profile: HarnessProfile | null | undefined): ReadonlySet<string> {
  return new Set(profile?.off ?? [])
}

/** The lowest rung whose profile has every one of `nodes` on, or null. */
export function lowestRungWith(profiles: HarnessProfile[] | null | undefined, nodes: readonly string[]): HarnessProfile | null {
  const byRung = [...(profiles ?? [])].sort((a, b) => a.rung - b.rung)
  return byRung.find((p) => !nodes.some((n) => p.off.includes(n))) ?? null
}

export interface ProfileGap {
  /** The required nodes the current profile switches off. */
  off: string[]
  /** The profile the pack runs with. */
  profile: HarnessProfile
  /** The lowest rung that has them all on, if any. */
  rung: HarnessProfile | null
}

/** Why a chapter cannot be tried under the current profile, or null when it can. */
export function profileGap(profile: HarnessProfile | null, profiles: HarnessProfile[] | null | undefined, requires: readonly string[] | undefined): ProfileGap | null {
  if (!profile || !requires?.length) return null
  const off = requires.filter((n) => profile.off.includes(n))
  if (off.length === 0) return null
  return { off, profile, rung: lowestRungWith(profiles, requires) }
}

/** The sentence the Guide shows on a chapter whose node is off. */
export function gapText(gap: ProfileGap, label: (id: string) => string = (id) => id): string {
  const what = gap.off.map(label).join(', ')
  const where = gap.rung ? ` Switch to a pack on the ${gap.rung.id} rung (${gap.rung.rung}) or higher to try it.` : ''
  return `${what} ${gap.off.length === 1 ? 'is' : 'are'} off in profile ${gap.profile.id}.${where}`
}
