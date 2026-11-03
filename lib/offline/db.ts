/**
 * Tiny promise wrapper around IndexedDB for offline books (no dependency).
 *
 * Stores
 * - `books`     key courseId                  → OfflineBook (book JSON)
 * - `audio`     key `${courseId}/${pageId}/${segment}[@voice]` → OfflineAudio (blob + timings)
 * - `captures`  key id                        → LessonCapture (screenshots, by course/page)
 * - `activity`  key id                        → LessonActivity (practicals run, Q&A)
 *
 * Every call degrades to a no-op/empty result when IndexedDB is unavailable
 * (SSR, private mode), so callers never need to guard. Writes resolve only once
 * the transaction has committed; the book/audio writers used by "Download for
 * offline" reject on failure (quota, aborted transaction) so the UI never
 * reports "Ready offline" for a partial save.
 */

import type { WordTiming } from '@/lib/contracts/book-events'
import type { BookLessonPage } from '@/types/book'

const DB_NAME = 'bookey-offline'
const DB_VERSION = 1

export interface OfflineBook {
  courseId: string
  title: string
  chapters: { number: number; title: string }[]
  pages: BookLessonPage[]
  savedAt: number
  /** Bytes of audio stored for this course. */
  audioBytes: number
  complete: boolean
}

export interface OfflineAudio {
  key: string
  courseId: string
  pageId: string
  segment: number
  blob: Blob
  timings?: WordTiming[]
  /** Tutor voice of a prebuilt recording; absent for voice-agnostic clips. */
  voice?: 'male' | 'female'
  /** True when segment 0 covers the whole page (prebuilt mp3), false for one sentence. */
  whole?: boolean
}

export type CaptureKind = 'diagram' | 'output' | 'answer'

export interface LessonCapture {
  id: string
  courseId: string
  pageId: string
  chapterNumber: number
  kind: CaptureKind
  title: string
  blob: Blob
  createdAt: number
}

export type LessonActivity =
  | {
      id: string
      courseId: string
      pageId: string
      chapterNumber: number
      kind: 'practical'
      code: string
      stdout: string
      stderr?: string
      createdAt: number
    }
  | {
      id: string
      courseId: string
      pageId: string
      chapterNumber: number
      kind: 'question'
      question: string
      answer: string
      createdAt: number
    }

type StoreName = 'books' | 'audio' | 'captures' | 'activity'

let dbPromise: Promise<IDBDatabase | null> | null = null

function openDb(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise
  dbPromise = new Promise((resolve) => {
    if (typeof indexedDB === 'undefined') return resolve(null)
    let request: IDBOpenDBRequest
    try {
      request = indexedDB.open(DB_NAME, DB_VERSION)
    } catch {
      return resolve(null)
    }
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains('books')) db.createObjectStore('books', { keyPath: 'courseId' })
      if (!db.objectStoreNames.contains('audio')) {
        const store = db.createObjectStore('audio', { keyPath: 'key' })
        store.createIndex('courseId', 'courseId')
      }
      if (!db.objectStoreNames.contains('captures')) {
        const store = db.createObjectStore('captures', { keyPath: 'id' })
        store.createIndex('courseId', 'courseId')
      }
      if (!db.objectStoreNames.contains('activity')) {
        const store = db.createObjectStore('activity', { keyPath: 'id' })
        store.createIndex('courseId', 'courseId')
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => resolve(null)
    request.onblocked = () => resolve(null)
  })
  return dbPromise
}

/** Logout: closes the connection and deletes the whole offline database. */
export async function clearOfflineData(): Promise<void> {
  if (typeof indexedDB === 'undefined') return
  const db = await (dbPromise ?? Promise.resolve(null)).catch(() => null)
  db?.close()
  dbPromise = null
  await new Promise<void>((resolve) => {
    try {
      const request = indexedDB.deleteDatabase(DB_NAME)
      request.onsuccess = () => resolve()
      request.onerror = () => resolve()
      request.onblocked = () => resolve()
    } catch {
      resolve()
    }
  })
}

function wrap<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

function committed(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error ?? new Error('IndexedDB transaction failed'))
    tx.onabort = () => reject(tx.error ?? new Error('IndexedDB transaction aborted'))
  })
}

async function withStore<T>(
  name: StoreName,
  mode: IDBTransactionMode,
  fn: (store: IDBObjectStore) => IDBRequest<T>,
  options: { throwOnError?: boolean } = {},
) {
  const db = await openDb()
  if (!db) {
    if (options.throwOnError) throw new Error('Offline storage is unavailable in this browser')
    return undefined
  }
  try {
    const tx = db.transaction(name, mode)
    const done = mode === 'readwrite' ? committed(tx) : null
    // Don't leave the commit promise unobserved if the request itself fails first.
    done?.catch(() => {})
    const result = await wrap(fn(tx.objectStore(name)))
    if (done) await done
    return result
  } catch (error) {
    if (process.env.NODE_ENV === 'development') console.warn('[offline-db]', name, error)
    if (options.throwOnError) throw error
    return undefined
  }
}

async function byCourse<T>(name: StoreName, courseId: string): Promise<T[]> {
  const result = await withStore(name, 'readonly', (store) => store.index('courseId').getAll(courseId))
  return (result as T[] | undefined) ?? []
}

/* ---------------------------------- books --------------------------------- */

export const getOfflineBook = (courseId: string) =>
  withStore('books', 'readonly', (s) => s.get(courseId)) as Promise<OfflineBook | undefined>

export const putOfflineBook = (book: OfflineBook) =>
  withStore('books', 'readwrite', (s) => s.put(book), { throwOnError: true })

export async function removeOfflineBook(courseId: string) {
  await withStore('books', 'readwrite', (s) => s.delete(courseId))
  const audio = await byCourse<OfflineAudio>('audio', courseId)
  for (const entry of audio) await withStore('audio', 'readwrite', (s) => s.delete(entry.key))
}

/* ---------------------------------- audio --------------------------------- */

export const audioKey = (courseId: string, pageId: string, segment: number, voice?: OfflineAudio['voice']) =>
  `${courseId}/${pageId}/${segment}${voice ? `@${voice}` : ''}`

export const getOfflineAudio = (courseId: string, pageId: string, segment = 0, voice?: OfflineAudio['voice']) =>
  withStore('audio', 'readonly', (s) => s.get(audioKey(courseId, pageId, segment, voice))) as Promise<OfflineAudio | undefined>

export const putOfflineAudio = (entry: Omit<OfflineAudio, 'key'>) =>
  withStore(
    'audio',
    'readwrite',
    (s) => s.put({ ...entry, key: audioKey(entry.courseId, entry.pageId, entry.segment, entry.voice) }),
    { throwOnError: true },
  )

/** Pure: picks the clips for `voice` (recordings in that voice win; voice-agnostic clips otherwise). */
export function selectPageAudio(entries: OfflineAudio[], pageId: string, voice?: OfflineAudio['voice']): OfflineAudio[] {
  const forPage = entries.filter((entry) => entry.pageId === pageId)
  const voiced = voice ? forPage.filter((entry) => entry.voice === voice) : []
  const chosen = voiced.length ? voiced : forPage.filter((entry) => !entry.voice)
  return chosen.sort((a, b) => a.segment - b.segment)
}

export async function getPageAudio(courseId: string, pageId: string, voice?: OfflineAudio['voice']): Promise<OfflineAudio[]> {
  const all = await byCourse<OfflineAudio>('audio', courseId)
  return selectPageAudio(all, pageId, voice)
}

/* -------------------------------- captures -------------------------------- */

/** One capture per (page, kind): a re-run replaces the previous screenshot. */
export function putCapture(capture: Omit<LessonCapture, 'id' | 'createdAt'>) {
  const id = `${capture.courseId}/${capture.pageId}/${capture.kind}`
  return withStore('captures', 'readwrite', (s) => s.put({ ...capture, id, createdAt: Date.now() }))
}

export const listCaptures = (courseId: string) => byCourse<LessonCapture>('captures', courseId)

/* -------------------------------- activity -------------------------------- */

export function putActivity(entry: LessonActivity) {
  return withStore('activity', 'readwrite', (s) => s.put(entry))
}

export const listActivity = (courseId: string) => byCourse<LessonActivity>('activity', courseId)

/** Signals same-tab listeners (recap pages, badges) that offline data changed. */
export const OFFLINE_EVENT = 'bookey:offline-changed'

export function notifyOfflineChanged(courseId: string) {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(OFFLINE_EVENT, { detail: { courseId } }))
}
