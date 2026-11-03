/**
 * The one place that decides where REST calls go.
 *
 * - `NEXT_PUBLIC_BOOKEY_API_URL` unset → no backend (`null`): mock data,
 *   browser speech recognition, etc.
 * - Same-origin mode (`NEXT_PUBLIC_BOOKEY_SAME_ORIGIN=1`, default on in
 *   development) → `''`: calls use relative paths (`/api/...`, `/auth/...`)
 *   that `next.config.mjs` rewrites to the backend, so auth cookies are
 *   first-party and localhost vs 127.0.0.1 no longer matters.
 * - Otherwise → the absolute backend URL (cross-origin, needs CORS).
 *
 * WebSockets stay direct (`NEXT_PUBLIC_BOOKEY_WS_URL`). Kept import-free so
 * `node --test` can load it.
 *
 * Note: `API_BASE_URL` can be `''` (a valid relative base), so test for
 * `=== null`, never for truthiness.
 */

export function resolveApiBase(configuredUrl: string | undefined | null, sameOriginFlag: string | undefined, nodeEnv: string | undefined): string | null {
  const configured = configuredUrl?.trim().replace(/\/+$/, '') || null
  if (configured === null) return null
  const flag = sameOriginFlag?.trim().toLowerCase()
  const sameOrigin = flag === undefined || flag === '' ? nodeEnv === 'development' : flag === '1' || flag === 'true' || flag === 'on'
  return sameOrigin ? '' : configured
}

// Literal `process.env.NEXT_PUBLIC_*` reads so Next inlines them in the client bundle.
export const API_BASE_URL: string | null = resolveApiBase(
  process.env.NEXT_PUBLIC_BOOKEY_API_URL,
  process.env.NEXT_PUBLIC_BOOKEY_SAME_ORIGIN,
  process.env.NODE_ENV,
)

/** True when a backend is configured (same answer on the server and in the browser). */
export function isBackendConfigured(): boolean {
  return API_BASE_URL !== null
}

/** Full URL for an API path (`/api/...`); relative in same-origin mode. */
export function apiUrl(path: string): string {
  return `${API_BASE_URL ?? ''}${path}`
}
