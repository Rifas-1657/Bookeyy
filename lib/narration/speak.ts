'use client'

import type { TutorLanguage } from '@/lib/contracts/book-events'
import { useReaderPreferences, type VoiceGender } from '@/lib/stores/reader-preferences'
import { synthesize, ttsAvailable } from './tts'

export interface SpokenAnswer {
  /** Resolves when speech ends (or is stopped / unavailable). */
  done: Promise<void>
  stop: () => void
  getLevel: () => number
  /** True when real audio is being produced. */
  audible: boolean
}

const FEMALE_HINTS = /female|woman|zira|samantha|victoria|karen|moira|tessa|veena|heera|swara|neerja|aria|jenny|sonia|libby|google uk english female|pallavi/i
const MALE_HINTS = /male|man|david|mark|george|james|daniel|ravi|prabhat|valluvar|hemant|google uk english male/i

function pickBrowserVoice(lang: TutorLanguage, gender: VoiceGender): SpeechSynthesisVoice | null {
  if (typeof window === 'undefined' || !('speechSynthesis' in window)) return null
  const voices = window.speechSynthesis.getVoices()
  const hints = gender === 'male' ? MALE_HINTS : FEMALE_HINTS
  const preferred = lang === 'tanglish' ? ['en-IN', 'ta-IN'] : ['en-IN', 'en-GB', 'en-US']
  for (const code of preferred) {
    const match = voices.find((v) => v.lang.replace('_', '-').startsWith(code) && hints.test(v.name))
    if (match) return match
  }
  return voices.find((v) => hints.test(v.name) && v.lang.startsWith('en')) ?? voices.find((v) => v.lang.startsWith('en')) ?? null
}

/** Sentence-sized pieces (~220 chars max) so the first one is synthesized quickly. */
function speechChunks(text: string): string[] {
  const sentences = text.match(/[^.!?\n]+[.!?]*\s*/g) ?? [text]
  const chunks: string[] = []
  for (const raw of sentences) {
    const sentence = raw.trim()
    if (!sentence) continue
    const last = chunks.length - 1
    if (last >= 0 && chunks[last].length + sentence.length < 220 && chunks.length > 1) chunks[last] += ` ${sentence}`
    else chunks.push(sentence)
  }
  // A short opening clip is what the learner waits for, so cut the first sentence at a comma.
  const comma = chunks[0]?.indexOf(', ', 24) ?? -1
  if (comma > 0 && comma < 90) chunks.splice(0, 1, chunks[0].slice(0, comma + 1), chunks[0].slice(comma + 2))
  return chunks.length ? chunks : [text]
}

const cleanForSpeech = (text: string) => text.replace(/```[\s\S]*?```/g, ' ').replace(/[*_`#>]/g, '').trim()

/** Starts synthesizing clips that are already final, so they are ready when speakAnswer plays them. */
export function prefetchSpeech(text: string, lang: TutorLanguage) {
  if (!ttsAvailable() || typeof window === 'undefined') return
  const chunks = speechChunks(cleanForSpeech(text).slice(0, 4800))
  // The last piece may still be growing while the answer streams in.
  for (const chunk of chunks.slice(0, -1).slice(0, 3)) synthesize(chunk, { lang }).catch(() => {})
}

const silent = (): SpokenAnswer => ({ done: Promise.resolve(), stop: () => {}, getLevel: () => 0, audible: false })

/**
 * Speaks a tutor answer: backend TTS when configured (same voice as the
 * narration), otherwise the browser's speech synthesis in the learner's chosen voice.
 */
export function speakAnswer(text: string, options: { lang: TutorLanguage; rate?: number; muted?: boolean }): SpokenAnswer {
  const clean = cleanForSpeech(text)
  if (!clean || options.muted || typeof window === 'undefined') return silent()

  if (ttsAvailable()) {
    let audio: HTMLAudioElement | null = null
    let stopped = false
    let finish: () => void = () => {}
    const done = new Promise<void>((resolve) => (finish = resolve))
    // Speak sentence by sentence: the first chunk starts as soon as it is synthesized and
    // the next one is fetched while the current one plays, so long answers don't wait.
    const chunks = speechChunks(clean.slice(0, 4800))
    const fetchChunk = (i: number) => synthesize(chunks[i], { lang: options.lang })
    const playFrom = async (i: number, pending: ReturnType<typeof fetchChunk>) => {
      const clip = await pending
      if (stopped) return finish()
      const next = i + 1 < chunks.length ? fetchChunk(i + 1) : null
      next?.catch(() => {})
      if (i + 2 < chunks.length) fetchChunk(i + 2).catch(() => {})
      const url = URL.createObjectURL(clip.blob)
      const release = () => URL.revokeObjectURL(url)
      audio = new Audio(url)
      audio.playbackRate = options.rate ?? 1
      audio.onended = () => {
        release()
        if (next) void playFrom(i + 1, next).catch(() => finish())
        else finish()
      }
      audio.onerror = () => {
        release()
        finish()
      }
      await audio.play().catch(() => {
        release()
        finish()
      })
    }
    void playFrom(0, fetchChunk(0)).catch(() => {
      // Fall back to the browser voice when the backend can't speak.
      if (stopped) return finish()
      const fallback = browserSpeak(clean, options)
      void fallback.done.then(finish)
      stopFallback = fallback.stop
    })
    let stopFallback = () => {}
    return {
      done,
      audible: true,
      stop: () => {
        stopped = true
        if (audio) {
          audio.pause()
          // Revoke the clip that was playing (blob: src only; never a network URL).
          if (audio.src.startsWith('blob:')) URL.revokeObjectURL(audio.src)
        }
        stopFallback()
        finish()
      },
      getLevel: () => {
        if (!audio || audio.paused) return 0
        const t = performance.now() / 1000
        return 0.32 + 0.22 * Math.abs(Math.sin(t * 8.7) * Math.sin(t * 2.9))
      },
    }
  }
  return browserSpeak(clean, options)
}

function browserSpeak(text: string, options: { lang: TutorLanguage; rate?: number }): SpokenAnswer {
  if (!('speechSynthesis' in window)) return silent()
  const synth = window.speechSynthesis
  synth.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  const voice = pickBrowserVoice(options.lang, useReaderPreferences.getState().voiceGender)
  if (voice) utterance.voice = voice
  utterance.lang = voice?.lang ?? 'en-IN'
  utterance.rate = Math.max(0.7, Math.min(1.4, options.rate ?? 1))
  utterance.pitch = 1.08
  let speaking = false
  let lastBoundary = 0
  let finish: () => void = () => {}
  const done = new Promise<void>((resolve) => (finish = resolve))
  utterance.onstart = () => (speaking = true)
  utterance.onboundary = () => (lastBoundary = performance.now())
  utterance.onend = () => {
    speaking = false
    finish()
  }
  utterance.onerror = () => {
    speaking = false
    finish()
  }
  synth.speak(utterance)
  return {
    done,
    audible: true,
    stop: () => {
      synth.cancel()
      speaking = false
      finish()
    },
    getLevel: () => {
      if (!speaking) return 0
      const sinceWord = performance.now() - lastBoundary
      return Math.max(0.2, 0.75 - sinceWord / 600)
    },
  }
}
