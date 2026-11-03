'use client'

import { Moon, Sun } from 'lucide-react'
import { cn } from '@/lib/utils'
import { setLuxTheme, useLuxTheme } from './use-lux-theme'

export function ThemeToggle({ className }: { className?: string }) {
  const theme = useLuxTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  return (
    <button
      type="button"
      onClick={() => setLuxTheme(next)}
      className={cn(
        'inline-grid size-10 place-items-center rounded-full text-lux-fg/80 transition-colors outline-none hover:bg-lux-fg/8 hover:text-lux-fg focus-visible:ring-4 focus-visible:ring-ring/30',
        className,
      )}
    >
      {theme === 'dark' ? <Sun aria-hidden="true" className="size-[18px]" /> : <Moon aria-hidden="true" className="size-[18px]" />}
      <span className="sr-only">{`Switch to ${next} theme`}</span>
    </button>
  )
}
