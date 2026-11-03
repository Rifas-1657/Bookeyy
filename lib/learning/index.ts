'use client'

/**
 * R3 adaptive learning — one entry point for the reader, dashboard and progress page.
 *
 * Backend configured → `/api/me/learning/*` (the learner model lives in the database,
 * per signed-in user). No backend → the same model runs in this browser
 * (lib/learning/local-model.ts, localStorage), so the demo still adapts.
 *
 * What is recorded where (no double counting):
 *   questions + "explain simpler"  → the tutor records them (server: tutor graph; mock: mock client)
 *   quiz / review answers          → the grader records them
 *   page completed                 → the progress endpoint (server) / the reader (mock)
 *   code runs, "repeat", re-listen → `recordEvidence` below
 */
import { useCallback, useEffect, useState } from 'react'
import { apiJson, isBackendConfigured, useSession } from '@/lib/api/auth'
import { getBookPages } from '@/lib/mock/book-pages'
import { getLearningCourse } from '@/lib/mock/learning'
import { readStorage, storageKeys, writeStorage } from '@/lib/storage'
import {
  LEARNING_EVENT,
  localDueReviews,
  localGrade,
  localQuiz,
  localSummary,
  recordLocal,
} from './local-model'
import { LEVEL_PRIOR, type EvidenceKind, type Lang, type LearnerLevel, type QuizPage } from './model'
import type { CourseLearning, DueReview, GradeResponse, LearnerSummary, QuizResponse } from './types'

export type { CourseLearning, DueReview, GradeResponse, LearnerSummary, QuizResponse } from './types'
export type { QuizQuestion } from './model'

const LIVE = isBackendConfigured()
const CLIENT_KINDS = new Set<EvidenceKind>(['code_run', 'repeat', 'relisten'])

export function isLiveLearning() {
  return LIVE
}

function notifyChanged() {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(LEARNING_EVENT))
}

function surveyFor(courseId: string): { level: LearnerLevel; language: Lang } {
  const store = readStorage<Record<string, { level?: string; language?: string }>>(storageKeys.personalization, {})
  const s = store[courseId]
  const level = s?.level && s.level in LEVEL_PRIOR ? (s.level as LearnerLevel) : 'new'
  return { level, language: s?.language === 'english' ? 'english' : 'tanglish' }
}

const courseTitle = (id: string) => getLearningCourse(id)?.title ?? id

/* ------------------------------ evidence ------------------------------ */

interface QueuedEvent {
  kind: EvidenceKind
  pageId: string
  correct?: boolean
  text?: string
}

const queues = new Map<string, QueuedEvent[]>()
let flushTimer: number | null = null

async function flush() {
  flushTimer = null
  const batches = [...queues.entries()]
  queues.clear()
  for (const [courseId, events] of batches) {
    for (let i = 0; i < events.length; i += 50) {
      try {
        await apiJson(`/api/me/learning/${encodeURIComponent(courseId)}/events`, {
          method: 'POST',
          body: JSON.stringify({ events: events.slice(i, i + 50) }),
        })
      } catch {
        /* evidence is best-effort: never interrupt the lesson */
      }
    }
  }
  notifyChanged()
}

if (typeof window !== 'undefined') window.addEventListener('pagehide', () => void flush())

/** Records one piece of learner evidence (batched to the server, or applied locally). */
export function recordEvidence(courseId: string, page: QuizPage, kind: EvidenceKind, extra: { correct?: boolean; text?: string } = {}) {
  if (!LIVE) {
    if (kind === 'page_complete') {
      // Like the server: only the first completion of a page counts.
      const key = `${courseId}/${page.id}`
      const done = readStorage<string[]>(storageKeys.learnerModel + ':completed', [])
      if (done.includes(key)) return
      writeStorage(storageKeys.learnerModel + ':completed', [...done, key].slice(-500))
    }
    recordLocal(courseId, { kind, page, correct: extra.correct, text: extra.text }, surveyFor(courseId).level)
    return
  }
  if (!CLIENT_KINDS.has(kind)) return
  const list = queues.get(courseId) ?? []
  list.push({ kind, pageId: page.id, ...(extra.correct !== undefined ? { correct: extra.correct } : {}), ...(extra.text ? { text: extra.text.slice(0, 500) } : {}) })
  queues.set(courseId, list)
  if (flushTimer === null && typeof window !== 'undefined') flushTimer = window.setTimeout(() => void flush(), 1500)
}

/* -------------------------------- quizzes -------------------------------- */

function localPages(courseId: string, fallback: QuizPage[]) {
  const seeded = getBookPages(courseId)
  return seeded.length ? seeded : fallback
}

export async function startQuiz(
  courseId: string,
  page: QuizPage | null,
  lang: Lang,
  mode: 'page' | 'review' = 'page',
  pages: QuizPage[] = [],
): Promise<QuizResponse> {
  if (!LIVE) return localQuiz(courseId, page, lang, mode, localPages(courseId, pages))
  return apiJson<QuizResponse>(`/api/me/learning/${encodeURIComponent(courseId)}/quiz`, {
    method: 'POST',
    body: JSON.stringify({ pageId: page?.id ?? null, mode, language: lang, n: 3 }),
  })
}

export async function gradeAnswer(
  courseId: string,
  questionId: string,
  answer: string,
  opts: { responseMs?: number; lang: Lang; page?: QuizPage; pages?: QuizPage[]; pageId?: string },
): Promise<GradeResponse> {
  if (!LIVE) {
    const page = opts.page?.id === opts.pageId ? opts.page : localPages(courseId, opts.pages ?? []).find((p) => p.id === opts.pageId) ?? opts.page
    const res = localGrade(courseId, questionId, answer, opts.responseMs, opts.lang, page, surveyFor(courseId).level)
    if (!res) throw new Error('This question has expired. Start a new quiz.')
    return res
  }
  const res = await apiJson<GradeResponse>(`/api/me/learning/${encodeURIComponent(courseId)}/quiz/grade`, {
    method: 'POST',
    body: JSON.stringify({ questionId, answer, responseMs: opts.responseMs, language: opts.lang }),
  })
  notifyChanged()
  return res
}

/* --------------------------------- hooks --------------------------------- */

type Status = 'idle' | 'loading' | 'ready' | 'error'

function useLearningData<T>(load: (() => Promise<T> | T) | null, deps: unknown[]): { data: T | null; status: Status; refresh: () => void } {
  const [state, setState] = useState<{ data: T | null; status: Status }>({ data: null, status: 'idle' })
  const [tick, setTick] = useState(0)
  const refresh = useCallback(() => setTick((n) => n + 1), [])
  useEffect(() => {
    const onChange = () => setTick((n) => n + 1)
    window.addEventListener(LEARNING_EVENT, onChange)
    return () => window.removeEventListener(LEARNING_EVENT, onChange)
  }, [])
  useEffect(() => {
    if (!load) {
      setState({ data: null, status: 'idle' })
      return
    }
    let alive = true
    setState((s) => ({ data: s.data, status: s.data ? 'ready' : 'loading' }))
    Promise.resolve()
      .then(load)
      .then((data) => alive && setState({ data, status: 'ready' }))
      .catch(() => alive && setState((s) => ({ data: s.data, status: 'error' })))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, Boolean(load)])
  return { ...state, refresh }
}

/** This learner's model for one course (mastery per concept/chapter, weak spots, style, due reviews). */
export function useLearnerSummary(courseId: string | null, chapterTitle: (n: number) => string = (n) => `Chapter ${n}`) {
  const session = useSession()
  const enabled = Boolean(courseId) && (!LIVE || Boolean(session))
  return useLearningData<LearnerSummary>(
    enabled
      ? () =>
          LIVE
            ? apiJson<LearnerSummary>(`/api/me/learning/${encodeURIComponent(courseId!)}`)
            : localSummary(courseId!, { ...surveyFor(courseId!), chapterTitle, courseTitle })
      : null,
    [courseId, session?.userId],
  )
}

/** Concepts due for spaced review across every course. */
export function useDueReviews() {
  const session = useSession()
  const enabled = !LIVE || Boolean(session)
  return useLearningData<DueReview[]>(
    enabled
      ? () => (LIVE ? apiJson<{ due: DueReview[] }>('/api/me/learning/reviews').then((r) => r.due) : localDueReviews(courseTitle))
      : null,
    [session?.userId],
  )
}

/** Courses the learner has learner-model data for (live mode). */
export function useLearningOverview() {
  const session = useSession()
  return useLearningData<CourseLearning[]>(
    LIVE && session ? () => apiJson<{ courses: CourseLearning[] }>('/api/me/learning').then((r) => r.courses) : null,
    [session?.userId],
  )
}
