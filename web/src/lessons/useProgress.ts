// The progress store as a hook: the pure functions of progress.ts over one Zustand store that
// writes through to localStorage (harnesswalk.lessons). The app works without storage.
import { create } from 'zustand'
import { loadProgress, markRead, recordAnswer, saveProgress, type Progress } from './progress'

interface ProgressState {
  progress: Progress
  markRead: (slug: string) => void
  answer: (slug: string, question: number, choice: number, total: number) => void
}

export const useLessonProgress = create<ProgressState>()((set, get) => {
  const update = (next: Progress) => {
    if (next === get().progress) return
    saveProgress(next)
    set({ progress: next })
  }
  return {
    progress: loadProgress(),
    markRead: (slug) => update(markRead(get().progress, slug)),
    answer: (slug, question, choice, total) => update(recordAnswer(get().progress, slug, question, choice, total)),
  }
})
