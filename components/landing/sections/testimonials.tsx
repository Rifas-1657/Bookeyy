import { LUX_TESTIMONIALS } from '../data'
import { Eyebrow } from '../lux/section-heading'
import { Reveal } from '../reveal'

export function Testimonials() {
  const [lead, ...rest] = LUX_TESTIMONIALS
  return (
    <section aria-labelledby="voices-title" className="bg-lux-bg-2 px-4 py-24 sm:px-8 sm:py-32">
      <div className="mx-auto max-w-7xl">
        <Eyebrow>Margin notes</Eyebrow>
        <h2 id="voices-title" className="sr-only">
          What learners and authors say
        </h2>
        <Reveal>
          <figure className="mt-10 max-w-5xl">
            <blockquote className="font-serif text-[clamp(1.6rem,4vw,3.25rem)] leading-[1.12] font-light text-lux-fg">
              <span aria-hidden="true" className="mr-1 text-lux-gold">
                “
              </span>
              {lead.quote}
              <span aria-hidden="true" className="text-lux-gold">
                ”
              </span>
            </blockquote>
            <figcaption className="mt-6 text-sm text-lux-muted">
              <span className="text-lux-fg">{lead.name}</span> — {lead.role}
            </figcaption>
          </figure>
        </Reveal>
        <div className="mt-16 grid gap-6 md:grid-cols-2">
          {rest.map((t, i) => (
            <Reveal key={t.name} delay={i * 0.1}>
              <figure className="h-full rounded-[1.5rem] border border-lux-line bg-lux-surface p-7">
                <blockquote className="font-serif text-lg leading-relaxed text-lux-fg italic">“{t.quote}”</blockquote>
                <figcaption className="mt-5 text-sm text-lux-muted">
                  <span className="text-lux-fg">{t.name}</span> — {t.role}
                </figcaption>
              </figure>
            </Reveal>
          ))}
        </div>
        <p className="mt-6 text-xs text-lux-muted">Illustrative quotes from pilot personas.</p>
      </div>
    </section>
  )
}
