import { getBookPages } from '@/lib/mock/book-pages'
import type { LearningCourseId } from '@/lib/mock/learning'
import type { BookLessonPage, DiagramKind, DiagramSpec, DiagramTone, LessonPageId } from '@/types/book'

/**
 * Loads every reader page for a course. Uses the Bookey API when
 * NEXT_PUBLIC_BOOKEY_API_URL is set (BOOKEY_API_INTERNAL_URL overrides it for
 * server-to-server calls, e.g. inside Docker); otherwise the seeded mock pages.
 *
 * A configured backend that fails throws, so the route error boundary shows
 * a real error instead of silently serving demo content.
 */

export interface BackendPage {
  id: string
  course_id: string
  chapter_number: number
  page_number: number
  title: string
  narration_text: string
  paragraphs: string[]
  key_terms: string[]
  code_example: BookLessonPage['codeExample']
  diagram_spec: BookLessonPage['diagramSpec']
  cues: NonNullable<BookLessonPage['cues']>
  practice_prompts: string[]
  analogies: Partial<BookLessonPage['analogies']>
  estimated_minutes: number
}

function apiBaseUrl() {
  const url = process.env.BOOKEY_API_INTERNAL_URL || process.env.NEXT_PUBLIC_BOOKEY_API_URL
  return url ? url.replace(/\/$/, '') : null
}

const TONES = new Set<DiagramTone>(['neutral', 'highlight', 'valid', 'invalid'])
/** Older seed/generator names for the same diagram kinds. */
const KIND_ALIASES: Record<string, DiagramKind> = {
  'variable-box': 'labeled-boxes',
  'data-types': 'type-tree',
  'input-output': 'io-flow',
}

/** Coerces backend diagram specs into values the renderer understands. */
function normalizeDiagram(spec: DiagramSpec): DiagramSpec {
  if (!spec || !Array.isArray(spec.nodes)) return spec
  return {
    ...spec,
    kind: KIND_ALIASES[spec.kind as string] ?? spec.kind,
    nodes: spec.nodes.map((node) => ({
      ...node,
      tone: node.tone && TONES.has(node.tone) ? node.tone : 'neutral',
    })),
  }
}

export function toLessonPage(page: BackendPage, position: number): BookLessonPage {
  const neutral = page.analogies.neutral ?? ''
  return {
    id: page.id as LessonPageId,
    courseId: page.course_id as LearningCourseId,
    chapterId: `${page.course_id}-ch${page.chapter_number}`,
    chapterNumber: page.chapter_number,
    // The API numbers pages within a chapter; the reader wants the course-wide position.
    pageNumber: position + 1,
    title: page.title,
    narrationText: page.narration_text,
    paragraphs: page.paragraphs,
    keyTerms: page.key_terms,
    codeExample: page.code_example,
    diagramSpec: normalizeDiagram(page.diagram_spec),
    cues: page.cues,
    estimatedMinutes: page.estimated_minutes,
    practicePrompts: page.practice_prompts,
    analogies: {
      neutral,
      cricket: page.analogies.cricket ?? neutral,
      movies: page.analogies.movies ?? neutral,
    },
  }
}

export async function loadBookPages(courseId: string): Promise<BookLessonPage[]> {
  const base = apiBaseUrl()
  if (!base) return getBookPages(courseId)

  const res = await fetch(`${base}/api/courses/${encodeURIComponent(courseId)}/pages`, {
    // Admins edit pages; readers must see the current version.
    cache: 'no-store',
  })
  if (res.status === 404) return []
  if (!res.ok) throw new Error(`Failed to load pages for ${courseId}: ${res.status}`)
  const pages = (await res.json()) as BackendPage[]
  return pages.map(toLessonPage)
}
