'use client'

import { UserMinus, Users } from 'lucide-react'
import { useState } from 'react'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { EmptyState } from '@/components/ui-kit/empty-state'
import { notify } from '@/components/ui-kit/toast'
import { apiJson } from '@/lib/api/auth'
import type { DemoMember } from '@/lib/admin/types'
import { formatDate } from '@/lib/admin/utils'
import { useAdminDemo } from '@/lib/stores/admin-demo'
import { AdminPageHeader, ConfirmDialog, TableScroller, tdClass, thClass } from '../admin-ui'

export function AdminMembers() {
  const members = useAdminDemo((s) => s.members)
  const loadLive = useAdminDemo((s) => s.loadLive)
  const [pendingRemove, setPendingRemove] = useState<DemoMember | null>(null)

  async function deactivate(member: DemoMember) {
    try {
      await apiJson(`/api/admin/members/${encodeURIComponent(member.id)}`, { method: 'DELETE' })
      await loadLive()
      notify.success(`${member.name} can no longer log in`)
    } catch (error) {
      notify.error('Could not remove this member', error instanceof Error ? error.message : undefined)
    }
  }

  return (
    <div className="space-y-6">
      <AdminPageHeader title="Members" description="Everyone with a Bookey account. Learners sign up on their own." />

      {members.length === 0 ? (
        <EmptyState icon={<Users aria-hidden="true" />} title="No members yet" description="Learners appear here as soon as they sign up." />
      ) : (
        <TableScroller label="Members">
          <table className="w-full min-w-[36rem] text-sm">
            <caption className="sr-only">Members and the date they joined</caption>
            <thead className="border-b border-border bg-muted/50">
              <tr>
                <th scope="col" className={thClass}>Member</th>
                <th scope="col" className={thClass}>Joined</th>
                <th scope="col" className={`${thClass} text-right`}>
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {members.map((member) => (
                <tr key={member.id} className="hover:bg-muted/40">
                  <td className={tdClass}>
                    <p className="font-medium">{member.name}</p>
                    <p className="text-xs text-muted-foreground">{member.email}</p>
                  </td>
                  <td className={tdClass}>{member.lastActiveAt ? formatDate(member.lastActiveAt) : ''}</td>
                  <td className={`${tdClass} text-right`}>
                    <BookeyButton type="button" variant="ghost" size="sm" onClick={() => setPendingRemove(member)}>
                      <UserMinus aria-hidden="true" />
                      Remove<span className="sr-only"> {member.name}</span>
                    </BookeyButton>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroller>
      )}

      <ConfirmDialog
        open={pendingRemove !== null}
        onOpenChange={(open) => !open && setPendingRemove(null)}
        title={`Remove ${pendingRemove?.name ?? 'this member'}?`}
        description="They will no longer be able to log in. Their books and progress stay saved."
        confirmLabel="Remove member"
        onConfirm={() => {
          if (pendingRemove) void deactivate(pendingRemove)
          setPendingRemove(null)
        }}
      />
    </div>
  )
}
