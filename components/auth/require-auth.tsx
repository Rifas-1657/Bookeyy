'use client'

import { usePathname, useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { isBackendConfigured, refreshSession, useSession, type UserRole } from '@/lib/api/auth'
import { useHydrated } from '@/lib/stores/personalization'

interface RequireAuthProps {
  children: React.ReactNode
  /** Only users with this role may see the children. The server enforces it too. */
  role?: UserRole
}

/**
 * Client-side route guard. Without a configured backend the demo stays open.
 * With one, signed-out visitors go to /login?next=<path> after one silent
 * refresh attempt (the httpOnly refresh cookie may still be valid), and
 * non-admins are sent from admin routes to the dashboard.
 */
export function RequireAuth({ children, role }: RequireAuthProps) {
  const router = useRouter()
  const pathname = usePathname()
  const hydrated = useHydrated()
  const session = useSession()
  const enabled = isBackendConfigured()
  const [refreshFailed, setRefreshFailed] = useState(false)

  useEffect(() => {
    if (!enabled || !hydrated) return
    if (!session) {
      if (!refreshFailed) {
        let cancelled = false
        refreshSession().then((ok) => {
          if (!cancelled && !ok) setRefreshFailed(true)
        })
        return () => {
          cancelled = true
        }
      }
      router.replace(`/login?next=${encodeURIComponent(pathname)}`)
      return
    }
    if (role && session.role !== role) router.replace('/dashboard')
  }, [enabled, hydrated, session, refreshFailed, role, pathname, router])

  if (!enabled) return <>{children}</>
  if (!hydrated || !session || (role && session.role !== role)) {
    return (
      <div role="status" aria-live="polite" className="flex min-h-dvh items-center justify-center text-sm text-muted-foreground">
        Checking your session…
      </div>
    )
  }
  return <>{children}</>
}
