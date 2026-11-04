'use client'

import { isBackendConfigured } from '@/lib/api/auth'
import type { LearningCourse } from '@/lib/mock/learning'
import { useSurveyCompleted } from '@/lib/stores/personalization'
import { storageKeys, useLocalStorage } from '@/lib/storage'

export interface SurveyRecord {
  completedAt: string
  answers: Record<string, string>
}

export type SurveyStore = Record<string, SurveyRecord>

const EMPTY: SurveyStore = {}
const LIVE = isBackendConfigured()

/**
 * With a backend, the survey counts as done once `/me/surveys` holds answers
 * for the course (the wizard saves them through `usePersonalization`).
 * Without one, answers live on this device and a course with progress
 * counts as already onboarded so "Resume" goes straight back to the book.
 */
export function useCourseSurvey(course: LearningCourse) {
  const [surveys, setSurveys] = useLocalStorage<SurveyStore>(storageKeys.surveys, EMPTY)
  const savedOnServer = useSurveyCompleted(course.id)
  const completed = LIVE ? savedOnServer : Boolean(surveys[course.id]) || course.completedPages > 0

  function saveSurvey(answers: Record<string, string>) {
    if (LIVE) return true
    return setSurveys((previous) => ({
      ...previous,
      [course.id]: { completedAt: new Date().toISOString(), answers },
    }))
  }

  return { completed, saveSurvey }
}

export function getBookHref(courseId: string, chapter?: number) {
  return chapter ? `/book/${courseId}?chapter=${chapter}` : `/book/${courseId}`
}
