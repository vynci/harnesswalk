// Fetches for the Lessons tab and drawer: the chapter list, one chapter and a linked file, each
// cached per API (the chapters do not change while the app runs). A chapter the API does not
// serve yet (404 lesson_not_found) is `missing`, which the tab shows as not written yet.
import { useEffect, useMemo, useState } from 'react'
import { ApiError, type Api } from '@/api/client'
import type { Lesson, LessonList, LessonSource } from '@/api/types'
import { useApp } from '@/state/store'
import { buildOutline, type OutlineEntry } from './outline'

export type LessonLoad<T> = { status: 'idle' | 'loading' | 'ok' | 'missing' | 'error'; data?: T; error?: string }

const caches = new WeakMap<Api, Map<string, Promise<unknown>>>()

function cached<T>(api: Api, key: string, load: () => Promise<T>): Promise<T> {
  let cache = caches.get(api)
  if (!cache) caches.set(api, (cache = new Map()))
  let p = cache.get(key) as Promise<T> | undefined
  if (!p) {
    p = load()
    cache.set(key, p)
    // A failure is not kept, so the next open tries again.
    p.catch(() => cache.delete(key))
  }
  return p
}

function useLoad<T>(key: string | null, load: ((api: Api) => Promise<T>) | null): LessonLoad<T> {
  const api = useApp((s) => s.api)
  const [state, setState] = useState<{ key: string; value: LessonLoad<T> } | null>(null)
  useEffect(() => {
    if (!api || !key || !load) return
    let cancelled = false
    cached(api, key, () => load(api)).then(
      (data) => !cancelled && setState({ key, value: { status: 'ok', data } }),
      (err: unknown) => {
        if (cancelled) return
        const missing = err instanceof ApiError && err.status === 404
        setState({ key, value: missing ? { status: 'missing' } : { status: 'error', error: err instanceof Error ? err.message : String(err) } })
      },
    )
    return () => {
      cancelled = true
    }
    // `load` is keyed by `key`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, key])
  if (!key) return { status: 'idle' }
  return state?.key === key ? state.value : { status: 'loading' }
}

export function useLessonList(): LessonLoad<LessonList> {
  return useLoad('list', (api) => api.lessons())
}

export function useLesson(slug: string | null): LessonLoad<Lesson> {
  return useLoad(slug ? `lesson:${slug}` : null, slug ? (api) => api.lesson(slug) : null)
}

export function useLessonSource(slug: string | null, path: string | null): LessonLoad<LessonSource> {
  const key = slug && path ? `source:${slug}:${path}` : null
  return useLoad(key, slug && path ? (api) => api.lessonSource(slug, path) : null)
}

/** The chapters in reading order, from the diagram and the served list. */
export function useOutline(): { outline: OutlineEntry[]; status: LessonLoad<LessonList>['status'] } {
  const diagram = useApp((s) => s.diagram)
  const list = useLessonList()
  const lessons = list.data?.lessons
  const outline = useMemo(() => buildOutline(diagram, lessons ?? []), [diagram, lessons])
  return { outline, status: list.status }
}

/** Open a chapter: select its node (the expert view for a group chapter) on the Lessons tab. */
export function openChapter(entry: OutlineEntry): void {
  const s = useApp.getState()
  if (!entry.nodeId) return
  if (entry.view === 'expert' && s.view !== 'expert') s.setView('expert')
  s.openInspector(entry.nodeId, LESSONS_TAB)
}

let focusPending = false

/**
 * Move focus to the chapter's title once the Lessons tab shows it (the drawer closed after
 * opening a chapter). Focuses it now if it is already there.
 */
export function requestLessonFocus(): void {
  const title = document.getElementById('lesson-title')
  if (title) title.focus()
  else focusPending = true
}

/** True once after `requestLessonFocus` found no title yet: the tab focuses its title then. */
export function takeLessonFocus(): boolean {
  const pending = focusPending
  focusPending = false
  return pending
}

/** The inspector tab value of the Lessons tab. */
export const LESSONS_TAB = 'lessons'
