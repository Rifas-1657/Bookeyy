import { getPrebuiltAudioUrl, loadPrebuiltTimings } from '@/lib/narration/python-demo-audio'
import { tokenizeNarration } from '@/lib/narration/tokenize'
import { synthesize, ttsAvailable } from '@/lib/narration/tts'
import { PYODIDE_INDEX_URL, warmPyodide } from '@/lib/runner'
import type { BookLessonPage } from '@/types/book'
import { getOfflineBook, notifyOfflineChanged, putOfflineAudio, putOfflineBook, type OfflineBook } from './db'

export interface DownloadProgress {
  done: number
  total: number
  label: string
}

export interface DownloadCourse {
  id: string
  title: string
  chapters: { number: number; title: string }[]
}

/** Pyodide runtime files the service worker should keep for offline practicals. */
export const PYODIDE_FILES = [
  'pyodide.js',
  'pyodide.asm.js',
  'pyodide.asm.wasm',
  'python_stdlib.zip',
  'pyodide-lock.json',
].map((file) => `${PYODIDE_INDEX_URL}${file}`)

/** Asks the service worker to cache URLs (app shell, audio, Pyodide). Resolves even without a SW. */
export async function precacheUrls(urls: string[]) {
  if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return
  const registration = await navigator.serviceWorker.ready.catch(() => null)
  registration?.active?.postMessage({ type: 'bookey:precache', urls })
}

/**
 * "Download for offline": stores the book JSON, every page's narration audio
 * (prebuilt demo mp3s, or backend TTS per sentence) and warms Pyodide so
 * practicals run without a network.
 */
const VOICES = ['male', 'female'] as const

export async function downloadCourseForOffline(
  course: DownloadCourse,
  pages: BookLessonPage[],
  onProgress?: (progress: DownloadProgress) => void,
): Promise<OfflineBook> {
  const total = pages.length + 2
  let done = 0
  let audioBytes = 0
  let audioFailures = 0
  const report = (label: string) => onProgress?.({ done, total, label })

  report('Saving the book')
  const book: OfflineBook = {
    courseId: course.id,
    title: course.title,
    chapters: course.chapters.map((c) => ({ number: c.number, title: c.title })),
    pages,
    savedAt: Date.now(),
    audioBytes: 0,
    complete: false,
  }
  await putOfflineBook(book)
  done += 1

  const shellUrls = [`/book/${course.id}`]
  void precacheUrls([...shellUrls, ...PYODIDE_FILES])

  for (const page of pages) {
    report(`Saving narration · ${page.title}`)
    try {
      if (getPrebuiltAudioUrl(course.id, page.id)) {
        // Both tutor voices, each with its word timings, so switching voice offline still works.
        for (const voice of VOICES) {
          const url = getPrebuiltAudioUrl(course.id, page.id, voice)
          if (!url) continue
          const res = await fetch(url)
          if (!res.ok) throw new Error(`audio ${res.status} for ${url}`)
          const blob = await res.blob()
          const timings = await loadPrebuiltTimings(course.id, page.id, voice)
          audioBytes += blob.size
          await putOfflineAudio({ courseId: course.id, pageId: page.id, segment: 0, blob, timings, voice, whole: true })
        }
      } else if (ttsAvailable()) {
        const { sentences } = tokenizeNarration(page.narrationText)
        for (const sentence of sentences) {
          const clip = await synthesize(sentence.text)
          audioBytes += clip.blob.size
          await putOfflineAudio({
            courseId: course.id,
            pageId: page.id,
            segment: sentence.index,
            blob: clip.blob,
            timings: clip.timings,
            whole: false,
          })
        }
      }
    } catch (error) {
      if (process.env.NODE_ENV === 'development') console.warn('[offline] audio failed for', page.id, error)
      audioFailures += 1
      // Storage itself failing (quota, blocked) means nothing else will save either.
      if (error instanceof DOMException) throw error
    }
    done += 1
  }

  report('Preparing the Python lab')
  await warmPyodide().catch(() => false)
  done += 1

  if (audioFailures > 0) {
    throw new Error(`Narration for ${audioFailures} page${audioFailures === 1 ? '' : 's'} could not be saved`)
  }

  const finalBook = { ...book, audioBytes, complete: true, savedAt: Date.now() }
  await putOfflineBook(finalBook)
  notifyOfflineChanged(course.id)
  report('Ready offline')
  return finalBook
}

export async function isCourseOffline(courseId: string) {
  const book = await getOfflineBook(courseId)
  return Boolean(book?.complete)
}
