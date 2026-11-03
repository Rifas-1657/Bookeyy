import { LandingFooter } from '@/components/landing/landing-footer'
import { LandingHeader } from '@/components/landing/landing-header'
import { LuxSurface } from '@/components/landing/lux/lux-surface'

export default function SiteLayout({ children }: { children: React.ReactNode }) {
  return (
    <LuxSurface>
      <LandingHeader />
      <main id="main" className="overflow-x-clip">
        {children}
      </main>
      <LandingFooter />
    </LuxSurface>
  )
}
