// The header's panels, kept apart from the app store: the Guide (open, and the node whose chapter
// it shows), a code walk link the Guide hands to the inspector's Code tab, the session chip's
// popover, and which turn reports are open on the chat's timeline (per session, in memory).
import { create } from 'zustand'
import type { LessonCodeLink } from '@/api/types'
import { useApp } from './store'

/** A code walk link with the chapter it came from, for the inspector's Code tab. */
export type CodeTarget = LessonCodeLink & { slug: string }

/** A session's turn reports on the chat timeline: the open rows, and whether the next opens itself. */
export interface SessionReports {
  open: string[]
  /** The reader left the latest turn's report open, so the next turn's opens when it ends. */
  follow: boolean
}

const NO_REPORTS: SessionReports = { open: [], follow: false }

/** The key a session's reports are kept under (a session with no id yet has one too). */
export const reportsKey = (sessionId: string | null) => sessionId ?? ''

interface PanelsState {
  /** The Guide pane is open. */
  guideOpen: boolean
  /** The node whose chapter the Guide shows; null shows the chapter list. */
  guideNode: string | null
  /** 0.2.0: a chapter picked from the list that is not its node's own (24-canary covers tune). */
  guideSlug: string | null
  /** A code walk link for the inspector open on `node` (bumped `key` per click). */
  codeLink: { node: string; target: CodeTarget; key: number } | null
  /** The session chip's popover is open. */
  sessionOpen: boolean
  /** Open turn reports on the chat timeline, by session (reportsKey). */
  reports: Record<string, SessionReports>
  /** Open the Guide, on a node's chapter, or on the chapter list with `null`. */
  openGuide: (node?: string | null, slug?: string | null) => void
  closeGuide: () => void
  /** The Guide follows the node picked on the canvas while it is open. */
  followNode: (node: string | null) => void
  showCode: (node: string, target: CodeTarget) => void
  clearCode: () => void
  setSessionOpen: (open: boolean) => void
  /** Open or close a turn's report; on the latest turn it also sets whether the next one follows. */
  setReportOpen: (sessionId: string | null, turnId: string, open: boolean, latest: boolean) => void
}

/** A session's open turn reports. */
export const sessionReports = (s: PanelsState, sessionId: string | null): SessionReports => s.reports[reportsKey(sessionId)] ?? NO_REPORTS

let keys = 0

export const usePanels = create<PanelsState>()((set, get) => ({
  guideOpen: false,
  guideNode: null,
  guideSlug: null,
  codeLink: null,
  sessionOpen: false,
  reports: {},
  openGuide: (node, slug = null) =>
    set({ guideOpen: true, guideNode: node === undefined ? get().guideNode : node, guideSlug: slug }),
  closeGuide: () => set({ guideOpen: false }),
  followNode: (node) => {
    if (get().guideOpen && node && node !== get().guideNode) set({ guideNode: node, guideSlug: null })
  },
  showCode: (node, target) => set({ codeLink: { node, target, key: ++keys } }),
  clearCode: () => set({ codeLink: null }),
  setSessionOpen: (sessionOpen) => set({ sessionOpen }),
  setReportOpen: (sessionId, turnId, open, latest) => {
    const key = reportsKey(sessionId)
    const prev = get().reports[key] ?? NO_REPORTS
    const rest = prev.open.filter((id) => id !== turnId)
    set({ reports: { ...get().reports, [key]: { open: open ? [...rest, turnId] : rest, follow: latest ? open : prev.follow } } })
  },
}))

const finished = (phase: string | undefined) => phase === 'done' || phase === 'error'

// When the latest turn ends, its report opens if the reader left the one before it open.
useApp.subscribe((s, prev) => {
  const id = s.order[s.order.length - 1]
  if (!id) return
  const was = prev.turns[id]?.state?.phase
  if (!was || finished(was) || !finished(s.turns[id]?.state?.phase)) return
  const panels = usePanels.getState()
  const reports = sessionReports(panels, s.sessionId)
  if (reports.follow && !reports.open.includes(id)) panels.setReportOpen(s.sessionId, id, true, true)
})

// While the Guide is open it follows the node picked on the canvas to that node's chapter.
useApp.subscribe((s, prev) => {
  if (s.selectedNode !== prev.selectedNode) usePanels.getState().followNode(s.selectedNode)
})
