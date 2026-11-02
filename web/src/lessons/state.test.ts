import { afterEach, describe, expect, it, vi } from 'vitest'
import { diagram } from '@/test/fixtures'
import type { LessonListItem } from '@/api/types'
import { buildOutline, stepEntry } from './outline'
import { answersOf, chapterStatus, loadProgress, markRead, PROGRESS_KEY, recordAnswer, sanitize, saveProgress } from './progress'


describe('progress store', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('marks a chapter read once', () => {
    const p = markRead({}, '02-x')
    expect(chapterStatus(p, '02-x')).toEqual({ read: true, answered: 0, checked: false })
    expect(markRead(p, '02-x')).toBe(p)
  })

  it('records answers and marks checked when every question has one', () => {
    let p = recordAnswer({}, '02-x', 0, 1, 3)
    p = recordAnswer(p, '02-x', 1, 2, 3)
    expect(chapterStatus(p, '02-x')).toEqual({ read: false, answered: 2, checked: false })
    p = recordAnswer(p, '02-x', 1, 0, 3)
    expect(answersOf(p, '02-x')).toEqual({ 0: 1, 1: 0 })
    p = recordAnswer(p, '02-x', 2, 0, 3)
    expect(chapterStatus(p, '02-x').checked).toBe(true)
    // Changing an answer later keeps the chapter checked.
    expect(chapterStatus(recordAnswer(p, '02-x', 0, 2, 3), '02-x').checked).toBe(true)
  })

  it('keeps only well-formed entries from storage', () => {
    expect(sanitize(null)).toEqual({})
    expect(sanitize([1])).toEqual({})
    expect(sanitize({ a: { read: true, answers: { 0: 1, x: 2, 1: 'b' } }, b: 3, c: { checked: true } })).toEqual({ a: { read: true, answers: { 0: 1 } }, c: { checked: true } })
  })

  it('round-trips through localStorage as harnesswalk.lessons', () => {
    const data = new Map<string, string>()
    vi.stubGlobal('localStorage', { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => data.set(k, v), removeItem: (k: string) => data.delete(k) })
    saveProgress(markRead({}, '02-x'))
    expect(PROGRESS_KEY).toBe('lessons')
    expect(JSON.parse(data.get('harnesswalk.lessons')!)).toEqual({ '02-x': { read: true } })
    expect(loadProgress()).toEqual({ '02-x': { read: true } })
  })

  it('works without storage', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
    })
    expect(loadProgress()).toEqual({})
    expect(() => saveProgress({ a: { read: true } })).not.toThrow()
  })
})

describe('outline', () => {
  const walk = diagram.walk_order
  const item = (slug: string, extra: Partial<LessonListItem> = {}): LessonListItem => ({
    slug,
    n: Number(slug.slice(0, 2)),
    title: `Title ${slug}`,
    hook: 'hook',
    node_id: null,
    covers: [],
    group: false,
    words: 900,
    url: `/v1/lessons/${slug}`,
    ...extra,
  })

  it('lists one chapter per walk node, then the expert groups, from the diagram alone', () => {
    const outline = buildOutline(diagram, null)
    const simple = outline.filter((e) => !e.group)
    expect(simple.map((e) => e.nodeId)).toEqual(walk)
    expect(outline.slice(0, walk.length).every((e) => !e.group)).toBe(true)
    const groups = outline.filter((e) => e.group)
    expect(groups.length).toBe(new Set(diagram.nodes.filter((n) => n.view === 'expert').map((n) => n.parent)).size)
    for (const g of groups) {
      const target = diagram.nodes.find((n) => n.id === g.nodeId)!
      expect(target.view).toBe('expert')
      expect(g.view).toBe('expert')
      expect(diagram.nodes.find((n) => n.id === target.parent)!.label).toBe(g.title)
    }
    expect(outline.map((e) => e.n)).toEqual([...outline.map((e) => e.n)].sort((a, b) => a - b))
    expect(outline.every((e) => !e.written)).toBe(true)
  })

  it('takes title, hook and the first covered node from the served chapters', () => {
    const groupEntry = buildOutline(diagram, null).find((e) => e.group)!
    const parent = diagram.nodes.find((n) => n.id === diagram.nodes.find((x) => x.id === groupEntry.nodeId)!.parent)!
    const children = diagram.nodes.filter((n) => n.parent === parent.id).map((n) => n.id)
    const covers = [...children].reverse()
    const outline = buildOutline(diagram, [item(groupEntry.slug, { node_id: parent.id, covers, group: true })])
    const e = outline.find((x) => x.slug === groupEntry.slug)!
    expect(e).toMatchObject({ written: true, title: `Title ${groupEntry.slug}`, nodeId: covers[0], group: true })
  })

  it('steps to the next and previous chapter', () => {
    const outline = buildOutline(diagram, null)
    expect(stepEntry(outline, outline[0]!.slug, 1)).toBe(outline[1])
    expect(stepEntry(outline, outline[0]!.slug, -1)).toBeNull()
    expect(stepEntry(outline, 'nope', 1)).toBeNull()
  })
})
