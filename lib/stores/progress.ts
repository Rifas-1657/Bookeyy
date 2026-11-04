'use client'

import { useMemo } from 'react'
import { apiJson, isBackendConfigured } from '@/lib/api/auth'
import type { LearningCourse } from '@/lib/mock/learning'
import { createRemoteResource, useRemote } from './remote'

interface ServerProgressSummary {
  course_id: string
  completed_pages: number
  total_pages: number
}

const LIVE = isBackendConfigured()

/** Completed page count per course for the signed-in user (`/me/progress`). */
const remoteProgress = createRemoteResource<Record<string, number>>('/me/progress', {}, (raw) =>
  Object.fromEntries((raw as ServerProgressSummary[]).map((item) => [item.course_id, item.completed_pages])),
)

export interface PersonalBookSummary {
  ready: boolean
  courseId: string
  title: string
  chapters: LearningCourse['chapters']
}

/** The learner's own copy of the Python book (`/api/books/personal`). */
const remotePersonal = createRemoteResource<PersonalBookSummary | null>('/api/books/personal', null, (raw) => {
  const book = raw as Partial<PersonalBookSummary> & { exists: boolean }
  return book.exists && book.ready ? (book as PersonalBookSummary) : null
})

export function refreshPersonalBook() {
  void remotePersonal.load(true)
}

export function usePersonalBook() {
  return useRemote(remotePersonal, LIVE).data
}

const completedThisSession = new Set<string>()

/**
 * Records a finished page. No-op without a backend (demo progress is seeded)
 * and for pages already sent this session, so page turns stay cheap.
 */
export function markPageComplete(courseId: string, pageId: string) {
  if (!LIVE) return
  const key = `${courseId}/${pageId}`
  if (completedThisSession.has(key)) return
  completedThisSession.add(key)
  apiJson(`/me/progress/${encodeURIComponent(courseId)}/${encodeURIComponent(pageId)}/complete`, { method: 'POST' })
    .then(() => remoteProgress.load(true))
    .catch(() => completedThisSession.delete(key))
}

/** Courses with `completedPages` replaced by the learner's real progress when a backend is configured. */
export function useLiveCourses<T extends LearningCourse>(courses: T[]): T[] {
  const { data } = useRemote(remoteProgress, LIVE)
  const personal = usePersonalBook()
  return useMemo(
    () =>
      LIVE
        ? courses.map((course) => ({
            ...course,
            completedPages: data[course.id] ?? 0,
            ...(course.id === 'python' && personal ? { title: personal.title, chapters: personal.chapters } : {}),
          }))
        : courses,
    [courses, data, personal],
  )
}

export function useLiveCourse<T extends LearningCourse>(course: T): T {
  const [live] = useLiveCourses(useMemo(() => [course], [course]))
  return live
}
