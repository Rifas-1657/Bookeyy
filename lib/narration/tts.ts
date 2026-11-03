import type { TutorLanguage, WordTiming } from '@/lib/contracts/book-events'
import { authorizedFetch } from '@/lib/api/auth'
import { isApiConfigured } from '@/lib/api/client'
import { useReaderPreferences, type VoiceGender } from '@/lib/stores/reader-preferences'

export interface TtsClip {
  blob: Blob
  mime: string
  timings?: WordTiming[]
  durationMs?: number
}

export interface TtsOptions {
  lang?: TutorLanguage
  voice?: string
  /** Tutor voice; defaults to the learner's saved choice. */
  gender?: VoiceGender
  speed?: number
  signal?: AbortSignal
}

/** Most-recently-used clips are kept; older ones are evicted (Map preserves insertion order). */
const CACHE_LIMIT = 40
const cache = new Map<string, Promise<TtsClip>>()
let cooldownUntil = 0

/** Errors the caller may retry later (rate limit / cool-down), as opposed to "this voice can't work". */
export class TtsRetryableError extends Error {
  readonly retryable = true
}

function remember(key: string, value: Promise<TtsClip>) {
  cache.delete(key)
  cache.set(key, value)
  while (cache.size > CACHE_LIMIT) {
    const oldest = cache.keys().next().value
    if (oldest === undefined) break
    cache.delete(oldest)
  }
}

/** Resolves like `promise`, but rejects early with AbortError when this caller's signal aborts. */
function withSignal<T>(promise: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return promise
  if (signal.aborted) return Promise.reject(new DOMException('Aborted', 'AbortError'))
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => reject(new DOMException('Aborted', 'AbortError'))
    signal.addEventListener('abort', onAbort, { once: true })
    promise.then(
      (value) => {
        signal.removeEventListener('abort', onAbort)
        resolve(value)
      },
      (error) => {
        signal.removeEventListener('abort', onAbort)
        reject(error)
      },
    )
  })
}

export function ttsAvailable() {
  return isApiConfigured()
}

function base64ToBlob(base64: string, mime: string) {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return new Blob([bytes], { type: mime })
}

type Obj = Record<string, unknown>

function readTimings(raw: unknown): WordTiming[] | undefined {
  if (!Array.isArray(raw)) return undefined
  const out: WordTiming[] = []
  for (const entry of raw as Obj[]) {
    if (!entry || typeof entry !== 'object') continue
    const word = String(entry.word ?? entry.text ?? '')
    const startMs = Number(entry.startMs ?? entry.start_ms ?? entry.start ?? NaN)
    const endMs = Number(entry.endMs ?? entry.end_ms ?? entry.end ?? NaN)
    const wordIndex = entry.wordIndex ?? entry.word_index
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs)) continue
    out.push({ word, startMs, endMs, wordIndex: typeof wordIndex === 'number' ? wordIndex : undefined })
  }
  return out.length ? out : undefined
}

/**
 * POST {API}/api/voice/tts. Asks for JSON (`?format=json`) so word timings can
 * come back with the audio, but also accepts a binary audio response (with an
 * optional `X-Word-Timings` JSON header). Results are memoised per text (LRU,
 * CACHE_LIMIT clips). The shared request is never tied to one caller's
 * AbortSignal: aborting only rejects that caller.
 */
export function synthesize(text: string, options: TtsOptions = {}): Promise<TtsClip> {
  const trimmed = text.trim()
  const gender = options.gender ?? useReaderPreferences.getState().voiceGender
  const key = `${options.lang ?? ''}|${gender}|${options.voice ?? ''}|${options.speed ?? 1}|${trimmed}`
  const hit = cache.get(key)
  if (hit) {
    remember(key, hit)
    return withSignal(hit, options.signal)
  }
  if (!trimmed) return Promise.reject(new Error('empty text'))
  if (Date.now() < cooldownUntil) return Promise.reject(new TtsRetryableError('tts cooling down'))

  const request = (async () => {
    const res = await authorizedFetch('/api/voice/tts?format=json', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json, audio/*' },
      body: JSON.stringify({ text: trimmed, lang: options.lang, voice: options.voice, gender, speed: options.speed ?? 1 }),
    })
    if (res.status === 429) {
      cooldownUntil = Date.now() + 5000
      throw new TtsRetryableError('tts 429')
    }
    if (!res.ok) throw new Error(`tts ${res.status}`)
    const type = res.headers.get('content-type') ?? ''
    if (type.includes('json')) {
      const body = (await res.json()) as Obj
      const base64 = String(body.audio_base64 ?? body.audioBase64 ?? body.audio ?? body.data ?? '')
      const mime = String(body.mime_type ?? body.mimeType ?? body.mime ?? 'audio/wav')
      if (!base64) throw new Error('tts: empty audio')
      return {
        blob: base64ToBlob(base64, mime),
        mime,
        timings: readTimings(body.wordTimings ?? body.word_timings ?? body.timings),
        durationMs: typeof body.duration_ms === 'number' ? body.duration_ms : undefined,
      }
    }
    const blob = await res.blob()
    let timings: WordTiming[] | undefined
    const header = res.headers.get('x-word-timings')
    if (header) {
      try {
        timings = readTimings(JSON.parse(header))
      } catch {
        /* ignore malformed header */
      }
    }
    return { blob, mime: blob.type || type || 'audio/mpeg', timings }
  })()

  remember(key, request)
  request.catch(() => {
    if (cache.get(key) === request) cache.delete(key)
  })
  return withSignal(request, options.signal)
}
