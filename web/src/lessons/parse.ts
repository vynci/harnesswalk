// Reads a chapter's markdown (front matter already stripped by the API) into the parts the
// Guide renders for a chapter: the title and hook it shows itself, the markdown before and after the
// "Check yourself" section, and that section's questions for the Questionnaire. A section that
// does not parse falls back to plain markdown, so a chapter in progress still renders.

export interface CheckChoice {
  /** Inline markdown. */
  text: string
  correct: boolean
}

export interface CheckQuestion {
  n: number
  /** Inline markdown, without the `N.` prefix. */
  text: string
  choices: CheckChoice[]
  /** The explanation (markdown) after `Answer:`. */
  answer: string
}

export interface ParsedLesson {
  title: string
  hook: string
  /** Markdown before the Check yourself section (or the whole body without one). */
  before: string
  /** The questions, or null when the chapter has no section that parses. */
  checks: CheckQuestion[] | null
  /** The section's heading text, when it parsed. */
  checksHeading: string | null
  /** Markdown after the Check yourself section (Further reading). */
  after: string
}

const CHECK_HEADING = /^check yourself$/i
const FENCE = /^\s*(```|~~~)/

/** Lines split at `##` headings outside fenced code: [heading text or null, lines]. */
function sections(lines: string[], level: number): { heading: string | null; lines: string[] }[] {
  const marker = `${'#'.repeat(level)} `
  const out: { heading: string | null; lines: string[] }[] = [{ heading: null, lines: [] }]
  let fenced = false
  for (const line of lines) {
    if (FENCE.test(line)) fenced = !fenced
    if (!fenced && line.startsWith(marker)) out.push({ heading: line.slice(marker.length).trim(), lines: [] })
    else out[out.length - 1]!.lines.push(line)
  }
  return out
}

const join = (lines: string[]) => lines.join('\n').trim()

/** The `### N. Question` blocks of a Check yourself section, or null if any is malformed. */
export function parseChecks(body: string): CheckQuestion[] | null {
  const blocks = sections(body.split('\n'), 3).filter((b) => b.heading !== null)
  if (blocks.length === 0) return null
  const questions: CheckQuestion[] = []
  for (const [i, block] of blocks.entries()) {
    const head = block.heading!.match(/^(\d+)[.)]\s+(.*)$/)
    const choices: CheckChoice[] = []
    const answer: string[] = []
    let inAnswer = false
    for (const line of block.lines) {
      const item = line.match(/^\s*[-*+]\s+\[([ xX])\]\s+(.*)$/)
      if (!inAnswer && item) {
        choices.push({ text: item[2]!.trim(), correct: item[1] !== ' ' })
      } else if (!inAnswer && /^\s*Answer:/.test(line)) {
        inAnswer = true
        answer.push(line.replace(/^\s*Answer:\s*/, ''))
      } else if (inAnswer) {
        answer.push(line)
      } else if (choices.length && /^\s{2,}\S/.test(line)) {
        // A wrapped choice continues on an indented line.
        const last = choices[choices.length - 1]!
        last.text = `${last.text} ${line.trim()}`
      }
    }
    if (choices.length < 2 || choices.filter((c) => c.correct).length !== 1) return null
    questions.push({ n: head ? Number(head[1]) : i + 1, text: head ? head[2]!.trim() : block.heading!, choices, answer: join(answer) })
  }
  return questions
}

/** The chapter's parts: title, hook, markdown around the checks, and the checks. */
export function parseLesson(markdown: string): ParsedLesson {
  let lines = markdown.replace(/\r\n?/g, '\n').split('\n')
  let title = ''
  let hook = ''
  // `# Title`, then the one-line `> hook`, both shown by the tab's own header.
  while (lines.length && !lines[0]!.trim()) lines = lines.slice(1)
  if (lines[0]?.startsWith('# ')) {
    title = lines[0].slice(2).trim()
    lines = lines.slice(1)
    while (lines.length && !lines[0]!.trim()) lines = lines.slice(1)
    if (lines[0]?.startsWith('> ')) {
      hook = lines[0].slice(2).trim()
      lines = lines.slice(1)
    }
  }
  const parts = sections(lines, 2)
  const at = parts.findIndex((p) => p.heading !== null && CHECK_HEADING.test(p.heading))
  const render = (ps: typeof parts) => join(ps.flatMap((p) => (p.heading === null ? p.lines : [`## ${p.heading}`, ...p.lines])))
  const checks = at >= 0 ? parseChecks(parts[at]!.lines.join('\n')) : null
  if (at < 0 || !checks) return { title, hook, before: render(parts), checks: null, checksHeading: null, after: '' }
  return { title, hook, before: render(parts.slice(0, at)), checks, checksHeading: parts[at]!.heading, after: render(parts.slice(at + 1)) }
}

/** `lessons/16-memory-inside.md#semantic` as its slug and anchor. */
export function lessonRef(url: string | undefined | null): { slug: string; anchor: string | null } | null {
  if (!url) return null
  const [path, anchor] = url.split('#', 2)
  const m = path!.match(/(?:^|\/)(\d{2}-[A-Za-z0-9_-]+)\.md$/)
  return m ? { slug: m[1]!, anchor: anchor || null } : null
}
