// The public sandbox session (phase-6 CONTRACT section 6): how long is left, in words, and when
// the tab warns and resets. Pure, so the times are tested without a clock.

/** Five minutes before the end, the tab warns once. */
export const WARN_BEFORE_MS = 5 * 60_000

/** "1 h 48 m", "12 m", "less than a minute". */
export function leftText(ms: number): string {
  if (ms < 60_000) return 'less than a minute'
  const minutes = Math.floor(ms / 60_000)
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return h > 0 ? `${h} h ${m} m` : `${m} m`
}

/** What the tab does next for an expiry time: warn, expire, or nothing, and in how many ms. */
export function sandboxPlan(expiresAt: string | null | undefined, now: number): { warnIn: number | null; expireIn: number | null } {
  const end = expiresAt ? Date.parse(expiresAt) : NaN
  if (!Number.isFinite(end)) return { warnIn: null, expireIn: null }
  const left = end - now
  return { warnIn: Math.max(0, left - WARN_BEFORE_MS), expireIn: Math.max(0, left) }
}
