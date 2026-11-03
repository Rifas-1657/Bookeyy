'use client'

import { AnimatePresence, motion, useMotionValueEvent, useScroll } from 'framer-motion'
import { Menu, X } from 'lucide-react'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { easeOutExpo } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { LANDING_NAV, ROUTES } from './data'
import { LuxWordmark } from './lux/lux-wordmark'
import { ThemeToggle } from './lux/theme-toggle'

export function LandingHeader() {
  const { scrollY } = useScroll()
  const [scrolled, setScrolled] = useState(false)
  const [open, setOpen] = useState(false)
  const toggleRef = useRef<HTMLButtonElement>(null)
  const pathname = usePathname()

  useMotionValueEvent(scrollY, 'change', (value) => setScrolled(value > 24))

  useEffect(() => {
    if (!open) return
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false)
        toggleRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  // Inner pages open on a dark hero band, so the bar is always solid there.
  const elevated = scrolled || open || pathname !== '/'

  return (
    <header className="pointer-events-none fixed inset-x-0 top-0 z-50 px-3 pt-3 sm:px-5 sm:pt-4">
      <a
        href="#main"
        className="pointer-events-auto sr-only focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-50 focus:rounded-full focus:bg-card focus:px-4 focus:py-2"
      >
        Skip to content
      </a>
      <div
        className={cn(
          'pointer-events-auto mx-auto max-w-6xl rounded-[1.75rem] border transition-[background-color,border-color,box-shadow,backdrop-filter] duration-500',
          elevated
            ? 'border-lux-line bg-lux-surface/75 shadow-[0_20px_50px_-30px_var(--lux-shadow)] backdrop-blur-xl'
            : 'border-transparent bg-transparent',
        )}
      >
        <div className="flex h-14 items-center justify-between gap-4 pr-2 pl-4 sm:h-16 sm:pl-5">
          <Link href="/" aria-label="Bookey home" className="rounded-lg">
            <LuxWordmark />
          </Link>

          <nav aria-label="Primary" className="hidden md:block">
            <ul className="flex items-center gap-1">
              {LANDING_NAV.map((item) => (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    aria-current={pathname === item.href ? 'page' : undefined}
                    className="rounded-full px-3.5 py-2 text-sm text-lux-muted transition-colors hover:text-lux-fg aria-[current=page]:text-lux-gold-text"
                  >
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>

          <div className="flex items-center gap-1.5">
            <ThemeToggle />
            <Link
              href={ROUTES.login}
              className="hidden rounded-full px-4 py-2 text-sm text-lux-muted transition-colors hover:text-lux-fg sm:inline-flex"
            >
              Log in
            </Link>
            <BookeyButton size="sm" nativeButton={false} render={<Link href={ROUTES.signup} />} className="hidden sm:inline-flex">
              Start learning
            </BookeyButton>
            <button
              ref={toggleRef}
              type="button"
              className="inline-grid size-10 place-items-center rounded-full text-lux-fg hover:bg-lux-fg/8 md:hidden"
              aria-expanded={open}
              aria-controls="landing-mobile-menu"
              onClick={() => setOpen((v) => !v)}
            >
              {open ? <X aria-hidden="true" /> : <Menu aria-hidden="true" />}
              <span className="sr-only">{open ? 'Close menu' : 'Open menu'}</span>
            </button>
          </div>
        </div>

        <AnimatePresence initial={false}>
          {open && (
            <motion.nav
              id="landing-mobile-menu"
              aria-label="Mobile"
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.45, ease: easeOutExpo }}
              className="overflow-hidden md:hidden"
            >
              <ul className="flex flex-col gap-1 border-t border-lux-line px-3 pt-3 pb-2">
                {LANDING_NAV.map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={() => setOpen(false)}
                      aria-current={pathname === item.href ? 'page' : undefined}
                      className="block rounded-2xl px-3 py-3 font-serif text-2xl font-light text-lux-fg hover:bg-lux-fg/5 aria-[current=page]:text-lux-gold-text"
                    >
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
              <div className="grid grid-cols-2 gap-2 px-3 pb-4">
                <BookeyButton
                  variant="outline"
                  nativeButton={false}
                  render={<Link href={ROUTES.login} onClick={() => setOpen(false)} />}
                >
                  Log in
                </BookeyButton>
                <BookeyButton nativeButton={false} render={<Link href={ROUTES.signup} onClick={() => setOpen(false)} />}>
                  Start learning
                </BookeyButton>
              </div>
            </motion.nav>
          )}
        </AnimatePresence>
      </div>
    </header>
  )
}
