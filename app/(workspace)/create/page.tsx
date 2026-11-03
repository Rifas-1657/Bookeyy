import type { Metadata } from 'next'
import { CreateBook } from '@/components/books/create-book'
import { WorkspacePage } from '@/components/workspace/workspace-page'

export const metadata: Metadata = { title: 'Create a book' }

export default function CreateBookPage() {
  return (
    <WorkspacePage
      title="Create a book"
      description="Turn a PDF, slide deck, document, video or article — or just a topic — into a narrated, interactive book with its own tutor."
    >
      <CreateBook />
    </WorkspacePage>
  )
}
