'use client'

import { useEffect } from 'react'

/**
 * Registers `public/sw.js` (app shell, narration audio and Pyodide caching).
 * Skipped in development so hot reload never serves stale chunks, unless
 * NEXT_PUBLIC_BOOKEY_SW=on is set to test offline mode locally.
 */
export function ServiceWorkerRegister() {
  useEffect(() => {
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return
    const enabled = process.env.NODE_ENV === 'production' || process.env.NEXT_PUBLIC_BOOKEY_SW === 'on'
    if (!enabled) return
    // The build id versions the worker's caches; a new deploy installs a new worker.
    const version = encodeURIComponent(process.env.NEXT_PUBLIC_BOOKEY_BUILD_ID || 'dev')
    const register = () => void navigator.serviceWorker.register(`/sw.js?v=${version}`, { scope: '/' }).catch(() => {})
    if (document.readyState === 'complete') register()
    else window.addEventListener('load', register, { once: true })
  }, [])
  return null
}
