/**
 * lib/api/course-builder.ts
 *
 * Client for the admin Course Builder API (`/api/admin/course-builder`):
 *
 *   POST  /preview-source          {type, url}            → SourcePreview
 *   POST  /jobs                    CreateJobInput          → {jobId}
 *   GET   /jobs                                            → BuildJob[]
 *   GET   /jobs/{id}                                       → BuildJob
 *   GET   /jobs/{id}/stream        (SSE, BuildJob frames)
 *   PATCH /jobs/{id}/outline       {outline}
 *   POST  /jobs/{id}/generate
 *   POST  /jobs/{id}/publish
 *
 * Auth goes through `authorizedFetch` (bearer token + one refresh on 401).
 * Responses are normalised so either camelCase or snake_case keys work.
 *
 * When NEXT_PUBLIC_BOOKEY_API_URL is unset, or the endpoint is unreachable /
 * not deployed (404), `resolveCourseBuilder()` returns a clearly-labelled demo
 * simulation that runs entirely in this browser tab.
 */

import { API_BASE_URL, authorizedFetch, getAuthToken } from './auth'
import { uploadBatch } from './kb-client'

// ---- Types ------------------------------------------------------------------

export type SourceType = 'youtube' | 'github' | 'reddit' | 'arxiv' | 'pdf_url' | 'web' | 'upload' | 'syllabus'
export type LinkSourceType = Exclude<SourceType, 'upload' | 'syllabus'>

export type SourceInput = { type: LinkSourceType; url: string } | { type: 'upload'; documentId: string }

export interface SourcePreview {
  title: string
  kind: string
  durationS?: number
  thumbnailUrl?: string
  author?: string
  description?: string
  [key: string]: unknown
}

export type CourseLevel = 'beginner' | 'intermediate' | 'advanced'
export type CourseLanguage = 'tanglish' | 'english'

export interface CreateJobInput {
  title: string
  description: string
  level: CourseLevel
  language: CourseLanguage
  targetAudience: string
  sources: SourceInput[]
  syllabusText?: string
}

export type StepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export interface BuildStep {
  key: string
  label: string
  status: StepStatus
  detail?: string
}

export interface Practical {
  kind: string
  prompt: string
  starterCode?: string
}

export interface Lesson {
  title: string
  objectives: string[]
  practical: Practical
  tasks: string[]
  diagramIdeas: string[]
  /** Backend fields the editor doesn't show (narration plan, tests, citations…), kept so saves don't drop them. */
  raw?: Record<string, unknown>
}

export interface Chapter {
  title: string
  summary: string
  lessons: Lesson[]
  project: { title: string; brief: string }
  raw?: Record<string, unknown>
}

export interface Outline {
  chapters: Chapter[]
  raw?: Record<string, unknown>
}

/** queued → running → outline_ready → generating → ready → published (or failed). */
export type JobStatus = 'queued' | 'running' | 'outline_ready' | 'generating' | 'ready' | 'published' | 'failed' | (string & {})

export interface BuildJob {
  id: string
  title?: string
  status: JobStatus
  progress: number
  steps: BuildStep[]
  sources: Array<Record<string, unknown>>
  outline?: Outline | null
  createdAt?: string
  error?: string
  courseId?: string
  /** Backend is still generating lessons in the background, even if status reads 'ready'. */
  busy?: boolean
}

export interface UploadedDoc {
  documentId: string
  filename: string
  sizeBytes: number
}

export type BuilderMode = 'live' | 'demo'

export interface CourseBuilderClient {
  mode: BuilderMode
  /** Why the demo is active (unset / unreachable / not deployed). */
  reason?: string
  previewSource(type: LinkSourceType, url: string): Promise<SourcePreview>
  uploadDocuments(files: File[], draftId: string): Promise<{ accepted: UploadedDoc[]; rejected: { filename: string; reason: string }[] }>
  createJob(input: CreateJobInput): Promise<{ jobId: string }>
  listJobs(): Promise<BuildJob[]>
  getJob(id: string): Promise<BuildJob>
  /** Live updates; falls back to polling. Returns an unsubscribe function. */
  subscribe(id: string, onUpdate: (job: BuildJob) => void, onError?: (error: Error) => void): () => void
  patchOutline(id: string, outline: Outline): Promise<BuildJob>
  generate(id: string): Promise<BuildJob>
  publish(id: string): Promise<BuildJob>
}

// ---- Source detection ----------------------------------------------------------

const PATTERNS: Array<[LinkSourceType, RegExp]> = [
  ['youtube', /(^|\.)((youtube\.com)|(youtu\.be)|(youtube-nocookie\.com))$/i],
  ['github', /(^|\.)github\.com$/i],
  ['reddit', /(^|\.)(reddit\.com|redd\.it)$/i],
  ['arxiv', /(^|\.)arxiv\.org$/i],
]

export function normalizeUrl(raw: string): URL | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  try {
    const url = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`)
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return null
    if (!url.hostname.includes('.')) return null
    return url
  } catch {
    return null
  }
}

/** Best-guess source type for a pasted link. */
export function detectSourceType(raw: string): LinkSourceType | null {
  const url = normalizeUrl(raw)
  if (!url) return null
  for (const [type, re] of PATTERNS) if (re.test(url.hostname)) return type
  if (/\.pdf($|\?)/i.test(url.pathname)) return 'pdf_url'
  return 'web'
}

export function youtubeId(url: URL): string | null {
  if (/youtu\.be$/i.test(url.hostname)) return url.pathname.slice(1).split('/')[0] || null
  const v = url.searchParams.get('v')
  if (v) return v
  const m = url.pathname.match(/\/(embed|shorts|live)\/([\w-]{6,})/)
  return m ? m[2] : null
}

// ---- Normalisation ----------------------------------------------------------------

type Raw = Record<string, unknown>

function pick<T>(obj: Raw | undefined, ...keys: string[]): T | undefined {
  if (!obj) return undefined
  for (const k of keys) if (obj[k] !== undefined && obj[k] !== null) return obj[k] as T
  return undefined
}

const strings = (v: unknown): string[] => (Array.isArray(v) ? v.map((x) => (typeof x === 'string' ? x : String((x as Raw)?.text ?? (x as Raw)?.title ?? ''))).filter(Boolean) : [])

function normLesson(raw: Raw): Lesson {
  const p = (pick<Raw>(raw, 'practical') ?? {}) as Raw
  return {
    title: String(pick(raw, 'title') ?? 'Untitled lesson'),
    objectives: strings(pick(raw, 'objectives')),
    practical: {
      kind: String(pick(p, 'kind', 'type') ?? 'exercise'),
      prompt: String(pick(p, 'prompt', 'instructions') ?? ''),
      starterCode: pick<string>(p, 'starterCode', 'starter_code'),
    },
    tasks: (Array.isArray(raw.tasks) ? raw.tasks : []).map((t) => (typeof t === 'string' ? t : String((t as Raw)?.prompt ?? (t as Raw)?.text ?? (t as Raw)?.title ?? ''))).filter(Boolean),
    diagramIdeas: strings(pick(raw, 'diagramIdeas', 'diagram_ideas')),
    raw,
  }
}

/** Merge editor changes back onto the backend's full outline so unedited fields survive a PATCH. */
export function denormalizeOutline(outline: Outline): Raw {
  return {
    ...(outline.raw ?? {}),
    chapters: outline.chapters.map((c) => {
      const rawProject = ((c.raw?.project as Raw | undefined) ?? {}) as Raw
      return {
        ...(c.raw ?? {}),
        title: c.title,
        summary: c.summary,
        project: { ...rawProject, title: c.project.title, brief: c.project.brief },
        lessons: c.lessons.map((l) => {
          const r = l.raw ?? {}
          const rawPractical = ((r.practical as Raw | undefined) ?? {}) as Raw
          const rawTasks = Array.isArray(r.tasks) ? (r.tasks as unknown[]) : []
          const kind = l.practical.kind === 'code' || l.practical.kind === 'activity' ? l.practical.kind : l.practical.starterCode ? 'code' : 'activity'
          const plan = Array.isArray(r.explanationPlan) && r.explanationPlan.length ? r.explanationPlan : [l.title]
          return {
            ...r,
            title: l.title,
            objectives: l.objectives.length ? l.objectives : [l.title],
            explanationPlan: plan,
            diagramIdeas: l.diagramIdeas,
            practical: { title: l.title, ...rawPractical, kind, instructions: l.practical.prompt, starterCode: l.practical.starterCode ?? '' },
            tasks: l.tasks.map((prompt, i) => {
              const prev = rawTasks[i]
              return prev && typeof prev === 'object' ? { ...(prev as Raw), prompt } : { kind: 'task', prompt }
            }),
          }
        }),
      }
    }),
  }
}

export function normalizeOutline(raw: unknown): Outline | null {
  if (!raw || typeof raw !== 'object') return null
  const chapters = pick<Raw[]>(raw as Raw, 'chapters')
  if (!Array.isArray(chapters)) return null
  return {
    chapters: chapters.map((c) => {
      const project = (pick<Raw>(c, 'project') ?? {}) as Raw
      return {
        title: String(pick(c, 'title') ?? 'Untitled chapter'),
        summary: String(pick(c, 'summary') ?? ''),
        lessons: (pick<Raw[]>(c, 'lessons') ?? []).map(normLesson),
        project: { title: String(pick(project, 'title') ?? ''), brief: String(pick(project, 'brief', 'description') ?? '') },
        raw: c,
      }
    }),
    raw: raw as Raw,
  }
}

export function normalizeJob(raw: unknown): BuildJob {
  const r = (raw ?? {}) as Raw
  const steps = (pick<Raw[]>(r, 'steps') ?? []).map((s) => ({
    key: String(pick(s, 'key', 'id') ?? ''),
    label: String(pick(s, 'label', 'name') ?? pick(s, 'key') ?? ''),
    status: String(pick(s, 'status') ?? 'pending') as StepStatus,
    detail: pick<string>(s, 'detail', 'message') ?? undefined,
  }))
  const progress = Number(pick(r, 'progress', 'progress_pct') ?? 0)
  return {
    id: String(pick(r, 'id', 'jobId', 'job_id') ?? ''),
    title: pick<string>(r, 'title'),
    status: String(pick(r, 'status') ?? 'queued'),
    progress: Number.isFinite(progress) ? Math.max(0, Math.min(100, progress)) : 0,
    steps,
    sources: pick<Raw[]>(r, 'sources') ?? [],
    outline: normalizeOutline(pick(r, 'outline')),
    createdAt: pick<string>(r, 'createdAt', 'created_at'),
    error: pick<string>(r, 'error', 'error_message', 'detail'),
    courseId: pick<string>(r, 'courseId', 'course_id'),
    busy: Boolean(pick(r, 'busy')),
  }
}

function normalizePreview(raw: unknown, fallbackKind: string): SourcePreview {
  const r = (raw ?? {}) as Raw
  return {
    ...r,
    title: String(pick(r, 'title') ?? 'Untitled source'),
    kind: String(pick(r, 'kind', 'type') ?? fallbackKind),
    durationS: pick<number>(r, 'durationS', 'durationSeconds', 'duration_s', 'duration'),
    thumbnailUrl: pick<string>(r, 'thumbnailUrl', 'thumbnail_url', 'thumbnail'),
    author: pick<string>(r, 'author', 'channel', 'owner'),
    description: pick<string>(r, 'description', 'summary'),
  }
}

export const isTerminalJob = (s: JobStatus) => s === 'outline_ready' || s === 'ready' || s === 'published' || s === 'failed'

// ---- Live client --------------------------------------------------------------------

const BASE = '/api/admin/course-builder'

class BuilderHttpError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await authorizedFetch(`${BASE}${path}`, init)
  if (!res.ok) {
    let message = res.statusText
    try {
      const data = (await res.json()) as { detail?: unknown }
      if (typeof data.detail === 'string') message = data.detail
    } catch {
      // keep statusText
    }
    throw new BuilderHttpError(message || `HTTP ${res.status}`, res.status)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** Parses an SSE byte stream into `data:` payloads. */
/** A stream that sends nothing (not even a keep-alive) for this long is treated as dead. */
export const SSE_IDLE_MS = 45_000

/** True for 4xx answers that won't change by retrying (408/429 are retryable). */
export function isPermanentClientError(error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status
  return typeof status === 'number' && status >= 400 && status < 500 && status !== 408 && status !== 429
}

/**
 * Reads `data:` blocks from an SSE body. Throws `Error('sse idle')` (after
 * cancelling the reader) when no bytes arrive for `idleMs`, so callers fall
 * back to polling instead of hanging on a silently dead connection.
 */
export async function readSse(
  body: ReadableStream<Uint8Array>,
  onData: (data: string) => void,
  signal: AbortSignal,
  idleMs = SSE_IDLE_MS,
) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (!signal.aborted) {
    let idleTimer: ReturnType<typeof setTimeout> | undefined
    const idle = new Promise<'idle'>((resolve) => {
      idleTimer = setTimeout(() => resolve('idle'), idleMs)
    })
    const next = await Promise.race([reader.read(), idle])
    clearTimeout(idleTimer)
    if (next === 'idle') {
      void reader.cancel().catch(() => {})
      throw new Error('sse idle')
    }
    const { value, done } = next
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.search(/\r?\n\r?\n/)) !== -1) {
      const block = buffer.slice(0, idx)
      buffer = buffer.slice(idx).replace(/^\r?\n\r?\n/, '')
      const data = block
        .split(/\r?\n/)
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trimStart())
        .join('\n')
      if (data) onData(data)
    }
  }
}

function liveClient(): CourseBuilderClient {
  return {
    mode: 'live',
    async previewSource(type, url) {
      const raw = await call<unknown>('/preview-source', { method: 'POST', body: JSON.stringify({ type, url }) })
      return normalizePreview(raw, type)
    },
    async uploadDocuments(files, draftId) {
      const res = await uploadBatch(draftId, files)
      return {
        accepted: res.accepted.map((d) => ({ documentId: d.id, filename: d.original_filename, sizeBytes: d.size_bytes })),
        rejected: res.rejected,
      }
    },
    async createJob(input) {
      const raw = await call<Raw>('/jobs', { method: 'POST', body: JSON.stringify(input) })
      return { jobId: String(pick(raw, 'jobId', 'job_id', 'id') ?? '') }
    },
    async listJobs() {
      const raw = await call<unknown>('/jobs')
      const list = Array.isArray(raw) ? raw : ((raw as Raw)?.jobs as unknown[]) ?? []
      return list.map(normalizeJob)
    },
    async getJob(id) {
      return normalizeJob(await call<unknown>(`/jobs/${encodeURIComponent(id)}`))
    },
    subscribe(id, onUpdate, onError) {
      const controller = new AbortController()
      let pollTimer: number | undefined
      let stopped = false

      const poll = async () => {
        if (stopped) return
        try {
          const job = normalizeJob(await call<unknown>(`/jobs/${encodeURIComponent(id)}`, { signal: controller.signal }))
          onUpdate(job)
          if (isTerminalJob(job.status) && !job.busy) return
        } catch (e) {
          if (stopped) return
          onError?.(e as Error)
          // 401/403/404…: polling again can't succeed.
          if (isPermanentClientError(e)) return
        }
        pollTimer = window.setTimeout(poll, 2000)
      }

      ;(async () => {
        try {
          // fetch-based SSE so the bearer token travels in a header (EventSource can't set one).
          const res = await authorizedFetch(`${BASE}/jobs/${encodeURIComponent(id)}/stream`, {
            headers: { Accept: 'text/event-stream' },
            signal: controller.signal,
          })
          if (!res.ok || !res.body) throw new Error(`stream ${res.status}`)
          let last: BuildJob | null = null
          await readSse(
            res.body,
            (data) => {
              try {
                last = normalizeJob(JSON.parse(data))
                onUpdate(last)
              } catch {
                // ignore keep-alive / malformed frames
              }
            },
            controller.signal,
          )
          // Stream ended: make sure we have the final state.
          if (!stopped && (!last || !isTerminalJob((last as BuildJob).status) || (last as BuildJob).busy)) poll()
        } catch {
          if (!stopped) poll()
        }
      })()

      return () => {
        stopped = true
        controller.abort()
        window.clearTimeout(pollTimer)
      }
    },
    async patchOutline(id, outline) {
      return normalizeJob(await call<unknown>(`/jobs/${encodeURIComponent(id)}/outline`, { method: 'PATCH', body: JSON.stringify({ outline: denormalizeOutline(outline) }) }))
    },
    async generate(id) {
      return normalizeJob(await call<unknown>(`/jobs/${encodeURIComponent(id)}/generate`, { method: 'POST' }))
    },
    async publish(id) {
      return normalizeJob(await call<unknown>(`/jobs/${encodeURIComponent(id)}/publish`, { method: 'POST' }))
    },
  }
}

// ---- Demo simulation ----------------------------------------------------------------

const OUTLINE_STEPS: Array<[string, string]> = [
  ['fetch', 'Fetching sources'],
  ['extract', 'Extracting transcripts, text and code'],
  ['analyze', 'Mapping concepts across sources'],
  ['outline', 'Drafting chapters and lessons'],
]
const GENERATE_STEPS: Array<[string, string]> = [
  ['lessons', 'Writing lesson pages'],
  ['practicals', 'Building practicals and tasks'],
  ['diagrams', 'Sketching live diagrams'],
  ['narration', 'Scripting the tutor narration'],
  ['review', 'Final quality review'],
]

interface DemoJob {
  job: BuildJob
  input: CreateJobInput
  listeners: Set<(job: BuildJob) => void>
  timer?: number
}

const demoJobs = new Map<string, DemoJob>()

function emit(entry: DemoJob) {
  const snapshot = structuredClone(entry.job)
  entry.listeners.forEach((l) => l(snapshot))
}

function titleCase(s: string) {
  return s.replace(/\b\w/g, (c) => c.toUpperCase())
}

function demoOutline(input: CreateJobInput): Outline {
  const topic = input.title.trim() || 'Your course'
  const tanglish = input.language === 'tanglish'
  const fromSyllabus = (input.syllabusText ?? '')
    .split(/\r?\n/)
    .map((l) => l.replace(/^[\s\-*\d.)]+/, '').trim())
    .filter((l) => l.length > 3)
    .slice(0, 6)
  const names = fromSyllabus.length >= 3 ? fromSyllabus : ['Foundations', 'Core ideas, by hand', 'Working with real examples', 'Putting it together']
  return {
    chapters: names.map((name, i) => ({
      title: titleCase(name),
      summary: `${tanglish ? 'Indha chapter la' : 'In this chapter'} we connect ${name.toLowerCase()} to ${topic.toLowerCase()} with examples drawn from your sources.`,
      lessons: [0, 1, 2].map((j) => ({
        title: j === 0 ? `Why ${name.toLowerCase()} matters` : j === 1 ? `${titleCase(name)} step by step` : `Practice: ${name.toLowerCase()}`,
        objectives: [`Explain ${name.toLowerCase()} in your own words`, `Recognise it in the source material`],
        practical:
          j === 2
            ? { kind: 'code', prompt: `Write a small program that demonstrates ${name.toLowerCase()}.`, starterCode: `# ${name}\ndef main():\n    pass\n\nmain()\n` }
            : { kind: 'quiz', prompt: `Two quick questions to check ${name.toLowerCase()}.` },
        tasks: [`Summarise the lesson in three lines`, `Screenshot your result for the recap`],
        diagramIdeas: [`Flow of ${name.toLowerCase()}`],
      })),
      project: {
        title: i === names.length - 1 ? `Capstone: build with ${topic}` : `Mini project ${i + 1}`,
        brief: `Apply ${name.toLowerCase()} to a small, real problem${input.targetAudience ? ` for ${input.targetAudience}` : ''}.`,
      },
    })),
  }
}

function runSteps(entry: DemoJob, defs: Array<[string, string]>, from: number, to: number, finalStatus: JobStatus, onDone?: () => void) {
  const existing = entry.job.steps.length
  entry.job.steps.push(...defs.map(([key, label]) => ({ key, label, status: 'pending' as StepStatus })))
  let index = 0
  let ticks = 0
  const perStep = 4
  window.clearInterval(entry.timer)
  entry.timer = window.setInterval(() => {
    const steps = entry.job.steps
    const current = steps[existing + index]
    if (!current) {
      window.clearInterval(entry.timer)
      entry.job.status = finalStatus
      entry.job.progress = to
      onDone?.()
      emit(entry)
      return
    }
    current.status = 'running'
    ticks++
    const sourceCount = entry.input.sources.length + (entry.input.syllabusText ? 1 : 0)
    current.detail = current.key === 'fetch' ? `${Math.min(sourceCount, Math.ceil((ticks / perStep) * sourceCount))} of ${sourceCount} sources` : 'Working…'
    if (ticks >= perStep) {
      current.status = 'done'
      current.detail = current.key === 'fetch' ? `${sourceCount} sources ready` : 'Done'
      ticks = 0
      index++
    }
    const doneFrac = (index + ticks / perStep) / defs.length
    entry.job.progress = Math.round(from + (to - from) * doneFrac)
    emit(entry)
  }, 650)
}

function delay<T>(value: T, ms = 450): Promise<T> {
  return new Promise((resolve) => window.setTimeout(() => resolve(value), ms))
}

function demoPreview(type: LinkSourceType, raw: string): SourcePreview {
  const url = normalizeUrl(raw)
  if (!url) return { title: raw, kind: type }
  const parts = url.pathname.split('/').filter(Boolean)
  switch (type) {
    case 'youtube': {
      const id = youtubeId(url)
      return { title: 'YouTube video', kind: 'video', durationS: 3 * 3600 + 252, thumbnailUrl: id ? `https://i.ytimg.com/vi/${id}/hqdefault.jpg` : undefined, author: 'YouTube' }
    }
    case 'github':
      return { title: parts.slice(0, 2).join('/') || 'GitHub repository', kind: 'repository', author: parts[0], description: 'Repository README, structure and code samples' }
    case 'reddit': {
      const slug = parts[parts.indexOf('comments') + 2]
      return { title: slug ? titleCase(slug.replace(/_/g, ' ')) : 'Reddit thread', kind: 'thread', author: parts[0] === 'r' ? `r/${parts[1]}` : 'Reddit' }
    }
    case 'arxiv': {
      const id = parts[parts.length - 1]?.replace(/\.pdf$/, '')
      return { title: id ? `arXiv ${id}` : 'arXiv paper', kind: 'paper', author: 'arXiv' }
    }
    case 'pdf_url':
      return { title: decodeURIComponent(parts[parts.length - 1] ?? 'document.pdf'), kind: 'pdf', author: url.hostname }
    default:
      return { title: url.hostname.replace(/^www\./, '') + (parts.length ? ` / ${decodeURIComponent(parts[parts.length - 1])}` : ''), kind: 'article', author: url.hostname }
  }
}

function demoClient(reason: string): CourseBuilderClient {
  const need = (id: string) => {
    const entry = demoJobs.get(id)
    if (!entry) throw new Error('Demo job not found')
    return entry
  }
  return {
    mode: 'demo',
    reason,
    previewSource: (type, url) => delay(demoPreview(type, url), 500),
    uploadDocuments: (files) =>
      delay(
        {
          accepted: files.map((f, i) => ({ documentId: `demo-doc-${Date.now().toString(36)}-${i}`, filename: f.name, sizeBytes: f.size })),
          rejected: [],
        },
        700,
      ),
    async createJob(input) {
      const id = `demo-${Date.now().toString(36)}`
      const entry: DemoJob = {
        input,
        listeners: new Set(),
        job: { id, title: input.title, status: 'queued', progress: 0, steps: [], sources: input.sources.map((s) => ({ ...s })), outline: null, createdAt: new Date().toISOString() },
      }
      demoJobs.set(id, entry)
      window.setTimeout(() => {
        entry.job.status = 'running'
        runSteps(entry, OUTLINE_STEPS, 2, 60, 'outline_ready', () => {
          entry.job.outline = demoOutline(input)
        })
      }, 300)
      return delay({ jobId: id }, 300)
    },
    listJobs: () => delay([...demoJobs.values()].map((e) => structuredClone(e.job)).reverse(), 200),
    getJob: async (id) => structuredClone(need(id).job),
    subscribe(id, onUpdate) {
      const entry = demoJobs.get(id)
      if (!entry) return () => {}
      entry.listeners.add(onUpdate)
      onUpdate(structuredClone(entry.job))
      return () => entry.listeners.delete(onUpdate)
    },
    async patchOutline(id, outline) {
      const entry = need(id)
      entry.job.outline = structuredClone(outline)
      emit(entry)
      return delay(structuredClone(entry.job), 250)
    },
    async generate(id) {
      const entry = need(id)
      entry.job.status = 'generating'
      runSteps(entry, GENERATE_STEPS, 60, 100, 'ready')
      emit(entry)
      return structuredClone(entry.job)
    },
    async publish(id) {
      const entry = need(id)
      entry.job.status = 'published'
      entry.job.courseId = `demo-${(entry.job.title ?? 'course').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')}`
      emit(entry)
      return delay(structuredClone(entry.job), 600)
    },
  }
}

// ---- Resolution ------------------------------------------------------------------------

let resolved: Promise<CourseBuilderClient> | null = null

/** The live API is configured but didn't answer; the UI offers a retry (or demo mode). */
export class CourseBuilderUnavailableError extends Error {}

/**
 * Picks the live client when the API is configured and the builder endpoint
 * answers; the demo simulation when no API is configured or the endpoint isn't
 * deployed. A timeout / network error rejects with
 * `CourseBuilderUnavailableError` and is NOT cached, so a retry can reach the
 * live API instead of being stuck in demo mode for the session.
 */
export function resolveCourseBuilder(force = false): Promise<CourseBuilderClient> {
  if (resolved && !force) return resolved
  const attempt = (async () => {
    if (API_BASE_URL === null) return demoClient('NEXT_PUBLIC_BOOKEY_API_URL is not set')
    const controller = new AbortController()
    const timer = window.setTimeout(() => controller.abort(), 8000)
    try {
      const res = await authorizedFetch(`${BASE}/jobs`, { signal: controller.signal })
      if (res.status === 404) return demoClient('The course-builder endpoint is not deployed on this API yet')
      if ((res.status === 401 || res.status === 403) && !getAuthToken()) return demoClient('Sign in as an admin to use the live builder')
      return liveClient()
    } catch {
      throw new CourseBuilderUnavailableError(`The course-builder API${API_BASE_URL ? ` at ${API_BASE_URL}` : ''} did not respond`)
    } finally {
      window.clearTimeout(timer)
    }
  })()
  resolved = attempt
  attempt.catch(() => {
    if (resolved === attempt) resolved = null
  })
  return attempt
}

/** Forces the demo simulation (e.g. when the live API fails mid-session). */
export function demoCourseBuilder(reason: string): CourseBuilderClient {
  const client = demoClient(reason)
  resolved = Promise.resolve(client)
  return client
}
