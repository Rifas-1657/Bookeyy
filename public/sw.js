/* Bookey service worker: offline app shell, narration audio and Pyodide. */

// Registered as /sw.js?v=<build id> (components/reader/service-worker-register.tsx),
// so every deploy installs a new worker and drops the previous build's pages.
const BUILD = new URL(self.location.href).searchParams.get('v') || 'dev'
const VERSION = `bookey-${BUILD}`
const PAGES = `${VERSION}-pages`
const RSC = `${VERSION}-rsc`
const RUNTIME = `${VERSION}-runtime`
// Audio and the Pyodide runtime are big and don't change per build: keep them across deploys.
const MEDIA = 'bookey-media-v1'
const KEEP = new Set([MEDIA])
const PYODIDE_ORIGIN = 'https://cdn.jsdelivr.net'

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(PAGES)
      .then((cache) => cache.addAll(['/', '/manifest.webmanifest', '/icon.svg']).catch(() => {}))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((k) => k.startsWith('bookey-') && !k.startsWith(VERSION) && !KEEP.has(k)).map((k) => caches.delete(k)),
        ),
      )
      .then(() => self.clients.claim()),
  )
})

/** Never let a failed cache write (quota, opaque body) break a good network response. */
async function safePut(cacheName, key, response) {
  try {
    const cache = await caches.open(cacheName)
    await cache.put(key, response)
  } catch {
    /* ignore */
  }
}

// The reader asks us to keep specific URLs (book page, mp3s, Pyodide files).
self.addEventListener('message', (event) => {
  const data = event.data || {}
  if (data.type !== 'bookey:precache' || !Array.isArray(data.urls)) return
  event.waitUntil(
    Promise.all(
      data.urls.map(async (url) => {
        const cacheName = url.startsWith(PYODIDE_ORIGIN) || /\.(mp3|wav|ogg)$/.test(url) ? MEDIA : PAGES
        try {
          const cache = await caches.open(cacheName)
          if (await cache.match(url)) return
          const res = await fetch(url, { mode: url.startsWith('/') ? 'same-origin' : 'cors', credentials: 'omit' })
          if (res.ok) await safePut(cacheName, url, res)
        } catch {
          /* offline: skip */
        }
      }),
    ),
  )
})

const isApi = (url) =>
  url.pathname.startsWith('/api/') ||
  url.pathname.startsWith('/ws/') ||
  url.pathname.startsWith('/auth/') ||
  url.pathname.startsWith('/me/')

const isRsc = (request, url) => request.headers.get('RSC') === '1' || url.searchParams.has('_rsc')

self.addEventListener('fetch', (event) => {
  const { request } = event
  if (request.method !== 'GET') return
  const url = new URL(request.url)

  // Pyodide runtime + packages: immutable per version → cache first.
  if (url.origin === PYODIDE_ORIGIN && url.pathname.startsWith('/pyodide/')) {
    event.respondWith(cacheFirst(request, MEDIA))
    return
  }
  if (url.origin !== self.location.origin || isApi(url)) return
  // JSON search API (same-origin proxy) is never cached.
  if (url.pathname === '/search' && (request.headers.get('accept') || '').includes('application/json')) return

  // Narration audio isn't content-hashed: serve the cached copy, refresh it in the background.
  if (url.pathname.startsWith('/audio/')) {
    event.respondWith(staleWhileRevalidate(event, request, MEDIA, true))
    return
  }
  // Hashed build assets never change.
  if (url.pathname.startsWith('/_next/static/')) {
    event.respondWith(cacheFirst(request, RUNTIME))
    return
  }
  // Unhashed public assets (fonts, icons, manifest): fast, but pick up edits.
  if (/\.(woff2?|svg|png|ico|webmanifest|json)$/.test(url.pathname)) {
    event.respondWith(staleWhileRevalidate(event, request, RUNTIME, false))
    return
  }
  // RSC payloads and HTML navigations: network first, each in its own cache so an
  // HTML page is never served to the router as an RSC payload (or vice versa).
  if (isRsc(request, url)) {
    event.respondWith(networkFirst(request, RSC, false))
    return
  }
  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, PAGES, true))
  }
})

async function cacheFirst(request, cacheName) {
  try {
    const hit = await caches.match(request, { cacheName })
    if (hit) return hit
  } catch {
    /* cache unavailable: go to network */
  }
  let res
  try {
    res = await fetch(request)
  } catch {
    return new Response('', { status: 504, statusText: 'Offline' })
  }
  if (res.ok && res.status === 200) await safePut(cacheName, request, res.clone())
  return res
}

async function staleWhileRevalidate(event, request, cacheName, stripRange) {
  // Audio is cached whole (no Range) and sliced on the way out.
  const key = stripRange ? request.url : request
  const range = stripRange ? request.headers.get('range') : null
  let hit
  try {
    hit = await caches.match(key, { cacheName, ignoreSearch: stripRange })
  } catch {
    hit = undefined
  }
  const refresh = (async () => {
    try {
      const res = await fetch(stripRange ? new Request(request.url) : request)
      if (res.ok && res.status === 200) await safePut(cacheName, key, res.clone())
      return res
    } catch {
      return null
    }
  })()
  if (hit) {
    event.waitUntil(refresh)
    return range ? rangeResponse(hit, range) : hit
  }
  const res = await refresh
  if (!res) return new Response('', { status: 504, statusText: 'Offline' })
  if (range && res.ok) return rangeResponse(res, range)
  return res
}

async function rangeResponse(response, range) {
  const buffer = await response.clone().arrayBuffer()
  const match = /bytes=(\d+)-(\d*)/.exec(range || '')
  if (!match) return response
  const start = Number(match[1])
  const end = match[2] ? Math.min(Number(match[2]), buffer.byteLength - 1) : buffer.byteLength - 1
  return new Response(buffer.slice(start, end + 1), {
    status: 206,
    headers: {
      'Content-Type': response.headers.get('Content-Type') || 'audio/mpeg',
      'Content-Range': `bytes ${start}-${end}/${buffer.byteLength}`,
      'Content-Length': String(end - start + 1),
      'Accept-Ranges': 'bytes',
    },
  })
}

async function networkFirst(request, cacheName, html) {
  let res
  try {
    res = await fetch(request)
  } catch {
    res = null
  }
  if (res) {
    if (res.ok) await safePut(cacheName, request, res.clone())
    return res
  }
  try {
    const cache = await caches.open(cacheName)
    const hit = (await cache.match(request)) || (html ? await cache.match(request, { ignoreSearch: true }) : undefined)
    if (hit) return hit
    if (html) {
      const shell = await cache.match('/')
      if (shell) return shell
    }
  } catch {
    /* fall through */
  }
  // An RSC miss must not get HTML: a 504 makes the router fall back to a full navigation.
  if (!html) return new Response('', { status: 504, statusText: 'Offline' })
  return new Response('<h1>Offline</h1><p>Open a book you saved for offline.</p>', {
    headers: { 'Content-Type': 'text/html' },
  })
}
