'use client'

import { ArrowRight, BookOpen, FileStack, Layers, SlidersHorizontal, UserCheck, Users } from 'lucide-react'
import Link from 'next/link'
import { useAdminDemo } from '@/lib/stores/admin-demo'
import { AdminPageHeader, AdminPanel } from '../admin-ui'

const QUICK_LINKS = [
  { href: '/admin/course-builder', label: 'Build a course', description: 'Turn papers, repos and links into a book.', icon: Layers },
  { href: '/admin/knowledge-base', label: 'Manage sources', description: 'Upload documents to ground your courses.', icon: FileStack },
  { href: '/admin/tutor-settings', label: 'Tutor settings', description: 'Narration and explanation defaults.', icon: SlidersHorizontal },
]

export function AdminOverview() {
  const overview = useAdminDemo((s) => s.overview)
  const published = useAdminDemo((s) => s.courses.filter((c) => c.status === 'published').length)

  const stats = [
    { label: 'Learners', value: overview?.learners ?? 0, note: `${overview?.joinedThisWeek ?? 0} joined this week`, icon: Users },
    { label: 'Published courses', value: published, note: 'Visible to every learner', icon: Layers },
    { label: 'Books created', value: overview?.books ?? 0, note: 'Personal and learner-made books', icon: BookOpen },
    { label: 'Pages read', value: overview?.pagesRead ?? 0, note: 'Across all learners', icon: UserCheck },
  ]

  return (
    <div className="space-y-6">
      <AdminPageHeader title="Overview" description="How Bookey is being used right now." />

      <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {stats.map(({ label, value, note, icon: Icon }) => (
          <li key={label} className="rounded-2xl border border-border bg-card p-5">
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-medium text-muted-foreground">{label}</p>
              <span aria-hidden="true" className="grid size-9 place-items-center rounded-xl bg-secondary text-secondary-foreground">
                <Icon className="size-4" />
              </span>
            </div>
            <p className="mt-3 font-display text-3xl font-bold tabular-nums">{value.toLocaleString('en-US')}</p>
            <p className="mt-1 text-xs text-muted-foreground">{note}</p>
          </li>
        ))}
      </ul>

      <AdminPanel title="Quick actions">
        <ul className="grid gap-3 md:grid-cols-3">
          {QUICK_LINKS.map(({ href, label, description, icon: Icon }) => (
            <li key={href}>
              <Link
                href={href}
                className="flex h-full items-start gap-3 rounded-2xl border border-border p-4 outline-none hover:bg-muted/50 focus-visible:ring-4 focus-visible:ring-ring/30"
              >
                <Icon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-primary" />
                <span className="min-w-0 flex-1">
                  <span className="block font-medium">{label}</span>
                  <span className="block text-sm text-muted-foreground">{description}</span>
                </span>
                <ArrowRight aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              </Link>
            </li>
          ))}
        </ul>
      </AdminPanel>
    </div>
  )
}
