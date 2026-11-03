'use client'

import { motion, useReducedMotion, useScroll, useSpring, useTransform, type MotionValue } from 'framer-motion'
import { ArrowRight } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { SourceGlyph, type SourceKind } from '@/components/ui-kit/source-glyphs'
import { cn } from '@/lib/utils'
import { SectionHeading } from '../lux/section-heading'

interface SourceChip {
  kind: SourceKind
  label: string
  meta: string
  /** Start position in % of the stage (desktop / mobile). */
  at: { x: number; y: number }
  atMobile: { x: number; y: number }
}

const SOURCES: SourceChip[] = [
  { kind: 'youtube', label: '3h lecture on transformers', meta: 'YouTube · 3:04:12', at: { x: 6, y: 12 }, atMobile: { x: 4, y: 4 } },
  { kind: 'arxiv', label: 'Attention Is All You Need', meta: 'arXiv · 15 pages', at: { x: 30, y: 4 }, atMobile: { x: 42, y: 10 } },
  { kind: 'github', label: 'minGPT', meta: 'GitHub · 42 files', at: { x: 2, y: 46 }, atMobile: { x: 6, y: 22 } },
  { kind: 'reddit', label: '“ELI5: why does attention work?”', meta: 'Reddit · 318 replies', at: { x: 26, y: 62 }, atMobile: { x: 40, y: 30 } },
  { kind: 'pdf_url', label: 'Lecture notes, week 6', meta: 'PDF · 22 pages', at: { x: 8, y: 82 }, atMobile: { x: 2, y: 40 } },
  { kind: 'web', label: 'The Illustrated Transformer', meta: 'Article · 18 min', at: { x: 34, y: 34 }, atMobile: { x: 42, y: 46 } },
]

const CHAPTERS = ['Why sequences are hard', 'Attention, by hand', 'Multi-head intuition', 'Build minGPT', 'Project: tiny poet']

const BOOK = { x: 74, y: 50 }
const BOOK_MOBILE = { x: 50, y: 72 }

function useStageSize(ref: React.RefObject<HTMLDivElement | null>) {
  const [size, setSize] = useState({ w: 0, h: 0 })
  useEffect(() => {
    const node = ref.current
    if (!node) return
    const ro = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }))
    ro.observe(node)
    return () => ro.disconnect()
  }, [ref])
  return size
}

function FlyingChip({ chip, index, progress, size, mobile }: { chip: SourceChip; index: number; progress: MotionValue<number>; size: { w: number; h: number }; mobile: boolean }) {
  const start = mobile ? chip.atMobile : chip.at
  const target = mobile ? BOOK_MOBILE : BOOK
  const dx = ((target.x - start.x) / 100) * size.w - 70
  const dy = ((target.y - start.y) / 100) * size.h - 20
  const s0 = 0.12 + index * 0.07
  const s1 = s0 + 0.32
  const x = useTransform(progress, [s0, s1], [0, dx])
  // Arc: rise before diving into the spine.
  const y = useTransform(progress, [s0, (s0 + s1) / 2, s1], [0, dy * 0.35 - 60, dy])
  const scale = useTransform(progress, [s0, s1], [1, 0.25])
  const opacity = useTransform(progress, [s0, s1 - 0.04, s1], [1, 0.9, 0])
  const rotate = useTransform(progress, [s0, s1], [0, index % 2 ? 18 : -18])

  return (
    <motion.li
      style={{ left: `${start.x}%`, top: `${start.y}%`, x, y, scale, opacity, rotate }}
      className="absolute origin-center"
    >
      <span className="flex max-w-[11.5rem] items-center sm:max-w-[15rem] gap-3 rounded-2xl border border-lux-line bg-lux-surface/90 py-2.5 pr-4 pl-2.5 shadow-[0_18px_40px_-24px_var(--lux-shadow)] backdrop-blur-md">
        <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-lux-gold-soft text-lux-gold-text">
          <SourceGlyph kind={chip.kind} />
        </span>
        <span className="min-w-0">
          <span className="block truncate text-[13px] font-medium text-lux-fg">{chip.label}</span>
          <span className="block text-[11px] text-lux-muted">{chip.meta}</span>
        </span>
      </span>
    </motion.li>
  )
}

function BoundBook({ progress, className }: { progress?: MotionValue<number>; className?: string }) {
  const fallback = useSpring(1)
  const p = progress ?? fallback
  const glow = useTransform(p, [0.1, 0.75], [0.15, 1])
  const lift = useTransform(p, [0.1, 0.75], [10, 0])
  return (
    <div className={cn('[perspective:1200px]', className)}>
      <motion.div
        style={{ y: lift }}
        className="relative aspect-[3/4] w-[min(40vw,240px)] [transform:rotateY(-16deg)_rotateX(6deg)] [transform-style:preserve-3d]"
      >
        <motion.div aria-hidden="true" style={{ opacity: glow }} className="absolute -inset-[30%] rounded-full bg-[radial-gradient(circle,var(--lux-glow),transparent_65%)] blur-xl" />
        <div aria-hidden="true" className="absolute inset-y-[2%] right-[-4%] w-[6%] rounded-r-sm bg-[repeating-linear-gradient(0deg,var(--lux-gold)_0_1px,var(--lux-gold-bright)_1px_3px)] [transform:translateZ(-6px)]" />
        <div className="absolute inset-0 overflow-hidden rounded-[6px_14px_14px_6px] bg-lux-oxblood p-[12%] text-lux-stage-fg shadow-[0_40px_70px_-30px_var(--lux-shadow),inset_10px_0_18px_-8px_rgb(0_0_0/0.5)]">
          <div aria-hidden="true" className="absolute inset-[6%] rounded-[4px_10px_10px_4px] border border-lux-gold/60" />
          <p className="relative font-mono text-[9px] tracking-[0.3em] text-lux-gold-bright uppercase">A Bookey edition</p>
          <p className="relative mt-3 font-serif text-[clamp(1.1rem,3.2vw,1.6rem)] leading-[1.05] font-light">
            Transformers, <em className="italic text-lux-gold-bright">explained</em>
          </p>
          <ol className="relative mt-4 space-y-1.5">
            {CHAPTERS.map((c, i) => (
              <ChapterLine key={c} index={i} label={c} progress={p} />
            ))}
          </ol>
        </div>
      </motion.div>
    </div>
  )
}

function ChapterLine({ index, label, progress }: { index: number; label: string; progress: MotionValue<number> }) {
  const at = 0.4 + index * 0.08
  const opacity = useTransform(progress, [at, at + 0.06], [0, 1])
  const x = useTransform(progress, [at, at + 0.06], [-6, 0])
  return (
    <motion.li style={{ opacity, x }} className="flex gap-2 text-[10px] leading-tight text-lux-stage-fg/80 sm:text-[11px]">
      <span className="font-mono text-lux-gold-bright">{String(index + 1).padStart(2, '0')}</span>
      <span className="truncate">{label}</span>
    </motion.li>
  )
}

export function SourcesMorph() {
  const reduced = useReducedMotion()
  const section = useRef<HTMLElement>(null)
  const stage = useRef<HTMLDivElement>(null)
  const size = useStageSize(stage)
  const mobile = size.w > 0 && size.w < 720
  const { scrollYProgress } = useScroll({ target: section, offset: ['start start', 'end end'] })
  const progress = useSpring(scrollYProgress, { stiffness: 120, damping: 30, mass: 0.3 })

  if (reduced) {
    return (
      <section aria-labelledby="sources-title" className="bg-lux-bg-2 px-4 py-24 sm:px-8">
        <div className="mx-auto max-w-7xl">
          <SectionHeading id="sources-title" eyebrow="Bring anything" title={<>Six sources in. <em className="italic text-lux-gold-text">One book out.</em></>} />
          <div className="mt-12 grid items-center gap-10 lg:grid-cols-[1fr_auto_auto]">
            <ul className="grid gap-3 sm:grid-cols-2">
              {SOURCES.map((s) => (
                <li key={s.kind} className="flex items-center gap-3 rounded-2xl border border-lux-line bg-lux-surface p-3">
                  <span className="grid size-9 place-items-center rounded-xl bg-lux-gold-soft text-lux-gold-text">
                    <SourceGlyph kind={s.kind} />
                  </span>
                  <span className="text-sm text-lux-fg">{s.label}</span>
                </li>
              ))}
            </ul>
            <ArrowRight aria-hidden="true" className="mx-auto size-6 text-lux-gold max-lg:rotate-90" />
            <BoundBook className="mx-auto" />
          </div>
        </div>
      </section>
    )
  }

  return (
    <section ref={section} aria-labelledby="sources-title" className="relative h-[240svh] bg-lux-bg-2">
      <div className="sticky top-0 flex h-svh min-h-[620px] flex-col overflow-hidden px-4 pt-20 pb-8 sm:px-8 sm:pt-24">
        <div className="mx-auto w-full max-w-7xl">
          <SectionHeading
            id="sources-title"
            eyebrow="Bring anything"
            title={
              <>
                Six sources in. <em className="italic text-lux-gold-text">One book out.</em>
              </>
            }
            lede="Paste a link or drop a file. Bookey reads the lecture, the paper, the repo and the argument in the comments, then binds them into chapters with a single thread."
          />
        </div>
        <div ref={stage} className="relative mx-auto mt-6 w-full max-w-7xl flex-1">
          <ul aria-label="Example sources" className="absolute inset-0">
            {size.w > 0 && SOURCES.map((chip, i) => <FlyingChip key={chip.kind} chip={chip} index={i} progress={progress} size={size} mobile={mobile} />)}
          </ul>
          <div
            className="absolute -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${mobile ? BOOK_MOBILE.x : BOOK.x}%`, top: `${mobile ? BOOK_MOBILE.y : BOOK.y}%` }}
          >
            <BoundBook progress={progress} />
          </div>
        </div>
      </div>
    </section>
  )
}
