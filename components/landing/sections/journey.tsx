import { Camera, Hammer, HeartHandshake, MessageCircleQuestion, WifiOff } from 'lucide-react'
import { SectionHeading } from '../lux/section-heading'
import { Reveal } from '../reveal'

const ACTS = [
  {
    numeral: 'I',
    title: 'She teaches, like a person',
    body: 'Not a robot reading slides. She pauses, checks “puriyudha?”, draws the idea, and slows down when you do.',
    icon: HeartHandshake,
  },
  {
    numeral: 'II',
    title: 'You interrupt, anytime',
    body: 'Tap to ask. She answers from your sources, in Tanglish or English, then picks up exactly where she stopped.',
    icon: MessageCircleQuestion,
  },
  {
    numeral: 'III',
    title: 'You build something real',
    body: 'Each lesson ends with small tasks; each course ends with a project. She encourages every attempt — not just the right ones.',
    icon: Hammer,
  },
  {
    numeral: 'IV',
    title: 'The book remembers',
    body: 'Your screenshots, notes and a recap are bound into the book. Open it on the train — it works offline.',
    icon: Camera,
  },
]

export function Journey() {
  return (
    <section aria-labelledby="journey-title" className="bg-lux-bg px-4 py-24 sm:px-8 sm:py-32">
      <div className="mx-auto max-w-7xl">
        <SectionHeading
          id="journey-title"
          eyebrow="A lesson, in four acts"
          align="center"
          title={
            <>
              Less watching. <em className="italic text-lux-gold-text">More becoming.</em>
            </>
          }
        />
        <ol className="mt-16 grid gap-px overflow-hidden rounded-[1.75rem] border border-lux-line bg-lux-line sm:grid-cols-2 lg:grid-cols-4">
          {ACTS.map(({ numeral, title, body, icon: Icon }, i) => (
            <Reveal as="li" key={numeral} delay={i * 0.08} className="relative bg-lux-surface p-7 sm:p-8">
              <span aria-hidden="true" className="font-serif text-5xl font-light text-lux-gold/70 italic">
                {numeral}
              </span>
              <Icon aria-hidden="true" className="absolute top-8 right-8 size-5 text-lux-gold-text" />
              <h3 className="mt-6 font-serif text-xl font-normal text-lux-fg">{title}</h3>
              <p className="mt-3 text-sm leading-relaxed text-lux-muted">{body}</p>
            </Reveal>
          ))}
        </ol>
        <p className="mt-6 flex items-center justify-center gap-2 text-xs text-lux-muted">
          <WifiOff aria-hidden="true" className="size-3.5" /> Finished books are cached on your device for offline reading.
        </p>
      </div>
    </section>
  )
}
