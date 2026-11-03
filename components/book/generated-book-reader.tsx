'use client'

import { useEffect, useState } from 'react'
import { BookReader } from '@/components/book/book-reader'
import { BookMissing, BookReaderSkeleton } from '@/components/book/book-states'
import { BookClientProvider } from '@/lib/book-client/react'
import { loadGeneratedBook, type GeneratedBook } from '@/lib/api/books'
import { getOfflineBook } from '@/lib/offline/db'
import type { LearningCourseId } from '@/lib/mock/learning'

type State =
  | { kind: 'loading' }
  | { kind: 'ready'; book: GeneratedBook }
  | { kind: 'missing' }
  | { kind: 'error'; message: string }

/**
 * Reader for courses that aren't seeded in the frontend: learner books and
 * course-builder books. Loads in the browser so the reader's own session
 * (bearer token / cookie) unlocks private books.
 */
export function GeneratedBookReader({
  courseId,
  initialChapter,
  initialPage,
}: {
  courseId: string
  initialChapter?: number
  initialPage?: number
}) {
  const [state, setState] = useState<State>({ kind: 'loading' })

  useEffect(() => {
    let cancelled = false
    loadGeneratedBook(courseId)
      .then((book) => {
        if (cancelled) return
        setState(book && book.pages.length > 0 ? { kind: 'ready', book } : { kind: 'missing' })
      })
      .catch(async (e: Error) => {
        // Offline (or the API is down): open the copy saved by "Download for offline".
        const saved = await getOfflineBook(courseId).catch(() => undefined)
        if (cancelled) return
        if (saved && saved.pages.length > 0) {
          setState({
            kind: 'ready',
            book: {
              id: saved.courseId,
              title: saved.title,
              chapters: saved.chapters.map((c) => ({
                number: c.number,
                title: c.title,
                summary: '',
                pages: saved.pages.filter((p) => p.chapterNumber === c.number).length,
              })),
              pages: saved.pages,
              aiGenerated: false,
              buildJobId: null,
            },
          })
          return
        }
        setState({ kind: 'error', message: e.message })
      })
    return () => {
      cancelled = true
    }
  }, [courseId])

  if (state.kind === 'loading') return <BookReaderSkeleton />
  if (state.kind !== 'ready') {
    return (
      <div className="flex min-h-dvh flex-col bg-background text-foreground">
        <BookMissing
          title={state.kind === 'missing' ? 'Book not found' : 'Could not open this book'}
          description={
            state.kind === 'missing'
              ? 'This book does not exist, is still being written, or belongs to someone else. Sign in with the account that created it.'
              : state.message
          }
          primary={{ href: '/create', label: 'Create a book' }}
          secondary={{ href: '/library', label: 'Back to library' }}
        />
      </div>
    )
  }

  const { book } = state
  return (
    <BookClientProvider>
      {book.aiGenerated && (
        <p className="sr-only" role="note">
          This book was generated from AI knowledge, not from your sources. Double-check important facts.
        </p>
      )}
      <BookReader
        // Generated ids aren't in the seeded union; the reader only uses the id as a key.
        course={{ id: book.id as LearningCourseId, title: book.title, chapters: book.chapters }}
        pages={book.pages}
        initialChapter={initialChapter}
        initialPage={initialPage}
      />
    </BookClientProvider>
  )
}
