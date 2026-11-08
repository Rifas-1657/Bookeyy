import { PenLine } from 'lucide-react'
import type { Metadata } from 'next'
import Link from 'next/link'
import { Suspense } from 'react'
import { LibraryExperience } from '@/components/library/library-experience'
import { LibrarySkeleton } from '@/components/library/library-skeleton'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { WorkspacePage } from '@/components/workspace/workspace-page'

export const metadata: Metadata = { title: 'My books' }

export default function LibraryPage() {
  return (
    <WorkspacePage
      title="My books"
      description="Your books, saved notes, bookmarks, and reading history."
      aside={
        <BookeyButton className="self-start sm:self-auto" nativeButton={false} render={<Link href="/create" />}>
          <PenLine aria-hidden="true" />
          Create a book
        </BookeyButton>
      }
    >
      <Suspense fallback={<LibrarySkeleton />}>
        <LibraryExperience />
      </Suspense>
    </WorkspacePage>
  )
}
