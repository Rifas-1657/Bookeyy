/**
 * Session helpers for the FastAPI auth endpoints (`/auth/*`, `/api/ws-ticket`).
 *
 * The backend also sets httpOnly access/refresh cookies. The bearer token kept
 * here authenticates API calls and WebSocket tickets; when it expires (15 min)
 * `authorizedFetch` refreshes it once through the refresh cookie and retries.
 * "Remember me" keeps the session in localStorage, otherwise sessionStorage.
 *
 * Where requests go is decided by `./base-url` (one resolver for the whole
 * app). In same-origin mode the Next server proxies `/api`, `/auth`, `/me`
 * and `/search`, so the cookies are first-party; otherwise they only flow when
 * the API shares the site's registrable domain.
 */

import { useSyncExternalStore } from 'react'
import { API_BASE_URL, apiUrl, isBackendConfigured } from './base-url'

export { API_BASE_URL, apiUrl, isBackendConfigured }

const SESSION_KEY = 'bookey:auth:session'
const CHANGE_EVENT = 'bookey:auth-change'
/** Fired after an explicit logout so live resources (book client sockets) shut down. */
export const LOGOUT_EVENT = 'bookey:logout'

export type UserRole = 'admin' | 'learner'

export interface AuthSession {
  token: string
  userId: string
  role: UserRole
  displayName: string
  email?: string
}

interface AuthResponse {
  access_token: string
  user_id: string
  role: string
  display_name: string
  email?: string
}

function readStored(storage: Storage): AuthSession | null {
  const raw = storage.getItem(SESSION_KEY)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<AuthSession>
    if (typeof parsed.token !== 'string' || typeof parsed.userId !== 'string') return null
    return {
      token: parsed.token,
      userId: parsed.userId,
      role: parsed.role === 'admin' ? 'admin' : 'learner',
      displayName: typeof parsed.displayName === 'string' ? parsed.displayName : '',
    }
  } catch {
    return null
  }
}

let cachedRaw: string | null | undefined
let cachedSession: AuthSession | null = null

export function getSession(): AuthSession | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = sessionStorage.getItem(SESSION_KEY) ?? localStorage.getItem(SESSION_KEY)
    // Stable identity between reads so useSyncExternalStore doesn't loop.
    if (raw !== cachedRaw) {
      cachedRaw = raw
      cachedSession = readStored(sessionStorage) ?? readStored(localStorage)
    }
    return cachedSession
  } catch {
    return null
  }
}

export function getAuthToken(): string | null {
  return getSession()?.token ?? null
}

function isRemembered() {
  try {
    return localStorage.getItem(SESSION_KEY) !== null
  } catch {
    return false
  }
}

function storeSession(data: AuthResponse, remember: boolean) {
  const session: AuthSession = {
    token: data.access_token,
    userId: data.user_id,
    role: data.role === 'admin' ? 'admin' : 'learner',
    displayName: data.display_name,
    email: data.email,
  }
  try {
    clearStored()
    ;(remember ? localStorage : sessionStorage).setItem(SESSION_KEY, JSON.stringify(session))
  } catch {
    // Storage blocked: the httpOnly cookie still carries the session.
  }
  window.dispatchEvent(new Event(CHANGE_EVENT))
}

function clearStored() {
  try {
    sessionStorage.removeItem(SESSION_KEY)
    localStorage.removeItem(SESSION_KEY)
  } catch {
    // ignore
  }
}

export function clearSession() {
  clearStored()
  if (typeof window !== 'undefined') window.dispatchEvent(new Event(CHANGE_EVENT))
}

function subscribe(onChange: () => void) {
  window.addEventListener(CHANGE_EVENT, onChange)
  window.addEventListener('storage', onChange)
  return () => {
    window.removeEventListener(CHANGE_EVENT, onChange)
    window.removeEventListener('storage', onChange)
  }
}

/** Current session; `null` during SSR or when signed out. */
export function useSession(): AuthSession | null {
  return useSyncExternalStore(subscribe, getSession, () => null)
}

export class AuthError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

async function errorFrom(res: Response): Promise<AuthError> {
  let message = res.statusText
  try {
    const data = (await res.json()) as { detail?: unknown }
    if (typeof data.detail === 'string') message = data.detail
  } catch {
    // keep statusText
  }
  return new AuthError(message, res.status)
}

function send(path: string, init: RequestInit = {}): Promise<Response> {
  if (API_BASE_URL === null) throw new Error('API_URL_NOT_SET')
  const token = getAuthToken()
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body !== undefined && !(init.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }
  return fetch(apiUrl(path), { credentials: 'include', ...init, headers })
}

let refreshing: Promise<boolean> | null = null

/** Trades the refresh cookie for a new access token. Single-flight. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const res = await fetch(apiUrl('/auth/refresh'), { method: 'POST', credentials: 'include' })
      if (!res.ok) {
        clearSession()
        return false
      }
      storeSession((await res.json()) as AuthResponse, isRemembered())
      return true
    } catch {
      return false
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

/** Fetch against the API with the session token, refreshing once on 401. */
export async function authorizedFetch(path: string, init?: RequestInit): Promise<Response> {
  const res = await send(path, init)
  if (res.status !== 401 || path.startsWith('/auth/')) return res
  if (!(await refreshSession())) return res
  return send(path, init)
}

/** JSON helper: throws `AuthError` for non-2xx responses; `undefined` for 204. */
export async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await authorizedFetch(path, init)
  if (!res.ok) throw await errorFrom(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export async function login(email: string, password: string, remember: boolean) {
  const res = await send('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) })
  if (!res.ok) throw await errorFrom(res)
  const data = (await res.json()) as AuthResponse
  storeSession(data, remember)
  return { role: (data.role === 'admin' ? 'admin' : 'learner') as UserRole }
}

export async function signup(input: { email: string; password: string; displayName: string }) {
  const res = await send('/auth/signup', {
    method: 'POST',
    body: JSON.stringify({
      email: input.email,
      password: input.password,
      display_name: input.displayName,
    }),
  })
  if (!res.ok) throw await errorFrom(res)
  storeSession((await res.json()) as AuthResponse, true)
}

export async function requestPasswordReset(email: string) {
  const res = await send('/auth/password-reset/request', { method: 'POST', body: JSON.stringify({ email }) })
  if (!res.ok) throw await errorFrom(res)
}

export async function confirmPasswordReset(token: string, newPassword: string) {
  const res = await send('/auth/password-reset/confirm', {
    method: 'POST',
    body: JSON.stringify({ token, new_password: newPassword }),
  })
  if (!res.ok) throw await errorFrom(res)
}

export async function logout() {
  try {
    await send('/auth/logout', { method: 'POST' })
  } catch {
    // Offline: still end the local session.
  } finally {
    clearSession()
  }
}

/** Single-use ticket the WebSocket gateway requires as its first frame. */
export async function fetchWsTicket(): Promise<string> {
  const data = await apiJson<{ ticket: string }>('/api/ws-ticket', { method: 'POST' })
  return data.ticket
}

/** Same-origin path from `?next=`, or the fallback. Blocks open redirects. */
export function safeNextPath(fallback = '/dashboard'): string {
  if (typeof window === 'undefined') return fallback
  const next = new URLSearchParams(window.location.search).get('next')
  return next && next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/\\') ? next : fallback
}
