'use client'

import { useGSAP } from '@gsap/react'
import { motion, useReducedMotion } from 'framer-motion'
import gsap from 'gsap'
import { ScrollTrigger } from 'gsap/ScrollTrigger'
import { ArrowRight, AudioLines } from 'lucide-react'
import Link from 'next/link'
import { useRef } from 'react'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { GrainOverlay } from '@/components/ui-kit/grain-overlay'
import { MagneticButton } from '@/components/ui-kit/magnetic-button'
import { easeOutExpo } from '@/lib/motion'
import { ROUTES } from '../data'
import { Eyebrow } from '../lux/section-heading'
import { HeroVisual } from './hero-visual'
import type { SceneDrivers } from './open-book-scene'

gsap.registerPlugin(ScrollTrigger, useGSAP)

const PROOF = ['Tanglish + English tutor', 'Diagrams drawn live', 'Code that runs', 'Yours offline']

export function Hero() {
  const section = useRef<HTMLElement>(null)
  const drivers = useRef<SceneDrivers>({ scroll: 0, px: 0, py: 0 })
  const reduced = useReducedMotion()

  useGSAP(
    () => {
      if (reduced) return
      const tl = gsap.timeline({
        defaults: { ease: 'none' },
        scrollTrigger: {
          trigger: section.current,
          start: 'top top',
          end: 'bottom bottom',
          scrub: 0.6,
          onUpdate: (self) => {
            drivers.current.scroll = self.progress
          },
        },
      })
      tl.to('[data-hero-copy]', { yPercent: -18, opacity: 0, duration: 0.45 }, 0.05)
        .fromTo('[data-hero-caption]', { opacity: 0, y: 40 }, { opacity: 1, y: 0, duration: 0.3 }, 0.4)
        .to('[data-hero-caption]', { opacity: 0, y: -30, duration: 0.2 }, 0.85)
        .fromTo('[data-hero-fallback-tilt]', { rotateX: 0, scale: 1 }, { rotateX: 16, scale: 1.14, duration: 1 }, 0)
    },
    { scope: section, dependencies: [reduced] },
  )

  return (
    <section
      ref={section}
      aria-labelledby="hero-title"
      className="relative isolate h-[200svh] bg-lux-bg motion-reduce:h-auto"
    >
      <div className="sticky top-0 flex h-svh min-h-[640px] flex-col overflow-hidden motion-reduce:relative motion-reduce:h-auto motion-reduce:min-h-svh">
        <div aria-hidden="true" className="lux-vignette absolute inset-0 -z-10" />
        <GrainOverlay opacity={0.09} className="-z-10" />

        {/* 3D book: below copy on phones, filling the right side on desktop */}
        <div className="absolute inset-x-0 bottom-0 h-[54%] [perspective:1400px] lg:inset-y-0 lg:right-[-6%] lg:left-[44%] lg:h-full">
          <HeroVisual className="size-full" drivers={drivers} />
        </div>

        <div className="relative z-10 mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 pt-28 sm:px-8 sm:pt-32 lg:justify-center lg:pt-16">
          <div data-hero-copy className="max-w-[40rem]">
            <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.9, ease: easeOutExpo }}>
              <Eyebrow>The living book</Eyebrow>
            </motion.div>
            <h1
              id="hero-title"
              className="mt-6 font-serif text-[clamp(2.6rem,6.2vw,5.6rem)] leading-[0.96] font-light tracking-[-0.035em] text-lux-fg"
            >
              {['Every source,', 'bound into a book', 'that teaches.'].map((line, i) => (
                <span key={line} className="block overflow-hidden pb-[0.08em]">
                  <motion.span
                    className="inline-block"
                    initial={{ y: '108%' }}
                    animate={{ y: '0%' }}
                    transition={{ duration: 1.15, ease: easeOutExpo, delay: 0.1 + i * 0.12 }}
                  >
                    {i === 2 ? (
                      <>
                        that <em className="text-gold-foil font-normal italic motion-safe:animate-[lux-shimmer_6s_linear_infinite]">teaches.</em>
                      </>
                    ) : (
                      line
                    )}
                  </motion.span>
                </span>
              ))}
            </h1>
            <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.9, ease: easeOutExpo, delay: 0.55 }}>
              <p className="mt-6 max-w-lg text-[15px] leading-relaxed text-lux-muted sm:text-lg">
                YouTube lectures, PDFs, research papers, GitHub repos and Reddit threads become interactive 3D books. A
                warm voice tutor explains in Tanglish or English, draws as she speaks, and runs the code with you.
              </p>
              <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
                <MagneticButton size="lg" nativeButton={false} render={<Link href={ROUTES.signup} />}>
                  Open your first book
                  <ArrowRight aria-hidden="true" />
                </MagneticButton>
                <BookeyButton size="lg" variant="ghost" nativeButton={false} render={<Link href="#tutor-demo" />} className="text-lux-fg hover:bg-lux-fg/6">
                  <AudioLines aria-hidden="true" />
                  Hear her teach
                </BookeyButton>
              </div>
              <ul className="mt-8 hidden flex-wrap gap-x-5 gap-y-2 text-xs tracking-wide text-lux-muted sm:flex">
                {PROOF.map((p) => (
                  <li key={p} className="flex items-center gap-2">
                    <span aria-hidden="true" className="size-1 rotate-45 bg-lux-gold" />
                    {p}
                  </li>
                ))}
              </ul>
            </motion.div>
          </div>
        </div>

        <p
          data-hero-caption
          className="pointer-events-none absolute inset-x-0 top-[18%] z-10 px-6 text-center font-serif text-[clamp(1.6rem,4vw,3rem)] leading-tight font-light text-lux-fg opacity-0 motion-reduce:hidden lg:top-[12%]"
        >
          Each page turns <em className="text-gold-foil italic">as she explains it.</em>
        </p>

        <div aria-hidden="true" className="absolute bottom-6 left-1/2 hidden -translate-x-1/2 flex-col items-center gap-2 text-[10px] tracking-[0.3em] text-lux-muted uppercase motion-reduce:hidden lg:flex">
          Scroll to turn the page
          <span className="lux-hairline h-10 w-px rotate-90" />
        </div>
      </div>
    </section>
  )
}
