import Link from 'next/link'
import { LANDING_NAV, ROUTES } from './data'
import { LuxWordmark } from './lux/lux-wordmark'

const COLUMNS = [
  { title: 'Product', links: LANDING_NAV },
  {
    title: 'Account',
    links: [
      { href: ROUTES.login, label: 'Log in' },
      { href: ROUTES.signup, label: 'Sign up' },
    ],
  },
  {
    title: 'Legal',
    links: [
      { href: ROUTES.privacy, label: 'Privacy' },
      { href: ROUTES.terms, label: 'Terms' },
    ],
  },
]

export function LandingFooter() {
  return (
    <footer className="border-t border-lux-line bg-lux-bg-2 text-lux-fg">
      <div className="mx-auto grid max-w-7xl gap-12 px-4 py-16 sm:px-8 md:grid-cols-[1.4fr_repeat(3,1fr)]">
        <div>
          <LuxWordmark />
          <p className="mt-4 max-w-xs font-serif text-base leading-relaxed text-lux-muted italic">
            Interactive books with a voice tutor, live diagrams, and practice you can run.
          </p>
        </div>
        {COLUMNS.map((col) => (
          <nav key={col.title} aria-label={col.title}>
            <p className="font-mono text-[11px] tracking-[0.24em] text-lux-gold-text uppercase">{col.title}</p>
            <ul className="mt-4 space-y-2.5">
              {col.links.map((link) => (
                <li key={link.href}>
                  <Link href={link.href} className="text-sm text-lux-muted transition-colors hover:text-lux-fg">
                    {link.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        ))}
      </div>
      <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 border-t border-lux-line px-4 py-6 text-xs text-lux-muted sm:px-8">
        <span>{`© ${new Date().getFullYear()} Bookey. All rights reserved.`}</span>
        <span className="font-serif italic">Made for curious minds, in Tanglish & English.</span>
      </div>
    </footer>
  )
}
