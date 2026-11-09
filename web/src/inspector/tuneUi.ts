// Which part of the Tune panel shows (0.2.0): kept outside the panel so another inspector (the
// curate node's link to its datasets) can open it on a section.
import { create } from 'zustand'

export type TuneSection = 'datasets' | 'runs' | 'compare' | 'canary'

export const TUNE_SECTIONS: { value: TuneSection; label: string }[] = [
  { value: 'datasets', label: 'Datasets' },
  { value: 'runs', label: 'Runs' },
  { value: 'compare', label: 'Compare' },
  { value: 'canary', label: 'Canary' },
]

export const useTuneUi = create<{ section: TuneSection; setSection: (s: TuneSection) => void }>((set) => ({
  section: 'datasets',
  setSection: (section) => set({ section }),
}))
