import type { Metadata } from 'next'
import { notFound } from 'next/navigation'
import { BookReader } from '@/components/book/book-reader'
import { GeneratedBookReader } from '@/components/book/generated-book-reader'
import { PersonalBookGate } from '@/components/book/personal-book-gate'
import { BookClientProvider } from '@/lib/book-client/react'
import { loadBookPages } from '@/lib/api/pages'
import { getLearningCourse, learningCourses } from '@/lib/mock/learning'

interface BookPageProps {
  params: Promise<{ courseId: string }>
  searchParams: Promise<{ chapter?: string; page?: string }>
}

export function generateStaticParams() {
  return learningCourses.map((course) => ({ courseId: course.id }))
}

export async function generateMetadata({ params }: BookPageProps): Promise<Metadata> {
  const { courseId } = await params
  const course = getLearningCourse(courseId)
  if (!course && process.env.NEXT_PUBLIC_BOOKEY_API_URL) return { title: 'Reading your book' }
  return {
    title: course ? `Reading ${course.title}` : 'Book not found',
    description: course ? `Read ${course.title} as an interactive, narrated book.` : undefined,
  }
}

function toPositiveInt(value: string | undefined) {
  if (!value) return undefined
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined
}

export default async function BookReaderPage({ params, searchParams }: BookPageProps) {
  const [{ courseId }, query] = await Promise.all([params, searchParams])
  const course = getLearningCourse(courseId)
  if (!course) {
    // Generated books (learner / course builder) live only in the API and may be
    // private, so they load in the browser with the reader's own session.
    if (!process.env.NEXT_PUBLIC_BOOKEY_API_URL) notFound()
    return (
      <GeneratedBookReader
        courseId={courseId}
        initialChapter={toPositiveInt(query.chapter)}
        initialPage={toPositiveInt(query.page)}
      />
    )
  }
  if (course.id === 'python' && process.env.NEXT_PUBLIC_BOOKEY_API_URL) return <PersonalBookGate />
  const pages = await loadBookPages(course.id)

  return (
    <BookClientProvider>
      <BookReader
        course={{ id: course.id, title: course.title, chapters: course.chapters }}
        pages={pages}
        initialChapter={toPositiveInt(query.chapter)}
        initialPage={toPositiveInt(query.page)}
      />
    </BookClientProvider>
  )
}
