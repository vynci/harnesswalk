// What this viewer has done with each chapter, kept in localStorage as `harnesswalk.lessons`:
// `read` once the chapter was opened in the Lessons tab, the choice picked per question, and
// `checked` once every question has an answer. Pure functions, guarded storage.
import { load, save } from '@/state/persist'

export interface ChapterProgress {
  read?: boolean
  /** Question index to the index of the picked choice. */
  answers?: Record<string, number>
  /** Every question of the chapter has an answer. */
  checked?: boolean
}

export type Progress = Record<string, ChapterProgress>

/** The localStorage key, under the `harnesswalk.` prefix. */
export const PROGRESS_KEY = 'lessons'

export function markRead(p: Progress, slug: string): Progress {
  if (p[slug]?.read) return p
  return { ...p, [slug]: { ...p[slug], read: true } }
}

export function recordAnswer(p: Progress, slug: string, question: number, choice: number, total: number): Progress {
  const prev = p[slug] ?? {}
  if (prev.answers?.[question] === choice) return p
  const answers = { ...prev.answers, [question]: choice }
  return { ...p, [slug]: { ...prev, answers, checked: prev.checked || Object.keys(answers).length >= total } }
}

export function answersOf(p: Progress, slug: string): Record<number, number> {
  return (p[slug]?.answers ?? {}) as Record<number, number>
}

export interface ChapterStatus {
  read: boolean
  answered: number
  checked: boolean
}

export function chapterStatus(p: Progress, slug: string): ChapterStatus {
  const c = p[slug]
  return { read: !!c?.read, answered: Object.keys(c?.answers ?? {}).length, checked: !!c?.checked }
}

/** What storage held, keeping only well-formed entries (storage is shared with older builds). */
export function sanitize(raw: unknown): Progress {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
  const out: Progress = {}
  for (const [slug, v] of Object.entries(raw as Record<string, unknown>)) {
    if (!v || typeof v !== 'object') continue
    const c = v as Record<string, unknown>
    const entry: ChapterProgress = {}
    if (c.read === true) entry.read = true
    if (c.checked === true) entry.checked = true
    if (c.answers && typeof c.answers === 'object') {
      const answers = Object.fromEntries(Object.entries(c.answers as Record<string, unknown>).filter(([k, n]) => /^\d+$/.test(k) && Number.isInteger(n)))
      if (Object.keys(answers).length) entry.answers = answers as Record<string, number>
    }
    out[slug] = entry
  }
  return out
}

export function loadProgress(): Progress {
  return sanitize(load<unknown>(PROGRESS_KEY, {}))
}

export function saveProgress(p: Progress): void {
  save(PROGRESS_KEY, p)
}
