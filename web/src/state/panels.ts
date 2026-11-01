// The header's panels, kept apart from the app store: the Guide (open, and the node whose chapter
// it shows), a code walk link the Guide hands to the inspector's Code tab, and the session chip's
// popover (the status bar's budget readout opens it too).
import { create } from 'zustand'
import type { LessonCodeLink } from '@/api/types'
import { useApp } from './store'

/** A code walk link with the chapter it came from, for the inspector's Code tab. */
export type CodeTarget = LessonCodeLink & { slug: string }

interface PanelsState {
  /** The Guide pane is open. */
  guideOpen: boolean
  /** The node whose chapter the Guide shows; null shows the chapter list. */
  guideNode: string | null
  /** A code walk link for the inspector open on `node` (bumped `key` per click). */
  codeLink: { node: string; target: CodeTarget; key: number } | null
  /** The session chip's popover is open. */
  sessionOpen: boolean
  /** Open the Guide, on a node's chapter, or on the chapter list with `null`. */
  openGuide: (node?: string | null) => void
  closeGuide: () => void
  /** The Guide follows the node picked on the canvas while it is open. */
  followNode: (node: string | null) => void
  showCode: (node: string, target: CodeTarget) => void
  clearCode: () => void
  setSessionOpen: (open: boolean) => void
}

let keys = 0

export const usePanels = create<PanelsState>()((set, get) => ({
  guideOpen: false,
  guideNode: null,
  codeLink: null,
  sessionOpen: false,
  openGuide: (node) => set({ guideOpen: true, guideNode: node === undefined ? get().guideNode : node }),
  closeGuide: () => set({ guideOpen: false }),
  followNode: (node) => {
    if (get().guideOpen && node && node !== get().guideNode) set({ guideNode: node })
  },
  showCode: (node, target) => set({ codeLink: { node, target, key: ++keys } }),
  clearCode: () => set({ codeLink: null }),
  setSessionOpen: (sessionOpen) => set({ sessionOpen }),
}))

// While the Guide is open it follows the node picked on the canvas to that node's chapter.
useApp.subscribe((s, prev) => {
  if (s.selectedNode !== prev.selectedNode) usePanels.getState().followNode(s.selectedNode)
})
