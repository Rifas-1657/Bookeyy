import type { Metadata } from 'next'
import { Faq } from '@/components/landing/faq'
import { Hero } from '@/components/landing/hero/hero'
import { LandingFooter } from '@/components/landing/landing-footer'
import { LandingHeader } from '@/components/landing/landing-header'
import { LuxSurface } from '@/components/landing/lux/lux-surface'
import { BuilderShowcase } from '@/components/landing/sections/builder-showcase'
import { CourseShowcase } from '@/components/landing/sections/course-showcase'
import { Journey } from '@/components/landing/sections/journey'
import { LuxCta } from '@/components/landing/sections/lux-cta'
import { SourcesMorph } from '@/components/landing/sections/sources-morph'
import { Testimonials } from '@/components/landing/sections/testimonials'
import { TutorDemo } from '@/components/landing/sections/tutor-demo'

export const metadata: Metadata = {
  title: 'Bookey — Every source, bound into a book that teaches',
  description:
    'Turn YouTube lectures, PDFs, research papers, GitHub projects and Reddit threads into interactive 3D books with a Tanglish + English voice tutor, live diagrams, runnable practicals, and an offline recap.',
}

export default function LandingPage() {
  return (
    <LuxSurface>
      <LandingHeader />
      <main id="main" className="overflow-x-clip">
        <Hero />
        <SourcesMorph />
        <TutorDemo />
        <Journey />
        <CourseShowcase />
        <BuilderShowcase />
        <Testimonials />
        <Faq />
        <LuxCta />
      </main>
      <LandingFooter />
    </LuxSurface>
  )
}
