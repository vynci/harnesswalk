// The Check yourself state: which choice is picked per question, whether it is the right one,
// and how far the reader got. Pure; the answers themselves live in the progress store.
import type { CheckQuestion } from './parse'

export interface QuizItem {
  chosen: number | null
  /** null until a choice is picked. */
  correct: boolean | null
  rightIndex: number
}

export interface QuizView {
  items: QuizItem[]
  answered: number
  right: number
  total: number
  complete: boolean
}

export function rightIndex(q: CheckQuestion): number {
  return q.choices.findIndex((c) => c.correct)
}

export function quizView(questions: CheckQuestion[], answers: Record<number, number>): QuizView {
  const items = questions.map((q, i) => {
    const pick = answers[i]
    const chosen = typeof pick === 'number' && pick >= 0 && pick < q.choices.length ? pick : null
    const r = rightIndex(q)
    return { chosen, correct: chosen === null ? null : chosen === r, rightIndex: r }
  })
  const answered = items.filter((i) => i.chosen !== null).length
  return { items, answered, right: items.filter((i) => i.correct).length, total: questions.length, complete: questions.length > 0 && answered === questions.length }
}

/** The first question without an answer, else the last one. */
export function firstOpen(view: QuizView): number {
  const i = view.items.findIndex((item) => item.chosen === null)
  return i < 0 ? Math.max(0, view.total - 1) : i
}

/** A letter per choice, as the Questionnaire's shortcuts show them. */
export const choiceLetter = (i: number) => String.fromCharCode(65 + i)
