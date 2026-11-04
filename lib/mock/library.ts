import { getLearningCourse, type LearningCourseId } from './learning'

/**
 * Fixed demo seed data for the Library and Search. "When" labels are static
 * relative strings so the demo never depends on today's date. Items the
 * learner creates later get a real timestamp instead.
 */

export interface PageRef {
  courseId: LearningCourseId
  /** 1-based chapter number. */
  chapter: number
  /** 1-based page within the chapter. */
  page: number
}

export interface LibraryBookmark extends PageRef {
  id: string
  excerpt: string
  /** Fixed label for seed items. */
  label?: string
  /** ISO timestamp for items created in this browser. */
  createdAt?: string
}

export interface LibraryNote extends PageRef {
  id: string
  quote?: string
  text: string
  label?: string
  updatedAt?: string
}

export interface ReadingSession extends PageRef {
  id: string
  label: string
  minutes: number
  summary: string
}

export interface PageExcerpt extends PageRef {
  id: string
  title: string
  text: string
  tags: string[]
}

export const seedBookmarks: LibraryBookmark[] = []

export const seedNotes: LibraryNote[] = []

export const seedReadingHistory: ReadingSession[] = []

/** Searchable demo page text. Kept short; the reader renders the full page. */
export const pageExcerpts: PageExcerpt[] = []

/**
 * Link to a specific page. The reader currently opens the saved resume
 * position; chapter/page params are already included so deep links work once
 * the reader reads them. Unavailable demo courses link to their overview.
 */
export function pageHref({ courseId, chapter, page }: PageRef) {
  const course = getLearningCourse(courseId)
  if (!course?.available) return `/courses/${courseId}`
  return `/book/${courseId}?chapter=${chapter}&page=${page}`
}

export function describePage({ courseId, chapter, page }: PageRef) {
  const course = getLearningCourse(courseId)
  const chapterTitle = course?.chapters.find((entry) => entry.number === chapter)?.title
  return {
    courseTitle: course?.title ?? 'Your book',
    location: `Chapter ${chapter}${chapterTitle ? ` · ${chapterTitle}` : ''} · Page ${page}`,
  }
}
