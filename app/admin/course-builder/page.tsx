import type { Metadata } from 'next'
import { CourseBuilder } from '@/components/admin/course-builder/course-builder'

export const metadata: Metadata = { title: 'Course builder' }

export default function AdminCourseBuilderPage() {
  return <CourseBuilder />
}
