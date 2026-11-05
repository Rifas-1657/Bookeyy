'use client'

import { useCallback, useSyncExternalStore } from 'react'
import { apiJson, isBackendConfigured } from '@/lib/api/auth'
import { usePersonalBook } from '@/lib/stores/progress'
import { storageKeys, useLocalStorage } from '@/lib/storage'
import { createRemoteResource, useRemote } from './remote'

export type LearnerLevel = 'new' | 'basics' | 'projects'
export type Interest = 'movies' | 'cricket' | 'games' | 'music' | 'cooking' | 'cars' | 'anime' | 'business' | 'science'
export type MovieGenre = 'action' | 'comedy' | 'thriller' | 'romance' | 'sci-fi' | 'animation'
export type ExplanationStyle = 'example' | 'diagram' | 'code'
/** Bookey narrates and answers only in these two languages. */
export type PreferredLanguage = 'tanglish' | 'english'
export type SessionPace = 'calm' | 'balanced' | 'fast'
export type LearningGoal = 'job' | 'college' | 'automation' | 'data' | 'web' | 'ai' | 'hobby'
export type DailyMinutes = '10' | '20' | '30' | '60'

export interface AccessibilityPreferences {
  reducedMotion: boolean
  largerText: boolean
  captions: boolean
}

export interface Personalization {
  level: LearnerLevel
  interests: Interest[]
  /** Only meaningful when `interests` includes `movies`. */
  movieGenre: MovieGenre | null
  style: ExplanationStyle
  language: PreferredLanguage
  pace: SessionPace
  goal: LearningGoal | null
  dailyMinutes: DailyMinutes | null
  /** What the learner dreams of building, in their own words. */
  project: string | null
  accessibility: AccessibilityPreferences
}

export interface PersonalizationRecord extends Personalization {
  completedAt: string
}

type PersonalizationStore = Partial<Record<string, PersonalizationRecord>>

/** Used by the viewer whenever a learner hasn't finished the survey for a course. */
export const defaultPersonalization: Personalization = {
  level: 'new',
  interests: [],
  movieGenre: null,
  style: 'example',
  language: 'tanglish',
  pace: 'balanced',
  goal: null,
  dailyMinutes: null,
  project: null,
  accessibility: { reducedMotion: false, largerText: false, captions: false },
}

const EMPTY: PersonalizationStore = {}

export function isSupportedLanguage(value: unknown): value is PreferredLanguage {
  return value === 'tanglish' || value === 'english'
}

function normalize(input: Personalization): Personalization {
  const interests = Array.from(new Set(input.interests))
  return {
    ...input,
    interests,
    movieGenre: interests.includes('movies') ? input.movieGenre : null,
  }
}

interface ServerSurvey {
  course_id: string
  answers: Personalization
  completed_at: string
}

/** Server copy of every course survey for the signed-in user (`/me/surveys`). */
const remoteSurveys = createRemoteResource<PersonalizationStore>('/me/surveys', EMPTY, (raw) =>
  Object.fromEntries(
    (raw as ServerSurvey[]).map((item) => [item.course_id, { ...item.answers, completedAt: item.completed_at }]),
  ),
)

const LIVE = isBackendConfigured()

/** Survey store: `/me/surveys` when a backend is configured, else this device. */
function useSurveyStore(courseId: string) {
  const [localStore, setLocalStore] = useLocalStorage<PersonalizationStore>(storageKeys.personalization, EMPTY)
  const remote = useRemote(remoteSurveys, LIVE)

  const save = useCallback(
    (next: Personalization) => {
      const record = { ...normalize(next), completedAt: new Date().toISOString() }
      if (!LIVE) return setLocalStore((previous) => ({ ...previous, [courseId]: record }))
      const { completedAt: _at, ...answers } = record
      void remoteSurveys.mutate(
        (previous) => ({ ...previous, [courseId]: record }),
        () => apiJson(`/me/surveys/${encodeURIComponent(courseId)}`, { method: 'PUT', body: JSON.stringify(answers) }),
        'Could not save your survey',
      )
      return true
    },
    [courseId, setLocalStore],
  )

  const reset = useCallback(() => {
    const drop = (previous: PersonalizationStore) => {
      const { [courseId]: _removed, ...rest } = previous
      return rest
    }
    if (!LIVE) return setLocalStore(drop)
    void remoteSurveys.mutate(drop, () => apiJson(`/me/surveys/${encodeURIComponent(courseId)}`, { method: 'DELETE' }), 'Could not reset your survey')
    return true
  }, [courseId, setLocalStore])

  return { store: LIVE ? remote.data : localStore, ready: !LIVE || remote.status === 'ready' || remote.status === 'error', save, reset }
}

/** True once this course's survey is saved (on the server when one is configured). */
export function useSurveyCompleted(courseId: string) {
  const { store } = useSurveyStore(courseId)
  return Boolean(store[courseId])
}

/**
 * Per-course personalization answers. Always returns a complete object,
 * falling back to `defaultPersonalization` for courses that haven't been
 * personalized yet. `ready` is false while server answers are still loading.
 */
export function usePersonalization(courseId: string) {
  const { store, ready, save, reset } = useSurveyStore(courseId)
  const ownCopy = usePersonalBook()?.courseId === courseId
  const record = store[courseId] ?? (ownCopy ? store.python : undefined)

  const personalization: Personalization = record
    ? {
        ...defaultPersonalization,
        ...record,
        // Older saves may hold a language Bookey no longer supports (e.g. 'tamil').
        language: isSupportedLanguage(record.language) ? record.language : defaultPersonalization.language,
        accessibility: { ...defaultPersonalization.accessibility, ...record.accessibility },
      }
    : defaultPersonalization

  return {
    personalization,
    completed: Boolean(record),
    completedAt: record?.completedAt ?? null,
    ready,
    save,
    reset,
  }
}

const noopSubscribe = () => () => {}

/** False during SSR and hydration, true once client-only storage can be read. */
export function useHydrated() {
  return useSyncExternalStore(
    noopSubscribe,
    () => true,
    () => false,
  )
}
