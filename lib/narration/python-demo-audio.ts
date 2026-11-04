/**
 * Mapping of Python demo pages to prebuilt narration audio in both tutor voices:
 * male (ElevenLabs "Roger" recordings) at the folder root, female (local Pocket-TTS,
 * backend/scripts/prerender_audio.py) under `female/`. A sibling `.json`, when present,
 * holds display-word timings, so playback needs no live TTS call.
 */
import type { VoiceGender } from '@/lib/stores/reader-preferences'


export const PYTHON_DEMO_AUDIO_MAP: Record<string, string> = {}

/**
 * Returns the public path to the prebuilt audio asset for a given course and page,
 * or null if no prebuilt asset exists (e.g. other courses or un-prebuilt pages).
 */
export function getPrebuiltAudioUrl(courseId: string, pageId: string, gender: VoiceGender = 'male'): string | null {
  if (courseId.toLowerCase() !== 'python') return null
  const url = PYTHON_DEMO_AUDIO_MAP[pageId]
  if (!url) return null
  return gender === 'female' ? url.replace('/audio/python-demo/', '/audio/python-demo/female/') : url
}

/** Word-timing file rendered next to a prebuilt mp3 (`page-01.mp3` → `page-01.json`). */
export function getPrebuiltTimingsUrl(courseId: string, pageId: string, gender: VoiceGender = 'male'): string | null {
  const audio = getPrebuiltAudioUrl(courseId, pageId, gender)
  return audio ? audio.replace(/\.mp3$/, '.json') : null
}

export async function loadPrebuiltTimings(courseId: string, pageId: string, gender: VoiceGender = 'male') {
  const url = getPrebuiltTimingsUrl(courseId, pageId, gender)
  if (!url) return undefined
  try {
    const res = await fetch(url)
    if (!res.ok) return undefined
    const data = (await res.json()) as { wordTimings?: Array<{ word: string; startMs: number; endMs: number; wordIndex?: number }> }
    return data.wordTimings?.length ? data.wordTimings : undefined
  } catch {
    return undefined
  }
}
