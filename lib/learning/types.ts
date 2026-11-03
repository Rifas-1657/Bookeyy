import type { ConceptStatus, Lang, LearnerLevel, QuizQuestion, TeachingDepth, TeachingStyle } from './model'

/** Shapes of `/api/me/learning/*` (backend/app/api/learning.py); the local model returns the same. */

export interface ConceptView {
  id: string
  label: string
  pageId: string | null
  chapter: number | null
  mastery: number
  attempts: number
  correct: number
  confusion: number
  status: ConceptStatus
  lastSeenAt: string | null
}

export interface ChapterMastery {
  number: number
  title: string
  mastery: number
  seen: number
  total: number
  mastered: number
}

export interface DueReview {
  courseId: string
  courseTitle: string
  conceptId: string
  label: string
  pageId: string | null
  chapter: number | null
  mastery: number
  lapses: number
  retrievability: number
  lastReviewed: string | null
}

export interface LearnerSummary {
  courseId: string
  level: LearnerLevel
  language: Lang
  overallMastery: number | null
  concepts: ConceptView[]
  chapters: ChapterMastery[]
  weak: ConceptView[]
  strong: ConceptView[]
  recentConfusion: number
  style: { arm: number; style: TeachingStyle; depth: TeachingDepth; expected: number; bucket: string; evidence: number }
  dueReviews: DueReview[]
  model: 'bkt'
}

export interface CourseLearning {
  courseId: string
  title: string
  concepts: number
  seen: number
  mastery: number | null
  mastered: number
  due: number
}

export interface QuizResponse {
  quizId: string | null
  mode: 'page' | 'review'
  questions: QuizQuestion[]
}

export interface GradeResponse {
  questionId: string
  correct: boolean
  score: number
  gradedBy: 'option' | 'keywords' | 'output' | 'rubric'
  feedback: string
  answer: string
  explanation: string
  conceptId: string
  conceptLabel: string
  counted: boolean
  mastery: number | null
}
