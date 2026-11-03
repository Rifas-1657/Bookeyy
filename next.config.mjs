import { fileURLToPath } from 'node:url'

/**
 * Where same-origin API calls are proxied (see lib/api/base-url.ts). Server-side
 * only. Defaults to the public API URL, then the local backend. 127.0.0.1 (not
 * "localhost") so Node never tries ::1 first.
 */
const apiOrigin = (
  process.env.BOOKEY_API_ORIGIN ||
  process.env.NEXT_PUBLIC_BOOKEY_API_URL ||
  'http://127.0.0.1:8000'
).replace(/\/+$/, '')

/**
 * Basic hardening. Scripts allow jsDelivr (Pyodide runtime, Monaco), blob:
 * workers (code sandbox) and WebAssembly. 'unsafe-inline'/'unsafe-eval' remain
 * because Next's inline bootstrap, dev tooling, Pyodide and the JS practical
 * runner need them; there is no default-src so API/WebSocket/media hosts keep working.
 */
const contentSecurityPolicy = [
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' blob: https://cdn.jsdelivr.net",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
].join('; ')

const securityHeaders = [
  { key: 'Content-Security-Policy', value: contentSecurityPolicy },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'microphone=(self), camera=(), geolocation=(), payment=()' },
]

/** Versions the service worker (`/sw.js?v=`); fixed at build time. */
const buildId = process.env.BOOKEY_BUILD_ID || process.env.VERCEL_GIT_COMMIT_SHA || String(Date.now())

/** @type {import('next').NextConfig} */
const nextConfig = {
  env: {
    NEXT_PUBLIC_BOOKEY_BUILD_ID: buildId,
  },
  images: {
    unoptimized: true,
  },
  // A stray package.json/lockfile in a parent folder makes Turbopack pick the wrong root,
  // which breaks next/font resolution in dev. Pin it to this project.
  turbopack: {
    root: fileURLToPath(new URL('.', import.meta.url)),
  },
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }]
  },
  // Same-origin REST (NEXT_PUBLIC_BOOKEY_SAME_ORIGIN): the browser calls /api, /auth, /me
  // and /search on this origin, so auth cookies are first-party. WebSockets stay direct.
  async rewrites() {
    return {
      beforeFiles: [
        // /search is also a page: only JSON fetches (not navigations / RSC) go to the API.
        {
          source: '/search',
          has: [{ type: 'header', key: 'accept', value: '.*application/json.*' }],
          missing: [{ type: 'header', key: 'rsc' }],
          destination: `${apiOrigin}/search`,
        },
      ],
      afterFiles: [
        { source: '/api/:path*', destination: `${apiOrigin}/api/:path*` },
        { source: '/auth/:path*', destination: `${apiOrigin}/auth/:path*` },
        { source: '/me/:path*', destination: `${apiOrigin}/me/:path*` },
      ],
      fallback: [],
    }
  },
}

export default nextConfig
