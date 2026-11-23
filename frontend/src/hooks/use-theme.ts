import { useCallback, useEffect, useState } from 'react'

export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'fleet-rlm.theme'

function readStoredTheme(): ThemePreference {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY)
    if (value === 'light' || value === 'dark' || value === 'system') return value
  } catch {
    // Ignore storage read failures (e.g. blocked site storage).
  }
  return 'system'
}

function getSystemTheme(): ResolvedTheme {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function applyThemeToDocument(preference: ThemePreference, resolved: ResolvedTheme) {
  document.documentElement.classList.toggle('dark', resolved === 'dark')
  const meta = document.querySelector<HTMLMetaElement>('meta[name="color-scheme"]')
  if (meta) {
    meta.content = preference === 'system' ? 'light dark' : preference
  }
}

export function useTheme() {
  const [theme, setThemeState] = useState<ThemePreference>(() => readStoredTheme())
  const [systemTheme, setSystemTheme] = useState<ResolvedTheme>(() => getSystemTheme())
  const resolvedTheme: ResolvedTheme = theme === 'system' ? systemTheme : theme

  useEffect(() => {
    applyThemeToDocument(theme, resolvedTheme)
  }, [theme, resolvedTheme])

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)')
    const onMediaChange = (event: MediaQueryListEvent) => {
      setSystemTheme(event.matches ? 'dark' : 'light')
    }
    const onStorage = (event: StorageEvent) => {
      if (event.key === THEME_STORAGE_KEY || event.key === null) {
        setThemeState(readStoredTheme())
      }
    }
    media.addEventListener('change', onMediaChange)
    window.addEventListener('storage', onStorage)
    return () => {
      media.removeEventListener('change', onMediaChange)
      window.removeEventListener('storage', onStorage)
    }
  }, [])

  const setTheme = useCallback((next: ThemePreference) => {
    setThemeState(next)
    try {
      if (next === 'system') window.localStorage.removeItem(THEME_STORAGE_KEY)
      else window.localStorage.setItem(THEME_STORAGE_KEY, next)
    } catch {
      // Theme still applies in memory even if persistence is blocked.
    }
  }, [])

  const toggleTheme = useCallback(() => {
    const target: ResolvedTheme = resolvedTheme === 'dark' ? 'light' : 'dark'
    setTheme(target === systemTheme ? 'system' : target)
  }, [resolvedTheme, systemTheme, setTheme])

  return {
    theme,
    resolvedTheme,
    systemTheme,
    setTheme,
    toggleTheme,
  }
}
