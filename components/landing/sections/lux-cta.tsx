import { ArrowRight } from 'lucide-react'
import Link from 'next/link'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { GrainOverlay } from '@/components/ui-kit/grain-overlay'
import { MagneticButton } from '@/components/ui-kit/magnetic-button'
import { ROUTES } from '../data'
import { Eyebrow } from '../lux/section-heading'

export function LuxCta() {
  return (
    <section aria-labelledby="cta-title" className="relative isolate overflow-hidden bg-lux-bg px-4 py-28 sm:px-8 sm:py-40">
      <div aria-hidden="true" className="lux-vignette absolute inset-0 -z-10" />
      <GrainOverlay opacity={0.08} className="-z-10" />
      <div className="mx-auto max-w-4xl text-center">
        <Eyebrow className="justify-center">Your first edition</Eyebrow>
        <h2 id="cta-title" className="mt-6 font-serif text-[clamp(2.4rem,7vw,5.5rem)] leading-[0.98] font-light tracking-[-0.03em] text-lux-fg">
          Paste a link. <br />
          <em className="text-gold-foil italic">Open a book.</em>
        </h2>
        <p className="mx-auto mt-6 max-w-lg text-base leading-relaxed text-lux-muted sm:text-lg">
          Start with a lecture you keep meaning to finish. She will be waiting on page one.
        </p>
        <div className="mt-10 flex flex-col justify-center gap-3 sm:flex-row">
          <MagneticButton size="lg" nativeButton={false} render={<Link href={ROUTES.signup} />}>
            Start learning free
            <ArrowRight aria-hidden="true" />
          </MagneticButton>
          <BookeyButton size="lg" variant="outline" nativeButton={false} render={<Link href={ROUTES.demoCourse} />} className="border-lux-line bg-transparent text-lux-fg hover:bg-lux-fg/5">
            See the Python book
          </BookeyButton>
        </div>
      </div>
    </section>
  )
}
