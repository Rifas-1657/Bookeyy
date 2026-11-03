/**
 * Voice audition helpers for /admin/tutor-settings.
 *
 *   GET  {API}/api/voice/voices                 → voice list (array or {voices: [...]})
 *   POST {API}/api/voice/preview {text,lang,voice} → audio bytes, or JSON with base64 `audio`
 *
 * Uses the session token when NEXT_PUBLIC_BOOKEY_API_URL is set; on localhost
 * it falls back to the default backend port (same rule as lib/api/client.ts).
 */

import { API_BASE_URL, authorizedFetch } from '@/lib/api/auth'

export type AuditionLang = 'tanglish' | 'english'

export interface TutorVoice {
  id: string
  label: string
  languages: AuditionLang[]
  gender?: string
  description?: string
}

export const AUDITION_LINES: Record<AuditionLang, string> = {
  tanglish: 'Vanakkam! Naan unga tutor. Indha chapter la loops pathi simple-a paakalaam — doubt irundha, eppo venaalum kelunga.',
  english: 'Hello! I’m your tutor. In this chapter we’ll explore loops step by step — stop me anytime and ask a question.',
}

function voiceFetch(path: string, init?: RequestInit): Promise<Response> {
  if (API_BASE_URL === null) return Promise.reject(new Error('API_URL_NOT_SET'))
  return authorizedFetch(path, init)
}

export function voiceApiAvailable() {
  return API_BASE_URL !== null
}

type Raw = Record<string, unknown>

function langsOf(v: Raw): AuditionLang[] {
  const raw = [v.lang, v.language, v.languages, v.langs].flat().filter(Boolean).map((x) => String(x).toLowerCase())
  const out = new Set<AuditionLang>()
  for (const l of raw) {
    if (l.includes('tanglish') || l.startsWith('ta')) out.add('tanglish')
    if (l.includes('english') || l.startsWith('en')) out.add('english')
  }
  return out.size ? [...out] : ['tanglish', 'english']
}

export async function listVoices(signal?: AbortSignal): Promise<TutorVoice[]> {
  const res = await voiceFetch('/api/voice/voices', { signal })
  if (!res.ok) throw new Error(`Voices unavailable (${res.status})`)
  const data = (await res.json()) as unknown
  const list = (Array.isArray(data) ? data : ((data as Raw)?.voices as unknown[]) ?? []) as unknown[]
  const voices = list.map((item): TutorVoice => {
    if (typeof item === 'string') return { id: item, label: item, languages: ['tanglish', 'english'] }
    const v = item as Raw
    const id = String(v.id ?? v.voice ?? v.name ?? '')
    return {
      id,
      label: String(v.label ?? v.display_name ?? v.displayName ?? v.name ?? id),
      languages: langsOf(v),
      gender: v.gender ? String(v.gender).toLowerCase() : undefined,
      description: v.description ? String(v.description) : v.style ? String(v.style) : undefined,
    }
  })
  // The tutor is a female voice; keep unlabelled voices in case the backend omits gender.
  return voices.filter((v) => v.id && (!v.gender || v.gender.startsWith('f')))
}

/** Returns a playable Blob for either a binary or a JSON/base64 response. */
export async function previewVoice(text: string, lang: AuditionLang, voice: string, signal?: AbortSignal): Promise<Blob> {
  const res = await voiceFetch('/api/voice/preview', { method: 'POST', body: JSON.stringify({ text, lang, voice }), signal })
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText)
    throw new Error(`Preview failed (${res.status}): ${detail.slice(0, 160)}`)
  }
  const type = res.headers.get('Content-Type') ?? ''
  if (type.includes('application/json')) {
    const data = (await res.json()) as Raw
    const b64 = String(data.audio ?? data.audio_base64 ?? data.audioBase64 ?? '')
    if (!b64) throw new Error('Preview response had no audio')
    const clean = b64.replace(/^data:[^,]+,/, '')
    const bytes = Uint8Array.from(atob(clean), (c) => c.charCodeAt(0))
    const mime = String(data.mime_type ?? data.mimeType ?? (b64.match(/^data:([^;,]+)/)?.[1] ?? 'audio/mpeg'))
    return new Blob([bytes], { type: mime })
  }
  return res.blob()
}

/** Offline stand-in: the browser's own speech synthesis, preferring a female voice. */
export function speakWithBrowser(text: string, lang: AuditionLang): SpeechSynthesisUtterance | null {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return null
  const utter = new SpeechSynthesisUtterance(text)
  const wanted = lang === 'tanglish' ? ['en-IN', 'ta-IN'] : ['en-IN', 'en-GB', 'en-US']
  const voices = window.speechSynthesis.getVoices()
  const female = /female|zira|heera|veena|samantha|google uk english female|aria|jenny|neerja/i
  utter.voice =
    voices.find((v) => wanted.includes(v.lang) && female.test(v.name)) ??
    voices.find((v) => wanted.includes(v.lang)) ??
    voices.find((v) => female.test(v.name)) ??
    null
  utter.lang = utter.voice?.lang ?? 'en-IN'
  utter.rate = 0.95
  window.speechSynthesis.cancel()
  window.speechSynthesis.speak(utter)
  return utter
}
