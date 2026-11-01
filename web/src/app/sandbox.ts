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

/** Under this much time left the header's countdown turns to the warning colour. */
export const CHIP_WARN_MS = 10 * 60_000

/** The chip's short countdown: "1 h 39 m", "9 m", "<1 m", "ended". */
export function chipText(left: number): string {
  if (left <= 0) return 'ended'
  if (left < 60_000) return '<1 m'
  return leftText(left)
}

/** The popover's sentence about the end of the sandbox session, with the time it ends. */
export function endsSentence(left: number, end: number, locale?: string): string {
  if (left <= 0) return 'This sandbox session ended: everything in it was deleted. Your next message starts a fresh one.'
  const at = new Date(end).toLocaleTimeString(locale ?? [], { hour: '2-digit', minute: '2-digit' })
  return `This sandbox session ends in ${leftText(left)}, at ${at}. Everything in it is deleted then.`
}
