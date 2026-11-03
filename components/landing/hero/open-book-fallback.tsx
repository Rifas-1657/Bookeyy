import { cn } from '@/lib/utils'

function PageLines({ count, className }: { count: number; className?: string }) {
  return (
    <div aria-hidden="true" className={cn('flex flex-col gap-[0.6em]', className)}>
      {Array.from({ length: count }, (_, i) => (
        <span key={i} className="block h-[0.28em] rounded-full bg-book-ink/18" style={{ width: `${i % 4 === 3 ? 56 : 94}%` }} />
      ))}
    </div>
  )
}

/**
 * Static open book — used without WebGL, on low-end devices, under reduced
 * motion, and as the placeholder while the 3D canvas streams in. It occupies
 * exactly the same box as the canvas, so swapping never shifts layout.
 */
export function OpenBookFallback({ className }: { className?: string }) {
  return (
    <div className={cn('grid place-items-center [perspective:1800px]', className)} role="img" aria-label="An open leather-bound Bookey book under a warm reading light">
      <div
        aria-hidden="true"
        className="absolute top-[4%] left-1/2 h-[70%] w-[46%] -translate-x-1/2 bg-[conic-gradient(from_180deg_at_50%_0%,transparent_158deg,var(--lux-glow)_180deg,transparent_202deg)] opacity-80 blur-md"
      />
      <div className="relative mt-[8%] aspect-[1.55/1] w-[min(86%,600px)] [transform:rotateX(52deg)_rotateZ(-8deg)] [transform-style:preserve-3d] text-[clamp(7px,1.05vw,13px)]">
        {/* Leather boards */}
        <div aria-hidden="true" className="absolute -inset-[3.5%] rounded-[0.9em] bg-lux-oxblood shadow-[0_70px_90px_-25px_var(--lux-shadow)] [transform:translateZ(-16px)]">
          <div className="absolute inset-[2.5%] rounded-[0.6em] border border-lux-gold/60" />
        </div>
        {/* Gilt block */}
        <div aria-hidden="true" className="absolute -inset-[0.8%] rounded-[0.5em] bg-[linear-gradient(90deg,var(--lux-gold),var(--lux-gold-bright)_50%,var(--lux-gold))] [transform:translateZ(-7px)]" />
        <div className="absolute inset-y-0 left-0 w-1/2 origin-right rounded-l-[0.5em] bg-[linear-gradient(90deg,var(--book-paper)_0%,var(--book-paper)_82%,color-mix(in_oklab,var(--book-paper)_80%,var(--book-ink))_100%)] p-[9%] [transform:rotateY(9deg)]">
          <p className="font-mono text-[0.72em] tracking-[0.28em] text-book-deep uppercase">Chapter III</p>
          <p className="mt-[0.7em] font-serif text-[1.85em] leading-[1.05] font-light text-book-ink">
            Loops that <em className="italic">repeat for you</em>
          </p>
          <PageLines count={6} className="mt-[1.4em]" />
        </div>
        <div className="absolute inset-y-0 right-0 w-1/2 origin-left rounded-r-[0.5em] bg-[linear-gradient(90deg,color-mix(in_oklab,var(--book-paper)_80%,var(--book-ink))_0%,var(--book-paper)_18%,var(--book-paper)_100%)] p-[9%] [transform:rotateY(-9deg)]">
          <p className="font-mono text-[0.72em] tracking-[0.28em] text-book-deep uppercase">Try it</p>
          <div className="mt-[0.8em] rounded-[0.6em] bg-book-ink p-[0.9em] font-mono text-[0.82em] leading-relaxed text-book-paper">
            <span className="text-book-marker">for</span> item <span className="text-book-marker">in</span> items:
            <br />
            {'  '}print(item)
          </div>
          <PageLines count={4} className="mt-[1.2em]" />
        </div>
        <div aria-hidden="true" className="absolute -bottom-[16%] left-[51%] h-[22%] w-[2.4%] rounded-b-sm bg-lux-oxblood [transform:translateZ(2px)]" />
      </div>
    </div>
  )
}
