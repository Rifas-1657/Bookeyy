/**
 * lib/api/books.ts
 *
 * Learner "Create a book" client (`/api/books`, auth required):
 *
 *   POST /api/books                 multipart {file?, url?, prompt?, title?, level, language,
 *                                   syllabus?, syllabusText?} → {jobId, courseId}
 *   POST /api/books/media           multipart {files[] (one video/audio, or images), …same fields}
 *   GET  /api/books/limits          → upload limits + what the server can read (vision / OCR / whisper)
 *   GET  /api/books                 → my books
 *   GET  /api/books/quota           → {running, maxConcurrent, today, maxPerDay}
 *   GET  /api/books/jobs/{id}       → BookJob snapshot
 *   GET  /api/books/jobs/{id}/stream (SSE; same snapshot per frame — read with fetch so the bearer header travels)
 *   POST /api/books/jobs/{id}/retry  failed book → resumes it (202; 409 if not failed, 429 if over quota)
 *
 * When NEXT_PUBLIC_BOOKEY_API_URL is unset, `resolveBooksClient()` returns a
 * clearly-labelled demo simulation that runs in this tab and opens the Python demo book.
 */

import { API_BASE_URL, AuthError, apiJson, authorizedFetch } from './auth'
import { detectSourceType, isPermanentClientError, readSse, type LinkSourceType } from './course-builder'
import { toLessonPage, type BackendPage } from './pages'
import type { LearningChapter } from '@/lib/mock/learning'
import type { BookLessonPage } from '@/types/book'

export type BookLevel = 'beginner' | 'intermediate' | 'advanced'
export type BookLanguage = 'tanglish' | 'english'
export type BookStepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export interface BookStep {
  key: string
  label: string
  status: BookStepStatus
  detail: string
}

export interface BookJob {
  id: string
  courseId: string
  title: string
  status: string
  busy: boolean
  progress: number
  steps: BookStep[]
  warnings: string[]
  error: string | null
  bookReady: boolean
  aiKnowledge: boolean
  lessons: { id: string; title: string; status: string }[]
}

export interface CreateBookInput {
  file?: File | null
  /** Video/audio (one) or images (several) — sent to POST /api/books/media. */
  files?: File[]
  /** The uploaded file(s) ARE the learner's syllabus: the outline follows them exactly. */
  syllabus?: boolean
  /** Pasted syllabus text (implies syllabus mode). */
  syllabusText?: string
  url?: string
  prompt?: string
  title?: string
  level: BookLevel
  language: BookLanguage
}

export interface MyBook {
  jobId: string
  courseId: string
  title: string
  status: string
  progress: number
  busy: boolean
  published: boolean
  origin: string
  aiKnowledge: boolean
  createdAt: string | null
}

export interface BookQuota {
  running: number
  maxConcurrent: number
  today: number
  maxPerDay: number
  exempt?: boolean
}

export interface BookLimits {
  maxUploadMb: number
  maxMediaMb: number
  maxMediaMinutes: number
  maxImages: number
  maxImageMb: number
  syllabusMaxChapters: number
  transcription: boolean
  images: { vision: boolean; tesseract: boolean; engine: 'vision' | 'tesseract' | 'none'; heic: boolean }
}

export interface BooksClient {
  mode: 'live' | 'demo'
  create(input: CreateBookInput): Promise<{ jobId: string; courseId: string }>
  /** Server upload limits + capabilities (null when unknown). */
  limits(): Promise<BookLimits | null>
  subscribe(jobId: string, onUpdate: (job: BookJob) => void, onError?: (e: Error) => void): () => void
  listMine(): Promise<MyBook[]>
  quota(): Promise<BookQuota | null>
  /** Re-queue a failed book; the backend resumes it (finished parts are kept). */
  retry(jobId: string): Promise<void>
}

export const ACCEPTED_EXTENSIONS = ['.pdf', '.pptx', '.docx', '.txt', '.md'] as const
export const VIDEO_EXTENSIONS = ['.mp4', '.mov', '.webm', '.mkv', '.m4v'] as const
export const AUDIO_EXTENSIONS = ['.m4a', '.mp3', '.wav', '.ogg', '.opus', '.aac', '.flac'] as const
export const IMAGE_EXTENSIONS = ['.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'] as const
export const MAX_UPLOAD_MB = 20

export const DEFAULT_LIMITS: BookLimits = {
  maxUploadMb: MAX_UPLOAD_MB,
  maxMediaMb: 500,
  maxMediaMinutes: 120,
  maxImages: 20,
  maxImageMb: 15,
  syllabusMaxChapters: 12,
  transcription: true,
  images: { vision: true, tesseract: false, engine: 'vision', heic: false },
}

export type UploadKind = 'document' | 'video' | 'audio' | 'image'

export function uploadKind(name: string): UploadKind | null {
  const i = name.lastIndexOf('.')
  const ext = i >= 0 ? name.slice(i).toLowerCase() : ''
  if ((ACCEPTED_EXTENSIONS as readonly string[]).includes(ext)) return 'document'
  if ((VIDEO_EXTENSIONS as readonly string[]).includes(ext)) return 'video'
  if ((AUDIO_EXTENSIONS as readonly string[]).includes(ext)) return 'audio'
  if ((IMAGE_EXTENSIONS as readonly string[]).includes(ext)) return 'image'
  return null
}

export const ALL_ACCEPTED = [...ACCEPTED_EXTENSIONS, ...VIDEO_EXTENSIONS, ...AUDIO_EXTENSIONS, ...IMAGE_EXTENSIONS]

export const LINK_LABELS: Record<LinkSourceType, string> = {
  youtube: 'YouTube video',
  github: 'GitHub repository',
  reddit: 'Reddit thread',
  arxiv: 'arXiv paper',
  pdf_url: 'PDF link',
  web: 'Web page',
}

/** Paper ids like 2401.01234 count as arXiv even without a URL. */
export function detectLink(raw: string): LinkSourceType | null {
  const t = raw.trim()
  if (/^\d{4}\.\d{4,5}(v\d+)?$/.test(t)) return 'arxiv'
  return detectSourceType(t)
}

export function isFinished(job: BookJob) {
  return (job.status === 'ready' || job.status === 'failed') && !job.busy
}

type Raw = Record<string, unknown>

function normalizeJob(raw: unknown): BookJob {
  const r = (raw ?? {}) as Raw
  const steps = Array.isArray(r.steps) ? (r.steps as Raw[]) : []
  const lessons = Array.isArray(r.lessons) ? (r.lessons as Raw[]) : []
  return {
    id: String(r.id ?? ''),
    courseId: String(r.courseId ?? ''),
    title: String(((r.outline as Raw | null)?.title as string | undefined) ?? r.title ?? 'Your book'),
    status: String(r.status ?? 'queued'),
    busy: Boolean(r.busy),
    progress: Number(r.progress ?? 0),
    steps: steps.map((s) => ({
      key: String(s.key ?? ''),
      label: String(s.label ?? ''),
      status: (s.status as BookStepStatus) ?? 'pending',
      detail: String(s.detail ?? ''),
    })),
    warnings: Array.isArray(r.warnings) ? (r.warnings as unknown[]).map(String) : [],
    error: (r.error as string | null) ?? null,
    bookReady: Boolean(r.bookReady ?? r.publishedAt),
    aiKnowledge: Boolean(r.aiKnowledge),
    lessons: lessons.map((l) => ({ id: String(l.id ?? ''), title: String(l.title ?? ''), status: String(l.status ?? '') })),
  }
}

async function errorMessage(res: Response) {
  try {
    const data = (await res.json()) as { detail?: unknown }
    if (typeof data.detail === 'string') return data.detail
  } catch {
    // ignore
  }
  return res.statusText || `HTTP ${res.status}`
}

function liveClient(): BooksClient {
  return {
    mode: 'live',
    async create(input) {
      const form = new FormData()
      const media = input.files?.length ? input.files : null
      if (media) media.forEach((f) => form.append('files', f))
      else if (input.file) form.append('file', input.file)
      if (input.url?.trim()) form.append('url', input.url.trim())
      if (input.prompt?.trim()) form.append('prompt', input.prompt.trim())
      if (input.title?.trim()) form.append('title', input.title.trim())
      if (input.syllabus) form.append('syllabus', 'true')
      if (input.syllabusText?.trim()) form.append('syllabusText', input.syllabusText.trim())
      form.append('level', input.level)
      form.append('language', input.language)
      const res = await authorizedFetch(media ? '/api/books/media' : '/api/books', { method: 'POST', body: form })
      if (!res.ok) throw new AuthError(await errorMessage(res), res.status)
      const data = (await res.json()) as Raw
      return { jobId: String(data.jobId), courseId: String(data.courseId) }
    },
    subscribe(jobId, onUpdate, onError) {
      const controller = new AbortController()
      let timer: number | undefined
      let stopped = false
      const path = `/api/books/jobs/${encodeURIComponent(jobId)}`

      const poll = async () => {
        if (stopped) return
        try {
          const job = normalizeJob(await apiJson<unknown>(path, { signal: controller.signal }))
          onUpdate(job)
          if (isFinished(job)) return
        } catch (e) {
          if (stopped) return
          onError?.(e as Error)
          // 401/403/404…: polling again can't succeed.
          if (isPermanentClientError(e)) return
        }
        timer = window.setTimeout(poll, 2500)
      }

      ;(async () => {
        try {
          const res = await authorizedFetch(`${path}/stream`, {
            headers: { Accept: 'text/event-stream' },
            signal: controller.signal,
          })
          if (!res.ok || !res.body) throw new Error(`stream ${res.status}`)
          let last: BookJob | null = null
          await readSse(
            res.body,
            (data) => {
              try {
                const parsed = JSON.parse(data) as Raw
                if (!('steps' in parsed)) return // `event: end` frame
                last = normalizeJob(parsed)
                onUpdate(last)
              } catch {
                // keep-alive / malformed frame
              }
            },
            controller.signal,
          )
          if (!stopped && (!last || !isFinished(last))) poll()
        } catch {
          if (!stopped) poll()
        }
      })()

      return () => {
        stopped = true
        controller.abort()
        if (timer) window.clearTimeout(timer)
      }
    },
    async listMine() {
      return apiJson<MyBook[]>('/api/books')
    },
    async limits() {
      try {
        return await apiJson<BookLimits>('/api/books/limits')
      } catch {
        return null
      }
    },
    async quota() {
      try {
        return await apiJson<BookQuota>('/api/books/quota')
      } catch {
        return null
      }
    },
    async retry(jobId) {
      const res = await authorizedFetch(`/api/books/jobs/${encodeURIComponent(jobId)}/retry`, { method: 'POST' })
      if (!res.ok) throw new AuthError(await errorMessage(res), res.status)
    },
  }
}

/* ------------------------------- demo mode ------------------------------- */

const DEMO_STEPS: Array<[string, string]> = [
  ['ingest', 'Fetch sources'],
  ['index', 'Chunk & embed'],
  ['outline', 'Draft book outline'],
  ['lesson1', 'Write chapter 1'],
  ['lessons', 'Write remaining lessons'],
  ['publish', 'Open in your library'],
]

function demoClient(): BooksClient {
  return {
    mode: 'demo',
    async create() {
      return { jobId: `demo-${Date.now()}`, courseId: 'python' }
    },
    subscribe(jobId, onUpdate) {
      let tick = 0
      const total = DEMO_STEPS.length
      const emit = () => {
        const steps = DEMO_STEPS.map(([key, label], i): BookStep => ({
          key,
          label,
          status: i < tick ? 'done' : i === tick ? 'running' : 'pending',
          detail: i < tick ? 'Simulated' : '',
        }))
        const done = tick >= total
        onUpdate({
          id: jobId,
          courseId: 'python',
          title: 'Demo: Python for beginners',
          status: done ? 'ready' : tick >= 3 ? 'generating' : 'ingesting',
          busy: !done,
          progress: Math.round((Math.min(tick, total) / total) * 100),
          steps,
          warnings: ['Demo simulation — no backend is configured, so this opens the seeded Python book.'],
          error: null,
          bookReady: tick >= 4,
          aiKnowledge: false,
          lessons: [],
        })
        tick += 1
        if (!done) timer = window.setTimeout(emit, 900)
      }
      let timer = window.setTimeout(emit, 300)
      return () => window.clearTimeout(timer)
    },
    async listMine() {
      return []
    },
    async limits() {
      return null
    },
    async quota() {
      return null
    },
    async retry() {
      // demo jobs never fail
    },
  }
}

export function resolveBooksClient(): BooksClient {
  return API_BASE_URL !== null ? liveClient() : demoClient()
}

/* ------------------------- reader: generated books ------------------------- */

export interface GeneratedBook {
  id: string
  title: string
  chapters: LearningChapter[]
  pages: BookLessonPage[]
  aiGenerated: boolean
  buildJobId: string | null
}

interface BackendCourseDetail {
  id: string
  title: string
  ai_generated?: boolean
  build_job_id?: string | null
  chapters: { number: number; title: string; summary: string; page_count: number }[]
}

/** Loads a non-seeded course (e.g. a learner's private book) with the reader's auth. */
export async function loadGeneratedBook(courseId: string): Promise<GeneratedBook | null> {
  const id = encodeURIComponent(courseId)
  const [courseRes, pagesRes] = await Promise.all([
    authorizedFetch(`/api/courses/${id}`, { cache: 'no-store' }),
    authorizedFetch(`/api/courses/${id}/pages`, { cache: 'no-store' }),
  ])
  if (courseRes.status === 404 || pagesRes.status === 404) return null
  if (!courseRes.ok) throw new Error(await errorMessage(courseRes))
  if (!pagesRes.ok) throw new Error(await errorMessage(pagesRes))
  const course = (await courseRes.json()) as BackendCourseDetail
  const pages = ((await pagesRes.json()) as BackendPage[]).map(toLessonPage)
  return {
    id: course.id,
    title: course.title,
    chapters: course.chapters.map((c) => ({ number: c.number, title: c.title, summary: c.summary, pages: c.page_count })),
    pages,
    aiGenerated: Boolean(course.ai_generated),
    buildJobId: course.build_job_id ?? null,
  }
}
