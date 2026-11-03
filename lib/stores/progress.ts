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
  return useMemo(
    () => (LIVE ? courses.map((course) => ({ ...course, completedPages: data[course.id] ?? 0 })) : courses),
    [courses, data],
  )
}

export function useLiveCourse<T extends LearningCourse>(course: T): T {
  const [live] = useLiveCourses(useMemo(() => [course], [course]))
  return live
}
