'use client'

import { useEffect } from 'react'

const EVERY_MS = 10 * 60 * 1000

/** The free backend sleeps when idle and needs ~45 s to wake: start that as soon as a page opens. */
export function BackendWake() {
  useEffect(() => {
    const url = process.env.NEXT_PUBLIC_BOOKEY_API_URL
    if (!url) return
    const ping = () => void fetch(`${url.replace(/\/+$/, '')}/health`, { mode: 'no-cors' }).catch(() => {})
    ping()
    const timer = window.setInterval(ping, EVERY_MS)
    return () => window.clearInterval(timer)
  }, [])
  return null
}
