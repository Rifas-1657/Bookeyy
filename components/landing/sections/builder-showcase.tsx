'use client'

import { useInView, useReducedMotion } from 'framer-motion'
import { Check, GripVertical, Loader2, Rocket } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { SourceGlyph, type SourceKind } from '@/components/ui-kit/source-glyphs'
import { cn } from '@/lib/utils'
import { SectionHeading } from '../lux/section-heading'

const STEPS = [
  'Fetching 4 sources',
  'Reading the paper & transcript',
  'Mapping concepts across sources',
  'Outlining chapters & lessons',
  'Writing practicals and tasks',
  'Designing the final project',
]

const SOURCES: { kind: SourceKind; label: string }[] = [
  { kind: 'arxiv', label: 'arXiv 1706.03762' },
  { kind: 'youtube', label: 'Stanford CS25 lecture' },
  { kind: 'github', label: 'karpathy/minGPT' },
  { kind: 'reddit', label: 'r/MachineLearning thread' },
]

const OUTLINE = [
  { title: 'Why sequences are hard', lessons: ['RNNs forget', 'The bottleneck'] },
  { title: 'Attention, by hand', lessons: ['Queries, keys, values', 'Practical: score a sentence'] },
  { title: 'Build minGPT', lessons: ['Reading the repo', 'Practical: train 200 steps'] },
]

export function BuilderShowcase() {
  const ref = useRef<HTMLDivElement>(null)
  const inView = useInView(ref, { amount: 0.35 })
  const reduced = useReducedMotion()
  const [step, setStep] = useState(0)

  useEffect(() => {
    if (reduced) {
      setStep(STEPS.length)
      return
    }
    if (!inView) return
    const id = window.setInterval(() => setStep((s) => (s >= STEPS.length + 2 ? 0 : s + 1)), 1100)
    return () => window.clearInterval(id)
  }, [inView, reduced])

  const done = Math.min(step, STEPS.length)
  const pct = Math.round((done / STEPS.length) * 100)

  return (
    <section aria-labelledby="builder-title" className="bg-lux-bg px-4 py-24 sm:px-8 sm:py-32">
      <div className="mx-auto grid max-w-7xl items-center gap-14 lg:grid-cols-[0.85fr_1.15fr]">
        <div>
          <SectionHeading
            id="builder-title"
            eyebrow="For course authors"
            title={
              <>
                A whole course from <em className="italic text-lux-gold-text">a paper and a repo.</em>
              </>
            }
            lede="Admins paste research papers, GitHub projects, Reddit threads, lectures and a syllabus. Bookey drafts chapters, lessons, practicals, tasks and a capstone project — you edit the outline, then publish."
          />
          <ul className="mt-8 space-y-3 text-sm text-lux-fg/85">
            {['Live build timeline you can watch', 'Drag to reorder chapters and lessons', 'Tanglish or English narration per course'].map((t) => (
              <li key={t} className="flex items-center gap-3">
                <span aria-hidden="true" className="size-1.5 rotate-45 bg-lux-gold" />
                {t}
              </li>
            ))}
          </ul>
        </div>

        <div ref={ref} className="overflow-hidden rounded-[1.75rem] border border-lux-line bg-lux-surface shadow-[0_50px_90px_-60px_var(--lux-shadow)]">
          <div className="flex items-center gap-2 border-b border-lux-line px-5 py-3">
            <span aria-hidden="true" className="flex gap-1.5">
              <span className="size-2.5 rounded-full bg-lux-line" />
              <span className="size-2.5 rounded-full bg-lux-line" />
              <span className="size-2.5 rounded-full bg-lux-line" />
            </span>
            <p className="ml-2 font-mono text-[11px] text-lux-muted">admin / course-builder</p>
            <span className="ml-auto font-mono text-[11px] text-lux-gold-text">{pct}%</span>
          </div>
          <div className="h-0.5 bg-lux-line">
            <div className="h-full bg-lux-gold transition-[width] duration-700" style={{ width: `${pct}%` }} />
          </div>

          <div className="grid gap-px bg-lux-line md:grid-cols-2">
            <div className="space-y-5 bg-lux-surface p-5">
              <ul className="flex flex-wrap gap-2" aria-label="Sources">
                {SOURCES.map((s) => (
                  <li key={s.label} className="inline-flex items-center gap-1.5 rounded-full border border-lux-line px-2.5 py-1 text-[11px] text-lux-fg">
                    <SourceGlyph kind={s.kind} className="size-3.5 text-lux-gold-text" />
                    {s.label}
                  </li>
                ))}
              </ul>
              <ol className="space-y-2.5" aria-label="Build steps">
                {STEPS.map((label, i) => {
                  const state = i < done ? 'done' : i === done ? 'active' : 'pending'
                  return (
                    <li key={label} className={cn('flex items-center gap-3 text-[13px]', state === 'pending' ? 'text-lux-muted' : 'text-lux-fg')}>
                      <span
                        className={cn(
                          'grid size-5 shrink-0 place-items-center rounded-full border',
                          state === 'done' ? 'border-lux-gold bg-lux-gold text-lux-surface' : 'border-lux-line',
                        )}
                      >
                        {state === 'done' && <Check aria-hidden="true" className="size-3" />}
                        {state === 'active' && <Loader2 aria-hidden="true" className="size-3 text-lux-gold-text motion-safe:animate-spin" />}
                      </span>
                      {label}
                    </li>
                  )
                })}
              </ol>
            </div>
            <div className="bg-lux-surface p-5">
              <p className="font-mono text-[10px] tracking-[0.28em] text-lux-gold-text uppercase">Outline draft</p>
              <ol className="mt-4 space-y-3">
                {OUTLINE.map((ch, i) => (
                  <li
                    key={ch.title}
                    className={cn('rounded-xl border border-lux-line p-3 transition-opacity duration-500', done > 2 + i ? 'opacity-100' : 'opacity-25')}
                  >
                    <p className="flex items-center gap-2 text-[13px] font-medium text-lux-fg">
                      <GripVertical aria-hidden="true" className="size-3.5 text-lux-muted" />
                      <span className="font-mono text-[10px] text-lux-gold-text">{String(i + 1).padStart(2, '0')}</span>
                      {ch.title}
                    </p>
                    <ul className="mt-1.5 space-y-1 pl-9 text-[12px] text-lux-muted">
                      {ch.lessons.map((l) => (
                        <li key={l}>{l}</li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ol>
              <div
                className={cn(
                  'mt-4 inline-flex items-center gap-2 rounded-full bg-primary px-4 py-2 text-[13px] font-medium text-primary-foreground transition-opacity',
                  done >= STEPS.length ? 'opacity-100' : 'opacity-40',
                )}
                aria-hidden="true"
              >
                <Rocket className="size-3.5" /> Publish course
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
