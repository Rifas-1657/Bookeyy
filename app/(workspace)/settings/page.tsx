import type { Metadata } from 'next'
import { ProfileForm } from '@/components/profile/profile-form'
import { SettingsForm } from '@/components/settings/settings-form'
import { WorkspacePage } from '@/components/workspace/workspace-page'
import { getLearningCourse } from '@/lib/mock/learning'

export const metadata: Metadata = { title: 'Settings' }

export default async function SettingsPage({ searchParams }: { searchParams: Promise<{ course?: string }> }) {
  const { course } = await searchParams
  const initialCourse = course ? getLearningCourse(course)?.id : undefined

  return (
    <WorkspacePage title="Settings" description="Your account, how you like to learn, and how Bookey looks and sounds.">
      <ProfileForm initialCourse={initialCourse} />
      <SettingsForm />
    </WorkspacePage>
  )
}
