import { learningCourses } from '@/lib/mock/learning'
import type { AdminCourse, AdminDocument, DemoMember, TutorSettings } from './types'

/** Fixed "today" for the demo so every timestamp stays deterministic. */
export const DEMO_TODAY = '2026-10-10T12:00:00Z'

const EDIT_DATES = ['2026-10-08T14:20:00Z', '2026-10-03T09:45:00Z', '2026-09-26T16:10:00Z', '2026-09-18T11:30:00Z']

export const seedCourses: AdminCourse[] = []

export const seedMembers: DemoMember[] = []

export const seedDocuments: AdminDocument[] = []

export const defaultTutorSettings: TutorSettings = {
  narrationLanguage: 'en-US',
  speakingSpeed: 1,
  explanationStyle: 'balanced',
  captions: true,
  animation: 'full',
  voice: 'ember',
  applyToAllCourses: true,
  scopedCourseIds: [],
}

export const overviewMetrics = {
  activeReaders: 27,
  booksOpened: 184,
  questionsAsked: 412,
}

export const activitySeries: { label: string; opened: number; questions: number }[] = []

export const recentAdminActivity: { id: string; text: string; at: string }[] = []
