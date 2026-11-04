import type { Metadata } from 'next'
import { Suspense } from 'react'
import { Skeleton } from '@/components/ui/skeleton'
import { SearchExperience } from '@/components/search/search-experience'
import { SearchResultsSkeleton } from '@/components/search/search-states'
import { WorkspacePage } from '@/components/workspace/workspace-page'

export const metadata: Metadata = { title: 'Search' }

export default function SearchPage() {
  return (
    <WorkspacePage title="Search" description="Search every course, chapter, book page, and your notes.">
      <div className="max-w-3xl">
        <Suspense
          fallback={
            <div className="flex flex-col gap-6">
              <Skeleton className="h-14 w-full rounded-2xl bg-muted" />
              <SearchResultsSkeleton />
            </div>
          }
        >
          <SearchExperience />
        </Suspense>
      </div>
    </WorkspacePage>
  )
}
