import type { Metadata } from 'next'
import { CtaBand } from '@/components/site/cta-band'
import { PageHero } from '@/components/site/page-hero'

export const metadata: Metadata = {
  title: 'Pricing',
  description: 'Bookey is free to use right now.',
}

export default function PricingPage() {
  return (
    <>
      <PageHero
        eyebrow="Pricing"
        title="Free while we grow."
        lead="Create an account, get your own Python book, and turn your own sources into books. No card needed."
      />
      <CtaBand title={<>Ready for your <span className="text-primary">own book?</span></>} />
    </>
  )
}
