import { describe, expect, it } from 'vitest'
import { MockServer } from './server'
import { symbolLine } from './lessons'
import type { Lesson, LessonList, LessonSource } from '@/api/types'

const get = async <T>(server: MockServer, path: string): Promise<{ status: number; body: T }> => {
  const res = await server.handle(path)
  return { status: res.status, body: (await res.json()) as T }
}

describe('mock lesson routes', () => {
  const server = new MockServer({ stepMs: 0, seed: false })

  it('lists the chapters with the API shape', async () => {
    const { body } = await get<LessonList>(server, '/v1/lessons')
    expect(body.walk_order).toEqual(server.diagram.walk_order)
    const ch = body.lessons.find((l) => l.slug === '02-input_guardrails')!
    expect(ch).toMatchObject({ n: 2, title: 'Input guardrails', covers: [ch.node_id], group: false, url: '/v1/lessons/02-input_guardrails' })
    expect(ch.words).toBeGreaterThan(600)
    expect(Object.keys(ch).sort()).toEqual(['covers', 'group', 'hook', 'n', 'node_id', 'requires', 'slug', 'title', 'url', 'words'])
  })

  it('serves one chapter without its front matter, with try and code links', async () => {
    const { body } = await get<Lesson>(server, '/v1/lessons/02-input_guardrails')
    expect(body.markdown.startsWith('# Input guardrails')).toBe(true)
    expect(body.try).toMatchObject({ disable: [body.node_id], fixture: 'tool_call' })
    const check = body.code_links.find((l) => l.symbol === 'InputGuardrails.check')!
    expect(check).toMatchObject({ path: 'harnesswalk/control/input_guardrails.py', node_id: body.node_id })
    expect(check.line).toBeGreaterThan(1)
    expect(body.code_links.find((l) => l.path === 'harnesswalk/control/pii.py')!.node_id).toBeNull()
    for (const link of body.code_links) expect(link.line, `${link.path}#${link.symbol}`).not.toBeNull()
  })

  it('answers 404 lesson_not_found for a chapter that does not exist', async () => {
    const { status, body } = await get<{ error: { code: string } }>(server, '/v1/lessons/99-not_written')
    expect(status).toBe(404)
    expect(body.error.code).toBe('lesson_not_found')
  })

  it('serves the source of a linked file only', async () => {
    const ok = await get<LessonSource>(server, '/v1/lessons/02-input_guardrails/source?path=harnesswalk/control/pii.py')
    expect(ok.status).toBe(200)
    expect(ok.body).toMatchObject({ path: 'harnesswalk/control/pii.py', language: 'python', truncated: false })
    expect(ok.body.symbols!.some((s) => s.name === 'Vault')).toBe(true)
    const no = await get<{ error: { code: string } }>(server, '/v1/lessons/02-input_guardrails/source?path=harnesswalk/runtime.py')
    expect(no.status).toBe(404)
    expect(no.body.error.code).toBe('source_not_found')
  })
})

describe('symbolLine', () => {
  const src = ['X = 1', 'class A:', '    def f(self):', '        pass', '', 'class B:', '    def g(self):', '        pass', 'def f():', '    pass'].join('\n')
  it('finds top-level names and members inside their class', () => {
    expect(symbolLine(src, 'X')).toBe(1)
    expect(symbolLine(src, 'A.f')).toBe(3)
    expect(symbolLine(src, 'f')).toBe(9)
    expect(symbolLine(src, 'B.f')).toBeNull()
    expect(symbolLine(src, 'B.g')).toBe(7)
  })
})

describe('lesson demonstrations (P5 integration)', () => {
  it('spells out the try pack and lists the same demonstration on a domain pack', async () => {
    const lessons = new MockServer({ stepMs: 0 }).lessons
    expect(lessons.get('01-gateway')?.try?.pack).toBe('_starter')
    const writer = lessons.get('12-memory_writer')!
    expect(writer.also).toEqual([
      expect.objectContaining({ pack: 'finance', disable: ['memory_writer'], fixture: 'mem_reporting_currency_set' }),
      expect.objectContaining({ pack: 'long_session', disable: ['memory_writer'], fixture: 'remember_aisle_seat' }),
    ])
    expect(writer.also![0]!.note).toMatch(/Nothing is written/)
  })
})
