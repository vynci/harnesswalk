import { useEffect, useState } from 'react'
import { useApp } from '@/state/store'
import { sandboxPlan } from './sandbox'

/** Refetch the capabilities this often while the tab is visible, in public mode. */
const POLL_MS = 60_000

/**
 * Public mode only: keeps the capabilities fresh (requests left, the shared budget, the session
 * expiry) every minute while the tab is visible and again when it comes back, warns five
 * minutes before the sandbox session ends, and resets the chat when it has.
 */
export function useSandbox() {
  const isPublic = useApp((s) => s.capabilities?.mode === 'public')
  const expiresAt = useApp((s) => s.capabilities?.public?.session_expires_at ?? null)

  useEffect(() => {
    if (!isPublic) return
    const refresh = () => {
      if (document.visibilityState === 'visible') void useApp.getState().refreshCapabilities()
    }
    const timer = setInterval(refresh, POLL_MS)
    document.addEventListener('visibilitychange', refresh)
    return () => {
      clearInterval(timer)
      document.removeEventListener('visibilitychange', refresh)
    }
  }, [isPublic])

  useEffect(() => {
    if (!isPublic || !expiresAt) return
    const { warnIn, expireIn } = sandboxPlan(expiresAt, Date.now())
    const timers: ReturnType<typeof setTimeout>[] = []
    if (warnIn !== null && expireIn !== null && expireIn > 0) timers.push(setTimeout(() => useApp.getState().warnSandbox(expiresAt), warnIn))
    if (expireIn !== null) timers.push(setTimeout(() => useApp.getState().expireSandbox(expiresAt), expireIn))
    return () => timers.forEach(clearTimeout)
  }, [isPublic, expiresAt])
}

/** The current time, ticking every `ms` (for countdowns). */
export function useNow(ms = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(timer)
  }, [ms])
  return now
}
