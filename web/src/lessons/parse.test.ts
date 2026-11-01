import { describe, expect, it } from 'vitest'
import chapter02 from '../../../lessons/02-input_guardrails.md?raw'
import { classifyLink, repoBlobUrl } from './links'

const REPO = 'https://example.org/acme/harnesswalk'
const REPO_BLOB_URL = `${REPO}/blob/main/`
import { lessonRef, parseChecks, parseLesson } from './parse'
import { createSlugger, headingSlug } from './slug'

const chapters = import.meta.glob('../../../lessons/[0-9][0-9]-*.md', { query: '?raw', import: 'default', eager: true }) as Record<string, string>
const strip = (text: string) => text.replace(/^---\n[\s\S]*?\n---\n/, '')
const headings = (md: string) => md.split('\n').filter((l) => l.startsWith('## ')).map((l) => l.slice(3))

describe('parseLesson on chapter 02', () => {
  const lesson = parseLesson(strip(chapter02))

  it('takes the title and hook off the top', () => {
    expect(lesson.title).toBe('Input guardrails')
    expect(lesson.hook).toMatch(/^Size limits, PII redaction and injection checks/)
    expect(lesson.before.startsWith('## What it does')).toBe(true)
  })

  it('splits the sections around Check yourself', () => {
    expect(headings(lesson.before)).toEqual(['What it does', 'What breaks without it', 'The code walk', 'Try this'])
    expect(lesson.checksHeading).toBe('Check yourself')
    expect(headings(lesson.after)).toEqual(['Further reading'])
    expect(lesson.before).not.toContain('Check yourself')
    expect(lesson.before).not.toContain('- [x]')
  })

  it('reads three questions with three choices, one right, and an explanation', () => {
    const checks = lesson.checks!
    expect(checks.map((q) => q.n)).toEqual([1, 2, 3])
    expect(checks[0]!.text).toBe('Why is PII redaction written as code in this node rather than as a rule in the system prompt?')
    for (const q of checks) {
      expect(q.choices).toHaveLength(3)
      expect(q.choices.filter((c) => c.correct)).toHaveLength(1)
      expect(q.answer.length).toBeGreaterThan(20)
      expect(q.answer).not.toMatch(/^Answer:/)
    }
    expect(checks.map((q) => q.choices.findIndex((c) => c.correct))).toEqual([1, 2, 0])
    // The explanation keeps its wrapped lines.
    expect(checks[0]!.answer).toContain('never sees the\nargument')
  })

  it('keeps fenced code that contains a heading-like line inside its section', () => {
    const p = parseLesson('# T\n\n> h\n\n## A\n\n```md\n## not a heading\n```\n\n## Check yourself\n\n### 1. Q\n\n- [x] a\n- [ ] b\n\nAnswer: because.\n')
    expect(headings(p.before)).toEqual(['A', 'not a heading'])
    expect(p.checks).toHaveLength(1)
  })
})

describe('every chapter in lessons/ has the format the UI reads', () => {
  for (const [path, text] of Object.entries(chapters)) {
    it(path.split('/').pop()!, () => {
      const lesson = parseLesson(strip(text))
      expect(lesson.title).not.toBe('')
      expect(lesson.hook).not.toBe('')
      expect(lesson.checks, 'Check yourself parses').not.toBeNull()
      expect(lesson.checks).toHaveLength(3)
      for (const q of lesson.checks!) expect(q.choices).toHaveLength(3)
    })
  }
})

describe('parseChecks', () => {
  it('rejects a question with no right choice or two', () => {
    expect(parseChecks('### 1. Q\n\n- [ ] a\n- [ ] b\n\nAnswer: x')).toBeNull()
    expect(parseChecks('### 1. Q\n\n- [x] a\n- [x] b\n\nAnswer: x')).toBeNull()
    expect(parseChecks('no questions here')).toBeNull()
  })

  it('joins a wrapped choice', () => {
    const [q] = parseChecks('### 2) Q\n\n- [ ] one\n  more\n- [x] two\n\nAnswer: y')!
    expect(q!.n).toBe(2)
    expect(q!.choices[0]!.text).toBe('one more')
  })

  it('falls back to markdown when the section does not parse', () => {
    const p = parseLesson('# T\n\n## Check yourself\n\nThink about it.\n\n## Further reading\n')
    expect(p.checks).toBeNull()
    expect(headings(p.before)).toEqual(['Check yourself', 'Further reading'])
  })
})

describe('code links in chapter 02', () => {
  it('are code links into the package, with their symbols', () => {
    const links = [...strip(chapter02).matchAll(/\]\(([^)]+)\)/g)].map((m) => classifyLink(m[1]!, REPO))
    const code = links.filter((l) => l.kind === 'code')
    expect(code).toContainEqual({ kind: 'code', path: 'harnesswalk/control/input_guardrails.py', symbol: 'InputGuardrails.check' })
    expect(code).toContainEqual({ kind: 'code', path: 'harnesswalk/control/pii.py', symbol: 'Vault' })
    expect(links).toContainEqual({ kind: 'repo', path: 'spec/contracts/guardrail.md', href: `${REPO_BLOB_URL}spec/contracts/guardrail.md` })
    expect(links.filter((l) => l.kind === 'external')).toHaveLength(2)
  })
})

describe('classifyLink', () => {
  it('reads chapter, anchor, repo and external links from lessons/', () => {
    expect(classifyLink('03-context_builder.md')).toEqual({ kind: 'chapter', slug: '03-context_builder', anchor: null })
    expect(classifyLink('./16-memory-inside.md#semantic')).toEqual({ kind: 'chapter', slug: '16-memory-inside', anchor: 'semantic' })
    expect(classifyLink('#try-this')).toEqual({ kind: 'anchor', id: 'try-this' })
    expect(classifyLink('README.md', REPO)).toEqual({ kind: 'repo', path: 'lessons/README.md', href: `${REPO_BLOB_URL}lessons/README.md` })
    // capabilities.repo_url decides where repo links go; without one the path is not linked.
    expect(classifyLink('README.md', `${REPO}/`)).toMatchObject({ href: `${REPO_BLOB_URL}lessons/README.md` })
    expect(classifyLink('README.md', null)).toEqual({ kind: 'repo', path: 'lessons/README.md', href: null })
    expect(repoBlobUrl('')).toBeNull()
    expect(classifyLink('../harnesswalk/control/pii.py')).toEqual({ kind: 'code', path: 'harnesswalk/control/pii.py', symbol: null })
    expect(classifyLink('https://example.com/x')).toEqual({ kind: 'external', href: 'https://example.com/x' })
    expect(classifyLink('../../outside').kind).toBe('external')
  })
})

describe('headings and lesson urls', () => {
  it('slugs headings the GitHub way', () => {
    expect(headingSlug('Online eval')).toBe('online-eval')
    expect(headingSlug('What breaks without it')).toBe('what-breaks-without-it')
    expect(headingSlug('1. Why is PII redaction written as code?')).toBe('1-why-is-pii-redaction-written-as-code')
    expect(headingSlug('The `memory_writer` gate')).toBe('the-memory_writer-gate')
    const slug = createSlugger()
    expect([slug('Notes'), slug('Notes'), slug('Notes'), slug('Notes-1')]).toEqual(['notes', 'notes-1', 'notes-2', 'notes-1-1'])
  })

  it('reads a lesson_url as slug and anchor', () => {
    expect(lessonRef('lessons/02-input_guardrails.md')).toEqual({ slug: '02-input_guardrails', anchor: null })
    expect(lessonRef('lessons/16-memory-inside.md#semantic')).toEqual({ slug: '16-memory-inside', anchor: 'semantic' })
    expect(lessonRef(undefined)).toBeNull()
    expect(lessonRef('lessons/README.md')).toBeNull()
  })
})
