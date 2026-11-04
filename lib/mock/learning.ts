import type { BookCover } from '@/types/learning'

/**
 * Demo learning catalog for the learner workspace. Every derived number
 * (percent, current chapter, page position) is computed from chapter page
 * counts so the dashboard, search, and reader always agree.
 */

export type LearningCourseId = string

export interface LearningChapter {
  number: number
  title: string
  summary: string
  pages: number
}

export type CourseCategory = 'Programming' | 'Data & AI' | 'Web'
export type CourseLevel = 'Beginner' | 'Intermediate' | 'Advanced'

export interface LearningCourse {
  id: LearningCourseId
  title: string
  subtitle: string
  description: string
  level: CourseLevel
  category: CourseCategory
  /** Primary language taught, shown as a badge. */
  language: string
  /** Unavailable courses explain why instead of starting. */
  available: boolean
  author: string
  cover: BookCover
  chapters: LearningChapter[]
  outcomes: string[]
  project: { title: string; description: string }
  topics: string[]
  tutorStyle: string
  /** Pages completed, read in order from page 1. */
  completedPages: number
  lastOpened: string | null
  minutesPerPage: number
  resumeExcerpt: string
}

function chapters(entries: [string, number, string][]): LearningChapter[] {
  return entries.map(([title, pages, summary], index) => ({ number: index + 1, title, pages, summary }))
}

export const learningCourses: LearningCourse[] = [
  {
    id: 'python',
    title: 'Python: The Complete Book',
    subtitle: 'From your first line of code to your own project, shaped around you.',
    description:
      'A complete Python book that rebuilds itself around you. Answer a few questions and Bookey picks your chapters, your pace and your examples, then teaches every page out loud.',
    level: 'Beginner',
    category: 'Programming',
    language: 'Tanglish',
    available: true,
    author: 'Bookey',
    cover: { base: '#2A2117', spine: '#120E09', accent: '#D4AE66', text: '#F3EAD8' },
    chapters: chapters([
      ['Meet Python', 4, 'What programming is, and your first working programs.'],
      ['Variables and Data Types', 5, 'Name values and learn the kinds of data Python holds.'],
      ['Operators and Expressions', 4, 'Calculate, compare and combine values.'],
      ['Strings in Depth', 4, 'Slice, clean and format text like a professional.'],
      ['Making Decisions', 4, 'Let your program choose a path.'],
      ['Loops', 4, 'Repeat work safely with for and while.'],
      ['Lists and Tuples', 4, 'Keep ordered collections of values.'],
      ['Dictionaries and Sets', 4, 'Look things up by key and keep unique items.'],
      ['Functions', 6, 'Package logic into reusable, testable pieces.'],
      ['Files and Errors', 5, 'Save data and handle things going wrong.'],
      ['Modules and the Standard Library', 6, 'Reuse code that already exists.'],
      ['Object-Oriented Python', 5, 'Model real things with classes.'],
      ['Pythonic Code', 5, 'Write Python the way experienced developers do.'],
      ['Debugging and Testing', 4, 'Find bugs quickly and prove your code works.'],
      ['Automation with Python', 3, 'Make the computer do your boring work.'],
      ['Working with Data', 3, 'Turn raw numbers into answers.'],
      ['Python for the Web', 3, 'Understand how Python talks to the web.'],
      ['First Steps in AI', 3, 'See how machines learn from data, in plain Python.'],
      ['Your Capstone Project', 4, 'Plan and build a complete program of your own.'],
    ]),
    outcomes: [
      'Write real Python programs on your own, without copying from a template',
      'Choose the right data structure for a problem',
      'Split code into functions, classes and modules that stay readable',
      'Save data, handle errors, test and debug your code',
      'Plan and finish a complete project of your own',
    ],
    project: {
      title: 'Your capstone project',
      description: 'A complete program built around your own idea, planned, written, tested and polished chapter by chapter.',
    },
    topics: ['Python 3', 'Code you run on the page', 'Data structures', 'Functions', 'Files', 'Object-oriented Python', 'Testing'],
    tutorStyle: 'Patient and example-first: explains one idea at a time in your language, then asks you to try it.',
    completedPages: 0,
    lastOpened: null,
    minutesPerPage: 4,
    resumeExcerpt: '',
  },
]

export function getLearningCourse(id: string): LearningCourse | undefined {
  return learningCourses.find((course) => course.id === id)
}

export interface CoursePosition {
  totalPages: number
  completedPages: number
  percent: number
  /** Chapter containing the next unread page (the last chapter when finished). */
  chapter: LearningChapter
  /** 1-based page within that chapter. */
  pageInChapter: number
  /** 1-based page across the whole book. */
  bookPage: number
  started: boolean
  finished: boolean
}

export function getCoursePosition(course: LearningCourse): CoursePosition {
  const totalPages = course.chapters.reduce((sum, chapter) => sum + chapter.pages, 0)
  const completedPages = Math.min(course.completedPages, totalPages)
  const finished = completedPages >= totalPages
  const bookPage = finished ? totalPages : completedPages + 1

  let pagesBefore = 0
  let chapter = course.chapters[course.chapters.length - 1]
  for (const candidate of course.chapters) {
    if (bookPage <= pagesBefore + candidate.pages) {
      chapter = candidate
      break
    }
    pagesBefore += candidate.pages
  }

  return {
    totalPages,
    completedPages,
    percent: totalPages === 0 ? 0 : Math.round((completedPages / totalPages) * 100),
    chapter,
    pageInChapter: bookPage - pagesBefore,
    bookPage,
    started: completedPages > 0,
    finished,
  }
}

export function getRemainingMinutes(course: LearningCourse) {
  const { totalPages, completedPages } = getCoursePosition(course)
  return (totalPages - completedPages) * course.minutesPerPage
}

/** Most recently opened in-progress course, used for "Continue your book". */
export const featuredCourseId: LearningCourseId = 'python'

export interface PracticeTask {
  id: string
  courseId: LearningCourseId
  title: string
  detail: string
  minutes: number
  kind: 'Code' | 'Quiz' | 'Recall'
}

export const todaysPractice: PracticeTask[] = []

/** Demo minutes per weekday (Mon → Sun). */
export const weeklyActivity = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day) => ({ day, minutes: 0 }))

export interface ActivityEvent {
  id: string
  courseId: LearningCourseId
  kind: 'page' | 'quiz' | 'chapter' | 'note'
  title: string
  when: string
}

export const recentActivity: ActivityEvent[] = []

export const demoStreakDays = 0

export type ChapterStatus = 'completed' | 'in-progress' | 'available' | 'locked'

export interface ChapterProgress {
  chapter: LearningChapter
  status: ChapterStatus
  pagesRead: number
  minutes: number
}

/** Chapters open in order: finished ones plus the next unread chapter. */
export function getChapterProgress(course: LearningCourse): ChapterProgress[] {
  let pagesBefore = 0
  let reachedNext = false
  return course.chapters.map((chapter) => {
    const pagesRead = Math.max(0, Math.min(chapter.pages, course.completedPages - pagesBefore))
    pagesBefore += chapter.pages
    let status: ChapterStatus
    if (!course.available) status = 'locked'
    else if (pagesRead === chapter.pages) status = 'completed'
    else if (!reachedNext) {
      status = pagesRead > 0 ? 'in-progress' : 'available'
      reachedNext = true
    } else status = 'locked'
    return { chapter, status, pagesRead, minutes: chapter.pages * course.minutesPerPage }
  })
}

export function getTotalMinutes(course: LearningCourse) {
  return getCoursePosition(course).totalPages * course.minutesPerPage
}

export function formatMinutes(minutes: number) {
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`
}
