'use client'

import { useReducedMotion } from 'framer-motion'
import { MessageCircleQuestion, Pause, Play, Terminal } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { SectionHeading } from '../lux/section-heading'

type Lang = 'tanglish' | 'english'

const SCRIPTS: Record<Lang, { text: string; done: string; answer: string }> = {
  tanglish: {
    text: 'Seri, ippo oru loop paakalaam. Oru list la irukura ovvoru item-aiyum naama onnu onna edupom. Adhu dhaan for loop — simple-a irukku la? Ippo code-a run panni paakalaam.',
    done: 'Super! Neenga pannitinga.',
    answer: 'Nalla question! range(3) na 0, 1, 2 — moonu thadava loop odum. Try pannunga, naan irukken.',
  },
  english: {
    text: 'Okay, let’s look at a loop. We take every item in a list, one at a time. That is a for loop — simple, right? Now let’s run the code and see.',
    done: 'Nice — you did it.',
    answer: 'Great question! range(3) gives 0, 1, 2 — so the loop runs three times. Try it, I’m right here.',
  },
}

const HOLD = 3.2
const BARS = 44

function timeline(text: string) {
  let t = 0.4
  return text.split(' ').map((word) => {
    const start = t
    t += 0.26 + Math.min(word.length, 10) * 0.028 + (/[.,?—]$/.test(word) || word === '—' ? 0.32 : 0)
    return { word, start, end: t - 0.04 }
  })
}

/** Diagram steps keyed to a fraction of the spoken duration. */
const STEPS = [
  { id: 'list', at: 0.12 },
  { id: 'arrow1', at: 0.3 },
  { id: 'item', at: 0.38 },
  { id: 'arrow2', at: 0.5 },
  { id: 'print', at: 0.56 },
  { id: 'loop', at: 0.66 },
  { id: 'run', at: 0.86 },
] as const

function useInView(ref: React.RefObject<HTMLElement | null>) {
  const [inView, setInView] = useState(false)
  useEffect(() => {
    const node = ref.current
    if (!node) return
    const io = new IntersectionObserver(([e]) => setInView(e.isIntersecting), { threshold: 0.25 })
    io.observe(node)
    return () => io.disconnect()
  }, [ref])
  return inView
}

export function TutorDemo() {
  const reduced = useReducedMotion()
  const section = useRef<HTMLElement>(null)
  const inView = useInView(section)
  const [lang, setLang] = useState<Lang>('tanglish')
  const [playing, setPlaying] = useState(true)
  const [asked, setAsked] = useState(false)
  const words = useMemo(() => timeline(SCRIPTS[lang].text), [lang])
  const speechEnd = words[words.length - 1].end
  const total = speechEnd + HOLD
  const [t, setT] = useState(0)
  const bars = useRef<(HTMLSpanElement | null)[]>([])
  const tRef = useRef(0)

  const running = playing && inView && !reduced && !asked

  useEffect(() => {
    tRef.current = 0
    setT(reduced ? total : 0)
  }, [lang, reduced, total])

  useEffect(() => {
    if (!running) return
    let raf = 0
    let last = performance.now()
    let frame = 0
    const loop = (now: number) => {
      const dt = Math.min(0.05, (now - last) / 1000)
      last = now
      tRef.current = (tRef.current + dt) % total
      const time = tRef.current
      const speaking = words.some((w) => time >= w.start && time <= w.end)
      for (let i = 0; i < BARS; i++) {
        const el = bars.current[i]
        if (!el) continue
        const n = Math.abs(Math.sin(time * 9 + i * 0.7) * Math.cos(time * 4.3 + i * 0.31))
        const env = Math.sin((Math.PI * i) / (BARS - 1))
        el.style.transform = `scaleY(${speaking ? 0.12 + n * env * 0.88 : 0.08 + env * 0.04})`
      }
      if (frame++ % 2 === 0) setT(time)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [running, total, words])

  const current = words.findIndex((w) => t >= w.start && t <= w.end)
  const spoken = words.filter((w) => t >= w.start).length
  const frac = Math.min(1, t / speechEnd)
  const draw = (id: (typeof STEPS)[number]['id']) => {
    const step = STEPS.find((s) => s.id === id)!
    return Math.max(0, Math.min(1, (frac - step.at) / 0.1))
  }

  return (
    <section ref={section} id="tutor-demo" aria-labelledby="tutor-title" className="scroll-mt-20 bg-lux-bg px-4 py-24 sm:px-8 sm:py-32">
      <div className="mx-auto max-w-7xl">
        <SectionHeading
          id="tutor-title"
          eyebrow="Hear her teach"
          title={
            <>
              She speaks. The page <em className="italic text-lux-gold-text">writes itself.</em>
            </>
          }
          lede="Words appear as she says them. The diagram draws in step with her voice. When she reaches the code, it opens and runs. Stop her anytime and ask."
        />

        <div className="mt-12 overflow-hidden rounded-[2rem] border border-lux-line bg-lux-surface shadow-[0_40px_80px_-50px_var(--lux-shadow)]">
          {/* Controls */}
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-lux-line px-4 py-3 sm:px-6">
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={() => {
                  setAsked(false)
                  setPlaying((p) => (asked ? true : !p))
                }}
                hidden={reduced === true}
                className="grid size-10 place-items-center rounded-full bg-primary text-primary-foreground outline-none focus-visible:ring-4 focus-visible:ring-ring/30"
              >
                {running ? <Pause aria-hidden="true" className="size-4" /> : <Play aria-hidden="true" className="size-4" />}
                <span className="sr-only">{running ? 'Pause demo' : 'Play demo'}</span>
              </button>
              <div>
                <p className="text-sm font-medium text-lux-fg">Meera, your tutor</p>
                <p className="text-xs text-lux-muted">Simulated demo · Chapter 3, page 2</p>
              </div>
            </div>
            <div role="radiogroup" aria-label="Narration language" className="flex rounded-full border border-lux-line p-1 text-xs">
              {(['tanglish', 'english'] as const).map((l) => (
                <button
                  key={l}
                  type="button"
                  role="radio"
                  aria-checked={lang === l}
                  onClick={() => {
                    setLang(l)
                    setAsked(false)
                  }}
                  className={cn(
                    'rounded-full px-3.5 py-1.5 capitalize outline-none transition-colors focus-visible:ring-4 focus-visible:ring-ring/30',
                    lang === l ? 'bg-lux-gold-soft font-medium text-lux-fg' : 'text-lux-muted hover:text-lux-fg',
                  )}
                >
                  {l}
                </button>
              ))}
            </div>
          </div>

          {/* Waveform */}
          <div aria-hidden="true" className="flex h-16 items-center gap-[3px] px-4 sm:px-6">
            {Array.from({ length: BARS }, (_, i) => (
              <span
                key={i}
                ref={(el) => {
                  bars.current[i] = el
                }}
                className="h-full flex-1 origin-center rounded-full bg-[linear-gradient(var(--lux-gold-bright),var(--lux-gold))] transition-transform duration-75"
                style={{ transform: `scaleY(${reduced ? 0.15 + Math.sin((Math.PI * i) / (BARS - 1)) * 0.5 : 0.1})` }}
              />
            ))}
          </div>

          <div className="grid gap-px bg-lux-line lg:grid-cols-[1.1fr_1fr]">
            {/* Transcript */}
            <div className="bg-lux-surface p-5 sm:p-8">
              <p className="font-mono text-[10px] tracking-[0.28em] text-lux-gold-text uppercase">Live transcript</p>
              <p lang={lang === 'tanglish' ? 'ta-Latn' : 'en'} className="mt-4 min-h-[9.5rem] font-serif text-[clamp(1.25rem,2.4vw,1.75rem)] leading-snug font-light text-lux-fg">
                {words.map((w, i) => (
                  <span
                    key={`${lang}-${i}`}
                    className={cn(
                      'transition-[opacity,background-size] duration-300',
                      i < spoken ? 'opacity-100' : 'opacity-15',
                      i === current && 'bg-[linear-gradient(transparent_62%,var(--lux-gold-soft)_62%)]',
                    )}
                  >
                    {w.word}{' '}
                  </span>
                ))}
              </p>
              <span className="sr-only" aria-live="off">
                {SCRIPTS[lang].text}
              </span>
              <div className="mt-6 flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={() => setAsked(true)}
                  className="inline-flex items-center gap-2 rounded-full border border-lux-line px-4 py-2 text-sm text-lux-fg outline-none transition-colors hover:border-lux-gold focus-visible:ring-4 focus-visible:ring-ring/30"
                >
                  <MessageCircleQuestion aria-hidden="true" className="size-4 text-lux-gold-text" />
                  “Wait — what does range(3) do?”
                </button>
              </div>
              <div aria-live="polite">
                {asked && (
                  <p className="mt-4 rounded-2xl rounded-tl-sm bg-lux-gold-soft px-4 py-3 text-sm leading-relaxed text-lux-fg">
                    {SCRIPTS[lang].answer}
                  </p>
                )}
              </div>
            </div>

            {/* Diagram + code */}
            <div className="flex flex-col gap-5 bg-lux-surface p-5 sm:p-8">
              <p className="font-mono text-[10px] tracking-[0.28em] text-lux-gold-text uppercase">Drawn as she speaks</p>
              <svg viewBox="0 0 320 210" className="w-full text-lux-gold" role="img" aria-label="Flow diagram: a list feeds each item to print, then loops back">
                <g fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <rect x="20" y="80" width="80" height="44" rx="22" pathLength={1} strokeDasharray="1" strokeDashoffset={1 - draw('list')} />
                  <path d="M100 102 H138" pathLength={1} strokeDasharray="1" strokeDashoffset={1 - draw('arrow1')} />
                  <rect x="140" y="80" width="70" height="44" rx="22" pathLength={1} strokeDasharray="1" strokeDashoffset={1 - draw('item')} />
                  <path d="M210 102 H238" pathLength={1} strokeDasharray="1" strokeDashoffset={1 - draw('arrow2')} />
                  <rect x="240" y="80" width="64" height="44" rx="22" pathLength={1} strokeDasharray="1" strokeDashoffset={1 - draw('print')} />
                  <path d="M272 124 C272 190 175 190 175 126" pathLength={1} strokeDasharray="1" strokeDashoffset={1 - draw('loop')} />
                </g>
                <g className="fill-lux-fg font-serif text-[13px] italic">
                  <text x="60" y="106" textAnchor="middle" opacity={draw('list')}>list</text>
                  <text x="175" y="106" textAnchor="middle" opacity={draw('item')}>item</text>
                  <text x="272" y="106" textAnchor="middle" opacity={draw('print')}>print</text>
                  <text x="224" y="200" textAnchor="middle" opacity={draw('loop')} className="fill-lux-gold-text text-[11px]">next item</text>
                </g>
                <circle cx="175" cy="126" r="4" className="fill-lux-oxblood" opacity={draw('loop')} />
              </svg>
              <div className={cn('rounded-2xl bg-lux-stage p-4 font-mono text-[13px] leading-relaxed text-lux-stage-fg transition-opacity duration-500', draw('arrow2') > 0 ? 'opacity-100' : 'opacity-30')}>
                <p className="flex items-center gap-2 text-[10px] tracking-[0.2em] text-lux-stage-fg/50 uppercase">
                  <Terminal aria-hidden="true" className="size-3.5" /> practical · python
                </p>
                <p className="mt-2">
                  <span className="text-lux-gold-bright">for</span> item <span className="text-lux-gold-bright">in</span> [&apos;idli&apos;, &apos;dosa&apos;]:
                </p>
                <p>{'    '}print(item)</p>
                <p className={cn('mt-2 text-lux-stage-fg/60 transition-opacity duration-300', draw('run') > 0 ? 'opacity-100' : 'opacity-0')}>
                  › idli
                  <br />› dosa <span className="text-lux-gold-bright">✓ {SCRIPTS[lang].done}</span>
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
