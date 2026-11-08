import type { Metadata } from 'next'
import { CourseCatalog } from '@/components/courses/course-catalog'
import { WorkspacePage } from '@/components/workspace/workspace-page'
import { learningCourses } from '@/lib/mock/learning'

export const metadata: Metadata = {
  title: 'Explore',
  description: 'Browse Bookey courses by topic, difficulty, and length.',
}

export default function CoursesPage() {
  return (
    <WorkspacePage
      title="Explore"
      description="Interactive books that teach one idea per page. Search by topic, filter by difficulty, and pick up where you left off."
    >
      <CourseCatalog courses={learningCourses} />
    </WorkspacePage>
  )
}
