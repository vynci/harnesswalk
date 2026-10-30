import { useEffect, useSyncExternalStore } from 'react'
import { useApp } from '@/state/store'

const QUERY = '(prefers-color-scheme: dark)'

function subscribe(onChange: () => void) {
  const media = globalThis.matchMedia?.(QUERY)
  media?.addEventListener('change', onChange)
  return () => media?.removeEventListener('change', onChange)
}

const osDark = () => !!globalThis.matchMedia?.(QUERY).matches

/**
 * Whether the page is dark: the saved choice, or the OS preference until one is saved (the
 * stored 'system', also what index.html reads before first paint so there is no flash).
 */
export function useDarkTheme(): boolean {
  const theme = useApp((s) => s.theme)
  const os = useSyncExternalStore(subscribe, osDark, () => false)
  return theme === 'dark' || (theme !== 'light' && os)
}

/** Keeps the `dark` class on <html> in step with the chosen theme and the OS preference. */
export function useThemeClass() {
  const dark = useDarkTheme()
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark)
    document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  }, [dark])
}
