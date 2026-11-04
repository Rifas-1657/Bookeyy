import Link from 'next/link'
import { BookeyBadge } from '@/components/ui-kit/bookey-badge'
import { BookeyLogo } from '@/components/ui-kit/bookey-logo'
import { cn } from '@/lib/utils'
import { WorkspaceNavList } from './workspace-nav-list'

export function WorkspaceSidebar({ collapsed }: { collapsed: boolean }) {
  return (
    <aside
      className={cn(
        'sticky top-0 hidden h-dvh shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-[width] duration-300 ease-out motion-reduce:transition-none lg:flex',
        collapsed ? 'w-[4.75rem]' : 'w-64',
      )}
    >
      <div className={cn('flex h-16 shrink-0 items-center', collapsed ? 'justify-center' : 'px-5')}>
        <Link
          href="/dashboard"
          aria-label="Bookey overview"
          className="rounded-xl outline-none focus-visible:ring-4 focus-visible:ring-ring/30"
        >
          <BookeyLogo markOnly={collapsed} />
        </Link>
      </div>

      <nav aria-label="Workspace" className={cn('flex-1 overflow-y-auto py-4', collapsed ? 'px-3' : 'px-3')}>
        <WorkspaceNavList collapsed={collapsed} />
      </nav>

    </aside>
  )
}
