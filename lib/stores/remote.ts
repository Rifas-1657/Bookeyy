'use client'

import { useEffect, useSyncExternalStore } from 'react'
import { apiJson, getSession } from '@/lib/api/auth'
import { notify } from '@/components/ui-kit/toast'

/**
 * Tiny cache for per-user server data (`/me/*`). One shared copy per
 * resource, loaded once per signed-in user, with optimistic mutations that
 * roll back and toast when the server rejects them.
 */

export interface RemoteState<T> {
  data: T
  status: 'idle' | 'loading' | 'ready' | 'error'
}

export interface RemoteResource<T> {
  get: () => RemoteState<T>
  set: (next: T) => void
  load: (force?: boolean) => Promise<void>
  subscribe: (listener: () => void) => () => void
  /** Applies `optimistic` now, runs `request`, and restores the previous data if it fails. */
  mutate: <R>(optimistic: (data: T) => T, request: () => Promise<R>, failure: string) => Promise<R | undefined>
}

export function createRemoteResource<T>(path: string, empty: T, parse: (raw: unknown) => T): RemoteResource<T> {
  let state: RemoteState<T> = { data: empty, status: 'idle' }
  let loadedFor: string | null = null
  let inflight: Promise<void> | null = null
  const listeners = new Set<() => void>()

  const emit = (next: RemoteState<T>) => {
    state = next
    listeners.forEach((listener) => listener())
  }

  const resource: RemoteResource<T> = {
    get: () => state,
    set: (data) => emit({ ...state, data }),
    subscribe: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    load: (force = false) => {
      const userId = getSession()?.userId ?? null
      if (!userId) {
        loadedFor = null
        if (state.status !== 'idle') emit({ data: empty, status: 'idle' })
        return Promise.resolve()
      }
      if (!force && loadedFor === userId) return inflight ?? Promise.resolve()
      // A different user signed in: never show the previous user's data.
      if (loadedFor !== userId) emit({ data: empty, status: 'loading' })
      loadedFor = userId
      inflight = apiJson<unknown>(path)
        .then((raw) => emit({ data: parse(raw), status: 'ready' }))
        .catch(() => emit({ ...state, status: 'error' }))
        .finally(() => {
          inflight = null
        })
      return inflight
    },
    mutate: async (optimistic, request, failure) => {
      const previous = state.data
      emit({ ...state, data: optimistic(previous) })
      try {
        return await request()
      } catch {
        emit({ ...state, data: previous })
        notify.error(failure, 'Your change was not saved. Check your connection and try again.')
        return undefined
      }
    },
  }
  return resource
}

/** Subscribes to a resource and loads it once when `enabled`. */
export function useRemote<T>(resource: RemoteResource<T>, enabled: boolean): RemoteState<T> {
  const state = useSyncExternalStore(resource.subscribe, resource.get, resource.get)
  useEffect(() => {
    if (enabled) void resource.load()
  }, [enabled, resource])
  return state
}
