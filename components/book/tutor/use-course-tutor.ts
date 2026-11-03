'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useBookClient, useBookEvent } from '@/lib/book-client/react'
import type { LearningCourseId } from '@/lib/mock/learning'
import type { DiagramSpecPayload, QuestionSource } from '@/lib/contracts/book-events'
import { notifyOfflineChanged, putActivity } from '@/lib/offline/db'
import type { PreferredLanguage } from '@/lib/stores/personalization'
import { type ChatMessage, useCourseChat } from '@/lib/stores/course-chat'
import { MAX_QUESTION_LENGTH } from '@/lib/tutor/demo-tutor'
import type { BookLessonPage } from '@/types/book'

const EMPTY: ChatMessage[] = []
const FALLBACK_ERROR = 'Sorry, the tutor could not answer that right now. Please try again.'

interface UseCourseTutorOptions {
  courseId: LearningCourseId
  courseTitle: string
  page: BookLessonPage | undefined
  pageIndex: number
  language: PreferredLanguage
  reducedMotion: boolean
}

export interface CourseTutor {
  messages: ChatMessage[]
  busy: boolean
  /** Tokens revealed so far for the answer currently streaming. */
  reveal: { id: string; count: number } | null
  ask: (question: string, source?: QuestionSource, options?: AskOptions) => boolean
  clear: () => void
  /** The most recent exchange, for the in-book tutor card (includes structured diagrams). */
  latest: LatestAnswer | null
  dismissLatest: () => void
}

/** What a lesson command asked the tutor for (R1); plain questions have no kind. */
export type AskKind = 'quiz' | 'simpler'

export interface AskOptions {
  /** Shown in the chat/card instead of the instruction sent to the tutor. */
  display?: string
  kind?: AskKind
}

export interface LatestAnswer {
  questionId: string
  question: string
  kind?: AskKind
  source: QuestionSource
  text: string
  final: boolean
  offTopic?: boolean
  code?: string
  diagram?: string[]
  diagramSpec?: DiagramSpecPayload
}

interface PendingAnswer {
  courseId: string
  tutorId: string
  text: string
  tokens: number
  pageId: string
  chapterNumber: number
  question: string
}

function makeId() {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

/**
 * Chat tutor backed by the book client: `ask_question` goes out, and
 * `answer_chunk` / `sources` / `scope_refused` frames fill the thread.
 */
export function useCourseTutor({
  courseId,
  page,
  pageIndex,
  language,
  reducedMotion,
}: UseCourseTutorOptions): CourseTutor {
  const client = useBookClient()
  const messages = useCourseChat((s) => s.threads[courseId]) ?? EMPTY
  const [reveal, setReveal] = useState<CourseTutor['reveal']>(null)
  const pending = useRef(new Map<string, PendingAnswer>())
  const busy = messages.some((m) => m.status !== 'done')
  const [latest, setLatest] = useState<LatestAnswer | null>(null)
  const patchLatest = useCallback((questionId: string, patch: Partial<LatestAnswer>) => {
    setLatest((current) => (current && current.questionId === questionId ? { ...current, ...patch } : current))
  }, [])

  const finish = useCallback((questionId: string, patch: Partial<ChatMessage>) => {
    const entry = pending.current.get(questionId)
    if (!entry) return
    pending.current.delete(questionId)
    useCourseChat.getState().update(entry.courseId, entry.tutorId, { ...patch, status: 'done' })
    setReveal((current) => (current?.id === entry.tutorId ? null : current))
  }, [])

  useBookEvent('answer_chunk', ({ payload }) => {
    const entry = pending.current.get(payload.questionId)
    if (!entry) return
    entry.text += payload.text
    entry.tokens += 1
    patchLatest(payload.questionId, {
      text: entry.text,
      final: payload.final,
      ...(payload.final
        ? { code: payload.attachments?.code, diagram: payload.attachments?.diagram, diagramSpec: payload.attachments?.diagramSpec }
        : {}),
    })
    if (payload.final) {
      void putActivity({
        id: payload.questionId,
        courseId: entry.courseId,
        pageId: entry.pageId,
        chapterNumber: entry.chapterNumber,
        kind: 'question',
        question: entry.question,
        answer: entry.text,
        createdAt: Date.now(),
      }).then(() => notifyOfflineChanged(entry.courseId))
      finish(payload.questionId, {
        text: entry.text,
        code: payload.attachments?.code,
        diagram: payload.attachments?.diagram,
      })
      return
    }
    if (reducedMotion) return
    useCourseChat.getState().update(entry.courseId, entry.tutorId, { text: entry.text, status: 'streaming' })
    setReveal({ id: entry.tutorId, count: entry.tokens })
  })

  useBookEvent('sources', ({ payload }) => {
    const entry = pending.current.get(payload.questionId)
    const target = entry ?? null
    if (!target) return
    useCourseChat.getState().update(target.courseId, target.tutorId, {
      sources: payload.items
        .filter((item) => item.kind === 'page')
        .map((item) => ({ label: item.title, pageIndex: Number(item.ref) || 0 })),
    })
  })

  useBookEvent('scope_refused', ({ payload }) => {
    patchLatest(payload.questionId, { text: payload.message, final: true, offTopic: true })
    finish(payload.questionId, { text: payload.message, offTopic: true })
  })

  useBookEvent('error', ({ payload }) => {
    if (payload.code !== 'empty_question' && payload.code !== 'unknown_context') return
    for (const questionId of [...pending.current.keys()]) {
      patchLatest(questionId, { text: FALLBACK_ERROR, final: true })
      finish(questionId, { text: FALLBACK_ERROR })
    }
  })

  // Questions in flight when the reader unmounts would otherwise stay "thinking" forever.
  useEffect(() => {
    const entries = pending.current
    return () => {
      for (const questionId of [...entries.keys()]) {
        const entry = entries.get(questionId)
        if (entry) useCourseChat.getState().update(entry.courseId, entry.tutorId, { text: entry.text || FALLBACK_ERROR, status: 'done' })
      }
      entries.clear()
    }
  }, [])

  const ask = useCallback(
    (raw: string, source: QuestionSource = 'text', options: AskOptions = {}) => {
      const question = raw.trim().slice(0, MAX_QUESTION_LENGTH)
      if (!question || !page || busy) return false
      const shown = options.display?.trim() || question
      const { append } = useCourseChat.getState()
      const pageLabel = `Page ${pageIndex + 1} · ${page.title}`
      const tutorId = makeId()

      append(courseId, { id: makeId(), role: 'user', text: shown, createdAt: Date.now(), pageLabel, status: 'done' })
      append(courseId, { id: tutorId, role: 'tutor', text: '', createdAt: Date.now(), pageLabel, status: 'thinking' })

      const questionId = client.askQuestion({
        sessionId: null,
        source,
        text: question,
        context: { courseId, pageId: page.id, language },
      })
      pending.current.set(questionId, {
        courseId,
        tutorId,
        text: '',
        tokens: 0,
        pageId: page.id,
        chapterNumber: page.chapterNumber,
        question: shown,
      })
      setLatest({ questionId, question: shown, kind: options.kind, source, text: '', final: false })
      return true
    },
    [busy, page, courseId, pageIndex, language, client],
  )

  const clear = useCallback(() => {
    pending.current.clear()
    setReveal(null)
    useCourseChat.getState().clear(courseId)
  }, [courseId])

  const dismissLatest = useCallback(() => setLatest(null), [])

  return { messages, busy, reveal, ask, clear, latest, dismissLatest }
}
