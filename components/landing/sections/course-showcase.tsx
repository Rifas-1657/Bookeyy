import { Hammer } from 'lucide-react'
import Link from 'next/link'
import { SourceGlyph } from '@/components/ui-kit/source-glyphs'
import { cn } from '@/lib/utils'
import { LUX_COURSES, ROUTES, type LuxCourse, type LuxCoverTone } from '../data'
import { SectionHeading } from '../lux/section-heading'
import { Reveal } from '../reveal'

const COVER: Record<LuxCoverTone, string> = {
  oxblood: 'bg-lux-oxblood text-lux-stage-fg',
  ink: 'bg-lux-stage text-lux-stage-fg',
  gold: 'bg-[linear-gradient(150deg,var(--lux-gold-bright),var(--lux-gold)_60%,var(--lux-gold-text))] text-lux-stage',
  linen: 'bg-lux-bg-2 text-lux-fg ring-1 ring-lux-line ring-inset',
}

function Cover({ course }: { course: LuxCourse }) {
  const light = course.tone === 'gold' || course.tone === 'linen'
  return (
    <div className="[perspective:1100px]">
      <div
        className={cn(
          'relative aspect-[3/4] rounded-[4px_14px_14px_4px] p-[12%] shadow-[0_30px_60px_-30px_var(--lux-shadow),inset_9px_0_14px_-8px_rgb(0_0_0/0.45)] transition-transform duration-700 ease-[cubic-bezier(0.16,1,0.3,1)] [transform:rotateY(-14deg)] [transform-style:preserve-3d] group-hover:[transform:rotateY(-4deg)_translateY(-6px)] motion-reduce:transition-none',
          COVER[course.tone],
        )}
      >
        <div aria-hidden="true" className={cn('absolute inset-[6%] rounded-[3px_10px_10px_3px] border', light ? 'border-lux-stage/30' : 'border-lux-gold/55')} />
        <div aria-hidden="true" className="absolute inset-y-0 left-[7%] w-px bg-[rgb(0_0_0/0.25)]" />
        <p className={cn('relative font-mono text-[9px] tracking-[0.3em] uppercase', light ? 'text-current/70' : 'text-lux-gold-bright')}>Bookey edition</p>
        <p className="relative mt-4 font-serif text-[clamp(1.35rem,2.2vw,1.75rem)] leading-[1.02] font-light">
          {course.title} <em className="italic">{course.italic}</em>
        </p>
        <div className="absolute inset-x-[12%] bottom-[10%] flex items-center justify-between">
          <span className="flex gap-1.5 opacity-80">
            {course.sources.map((s) => (
              <SourceGlyph key={s} kind={s} className="size-4" />
            ))}
          </span>
          <span className="font-mono text-[10px] opacity-70">{course.level.slice(0, 3).toUpperCase()}</span>
        </div>
      </div>
    </div>
  )
}

export function CourseShowcase() {
  return (
    <section aria-labelledby="courses-title" className="bg-lux-bg-2 py-24 sm:py-32">
      <div className="mx-auto max-w-7xl px-4 sm:px-8">
        <div className="flex flex-col gap-6 md:flex-row md:items-end md:justify-between">
          <SectionHeading
            id="courses-title"
            eyebrow="The library"
            title={
              <>
                Courses bound <em className="italic text-lux-gold-text">like keepsakes.</em>
              </>
            }
            lede="Every edition ends with a real project and small tasks along the way — and she cheers you through each one."
          />
          <Link href={ROUTES.courses} className="shrink-0 text-sm text-lux-gold-text underline-offset-4 hover:underline">
            Browse the catalog →
          </Link>
        </div>
      </div>
      <ul
        aria-label="Example course editions"
        className="mt-14 flex snap-x snap-mandatory gap-6 overflow-x-auto px-4 pb-6 [scrollbar-width:none] sm:px-8 lg:mx-auto lg:grid lg:max-w-7xl lg:grid-cols-4 lg:overflow-visible"
      >
        {LUX_COURSES.map((course, i) => (
          <li key={course.title} className="group w-[68vw] max-w-[260px] shrink-0 snap-start lg:w-auto lg:max-w-none">
            <Reveal delay={i * 0.08}>
              <Cover course={course} />
              <div className="mt-6 space-y-2">
                <p className="font-serif text-lg text-lux-fg">
                  {course.title} <em className="italic">{course.italic}</em>
                </p>
                <p className="text-xs text-lux-muted">
                  {course.chapters} chapters · {course.lessons} lessons · {course.languages.join(' / ')}
                </p>
                <p className="flex items-start gap-2 text-sm text-lux-fg/85">
                  <Hammer aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-lux-gold-text" />
                  <span>
                    <span className="sr-only">Final project: </span>
                    {course.project}
                  </span>
                </p>
              </div>
            </Reveal>
          </li>
        ))}
      </ul>
      <p className="mx-auto mt-4 max-w-7xl px-4 text-xs text-lux-muted sm:px-8">Illustrative editions — demo content.</p>
    </section>
  )
}
