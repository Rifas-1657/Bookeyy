'use client'

import { LOGOUT_EVENT, logout } from '@/lib/api/auth'
import { clearOfflineData } from '@/lib/offline/db'
import { clearAppStorage } from '@/lib/storage'

const STORAGE_PREFIX = 'bookey:v1:'
/** Cache Storage buckets written by public/sw.js. */
const SW_CACHE_PREFIX = 'bookey-'

function clearSessionStoragePrefix(prefix: string) {
  try {
    const owned: string[] = []
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const key = sessionStorage.key(i)
      if (key?.startsWith(prefix)) owned.push(key)
    }
    owned.forEach((key) => sessionStorage.removeItem(key))
  } catch {
    /* storage unavailable */
  }
}

async function clearServiceWorkerCaches() {
  if (typeof caches === 'undefined') return
  try {
    const keys = await caches.keys()
    await Promise.all(keys.filter((key) => key.startsWith(SW_CACHE_PREFIX)).map((key) => caches.delete(key)))
  } catch {
    /* ignore */
  }
}

/**
 * Ends the session and removes everything this device kept for the learner:
 * the live book client (sockets), `bookey:v1:*` local/session storage, the
 * offline IndexedDB (books, audio, captures, activity) and service-worker
 * caches. Then reloads into `redirectTo` so no in-memory store survives.
 */
export async function logoutAndClear(redirectTo = '/login') {
  await logout()
  if (typeof window === 'undefined') return
  window.dispatchEvent(new Event(LOGOUT_EVENT))
  clearAppStorage()
  clearSessionStoragePrefix(STORAGE_PREFIX)
  await Promise.all([clearOfflineData(), clearServiceWorkerCaches()])
  window.location.replace(redirectTo)
}
