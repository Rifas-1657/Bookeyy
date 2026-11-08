import { redirect } from 'next/navigation'

/** Profile now lives in Settings; old links keep working. */
export default async function ProfilePage({ searchParams }: { searchParams: Promise<{ course?: string }> }) {
  const { course } = await searchParams
  redirect(course ? `/settings?course=${encodeURIComponent(course)}` : '/settings')
}
