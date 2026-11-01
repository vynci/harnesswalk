import { describe, expect, it } from 'vitest'
import { leftText, sandboxPlan, WARN_BEFORE_MS } from './sandbox'

describe('the sandbox session countdown', () => {
  it('says the time left in hours and minutes', () => {
    expect(leftText((60 + 48) * 60_000 + 30_000)).toBe('1 h 48 m')
    expect(leftText(12 * 60_000)).toBe('12 m')
    expect(leftText(59_000)).toBe('less than a minute')
  })

  it('warns five minutes before the end and resets at the end', () => {
    const now = Date.parse('2026-11-01T16:00:00Z')
    expect(sandboxPlan('2026-11-01T18:00:00Z', now)).toEqual({ warnIn: 2 * 3600_000 - WARN_BEFORE_MS, expireIn: 2 * 3600_000 })
    expect(sandboxPlan('2026-11-01T16:03:00Z', now)).toEqual({ warnIn: 0, expireIn: 180_000 })
    expect(sandboxPlan('2026-11-01T15:00:00Z', now)).toEqual({ warnIn: 0, expireIn: 0 })
    expect(sandboxPlan(null, now)).toEqual({ warnIn: null, expireIn: null })
  })
})
