import type { Metadata } from 'next'
import { AdminShell } from '@/components/admin/admin-shell'
import { RequireAuth } from '@/components/auth/require-auth'

export const metadata: Metadata = { title: { default: 'Admin', template: '%s · Bookey Admin' }, robots: { index: false } }

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth role="admin">
      <AdminShell>{children}</AdminShell>
    </RequireAuth>
  )
}
