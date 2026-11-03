import { cn } from '@/lib/utils'

/** Serif wordmark with a gilt book mark, for the luxury marketing surface. */
export function LuxWordmark({ className }: { className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-2.5 text-lux-fg', className)}>
      <svg viewBox="0 0 28 28" aria-hidden="true" className="size-7 shrink-0 text-lux-gold">
        <path d="M14 7.5C11 5.6 7.4 5.2 4 6v14.5c3.4-.8 7-.4 10 1.5 3-1.9 6.6-2.3 10-1.5V6c-3.4-.8-7-.4-10 1.5Z" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
        <path d="M14 7.5V22" stroke="currentColor" strokeWidth="1.4" />
        <path d="M14 2.5l1.1 2.1L14 6.7l-1.1-2.1z" fill="currentColor" />
      </svg>
      <span className="font-serif text-[1.45rem] leading-none font-normal tracking-[-0.02em]">Bookey</span>
    </span>
  )
}
