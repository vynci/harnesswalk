import { describe, expect, it } from 'vitest'
import { normalizePrompts, pickPack } from './usePackPrompts'

describe('pack prompts', () => {
  it('reads strings and {prompt} objects, dropping blanks and repeats', () => {
    expect(normalizePrompts(['Where is order A-1001?', { prompt: 'What are your support hours?' }, '  ', { title: 'x' }, 3, 'Where is order A-1001?'])).toEqual([
      'Where is order A-1001?',
      'What are your support hours?',
    ])
    expect(normalizePrompts(undefined)).toEqual([])
    expect(normalizePrompts({ prompts: [] })).toEqual([])
  })

  it('picks the named pack, else the starter, else the first', () => {
    const packs = [{ id: 'clinic' }, { id: '_starter' }, { id: 'bank' }]
    expect(pickPack(packs, 'bank')!.id).toBe('bank')
    expect(pickPack(packs, null)!.id).toBe('_starter')
    expect(pickPack([{ id: 'clinic' }], 'nope')!.id).toBe('clinic')
    expect(pickPack([], null)).toBeUndefined()
    expect(pickPack([{ id: '_starter' }, { id: 'clinic', default: true }], null)!.id).toBe('clinic')
  })
})
