import type { Metadata } from 'next'
import { NotificationCenter } from '@/components/notifications/notification-center'
import { WorkspacePage } from '@/components/workspace/workspace-page'

export const metadata: Metadata = { title: 'Notifications' }

export default function NotificationsPage() {
  return (
    <WorkspacePage title="Notifications" description="Book updates and review reminders.">
      <NotificationCenter />
    </WorkspacePage>
  )
}
