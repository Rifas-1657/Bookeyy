import { RequireAuth } from '@/components/auth/require-auth'
import { ServiceWorkerRegister } from '@/components/reader/service-worker-register'

/** Immersive reader: no site or workspace chrome. Registers the offline service worker. */
export default function ViewerLayout({ children }: { children: React.ReactNode }) {
  return (
    <RequireAuth>
      <ServiceWorkerRegister />
      <div className="min-h-dvh bg-background">{children}</div>
    </RequireAuth>
  )
}
