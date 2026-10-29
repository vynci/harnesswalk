import { useEffect } from 'react'
import { useApp } from '@/state/store'

/** Keeps the `dark` class on <html> in step with the chosen theme and the OS preference. */
export function useThemeClass() {
  const theme = useApp((s) => s.theme)
  useEffect(() => {
    const media = globalThis.matchMedia?.('(prefers-color-scheme: dark)')
    const apply = () => {
      const dark = theme === 'dark' || (theme === 'system' && !!media?.matches)
      document.documentElement.classList.toggle('dark', dark)
      document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
    }
    apply()
    media?.addEventListener('change', apply)
    return () => media?.removeEventListener('change', apply)
  }, [theme])
}
