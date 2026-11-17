import { describe, expect, it } from 'vitest'
import type { HarnessProfile, PackSummary } from '@/api/types'
import { gapText, lowestRungWith, offSet, profileGap, profileOf } from './profiles'

const p = (id: string, rung: number, off: string[]): HarnessProfile => ({ id, title: id, summary: `${id} shape`, rung, disable: off, off, stores: [], overrides: {} })
const ladder = [p('operated', 6, []), p('minimal', 1, ['a', 'b', 'c']), p('guarded', 2, ['b', 'c']), p('governed', 5, ['c'])]
const packs: PackSummary[] = [{ id: 'one', profile: 'guarded' }, { id: 'two', profile: 'nope' }, { id: 'three' }]

describe('harness profiles (0.2.0)', () => {
  it('finds the profile a pack runs with, or nothing', () => {
    expect(profileOf(packs, ladder, 'one')?.id).toBe('guarded')
    expect(profileOf(packs, ladder, 'two')).toBeNull()
    expect(profileOf(packs, ladder, 'three')).toBeNull()
    expect(profileOf(packs, null, 'one')).toBeNull()
    expect([...offSet(profileOf(packs, ladder, 'one'))]).toEqual(['b', 'c'])
    expect(offSet(null).size).toBe(0)
  })

  it('names the lowest rung that has every required node on, whatever the list order', () => {
    expect(lowestRungWith(ladder, ['a'])?.id).toBe('guarded')
    expect(lowestRungWith(ladder, ['c'])?.id).toBe('operated')
    expect(lowestRungWith(ladder, [])?.id).toBe('minimal')
  })

  it('says why a chapter cannot be tried under the current profile', () => {
    const guarded = ladder[2]!
    expect(profileGap(guarded, ladder, ['a'])).toBeNull()
    expect(profileGap(guarded, ladder, [])).toBeNull()
    expect(profileGap(null, ladder, ['c'])).toBeNull()
    const gap = profileGap(guarded, ladder, ['b', 'c'])!
    expect(gap.off).toEqual(['b', 'c'])
    expect(gap.rung?.id).toBe('operated')
    expect(gapText(gap)).toBe('b, c are off in profile guarded. Switch to a pack on the operated rung (6) or higher to try it.')
    expect(gapText(profileGap(guarded, ladder, ['b'])!)).toBe('b is off in profile guarded. Switch to a pack on the governed rung (5) or higher to try it.')
  })
})
