'use client'

import { useSyncExternalStore } from 'react'

export type LuxTheme = 'light' | 'dark'

function read(): LuxTheme {
  const c = document.documentElement.classList
  if (c.contains('dark')) return 'dark'
  if (c.contains('light')) return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function subscribe(cb: () => void) {
  const mql = window.matchMedia('(prefers-color-scheme: dark)')
  mql.addEventListener('change', cb)
  const observer = new MutationObserver(cb)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
  return () => {
    mql.removeEventListener('change', cb)
    observer.disconnect()
  }
}

/** Resolved light/dark theme for the marketing surface. */
export function useLuxTheme(): LuxTheme {
  return useSyncExternalStore(subscribe, read, () => 'light')
}

export function setLuxTheme(theme: LuxTheme) {
  const c = document.documentElement.classList
  c.toggle('dark', theme === 'dark')
  c.toggle('light', theme === 'light')
  try {
    localStorage.setItem('theme', theme)
  } catch {
    // storage blocked: the class still applies for this visit
  }
}
