import { cn } from '@/lib/utils'

export function Eyebrow({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p className={cn('inline-flex items-center gap-3 font-mono text-[11px] tracking-[0.28em] text-lux-gold-text uppercase', className)}>
      <span aria-hidden="true" className="lux-hairline h-px w-8" />
      {children}
    </p>
  )
}

export function SectionHeading({
  id,
  eyebrow,
  title,
  lede,
  align = 'left',
  className,
}: {
  id: string
  eyebrow: string
  title: React.ReactNode
  lede?: React.ReactNode
  align?: 'left' | 'center'
  className?: string
}) {
  return (
    <div className={cn('max-w-3xl', align === 'center' && 'mx-auto text-center', className)}>
      <Eyebrow className={cn(align === 'center' && 'justify-center')}>{eyebrow}</Eyebrow>
      <h2 id={id} className="mt-5 font-serif text-[clamp(2.1rem,5.2vw,4rem)] leading-[1.02] font-light tracking-[-0.025em] text-lux-fg">
        {title}
      </h2>
      {lede && <p className={cn('mt-5 max-w-xl text-base leading-relaxed text-lux-muted sm:text-lg', align === 'center' && 'mx-auto')}>{lede}</p>}
    </div>
  )
}
