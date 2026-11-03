import { cn } from '@/lib/utils'
import { luxSerif } from './fonts'

/**
 * Applies the stored theme choice before paint (no flash) — compatible with
 * next-themes' default `theme` key and class attribute.
 */
const THEME_SCRIPT = `(function(){try{var t=localStorage.getItem('theme');var c=document.documentElement.classList;if(t==='dark'){c.add('dark');c.remove('light')}else if(t==='light'){c.add('light');c.remove('dark')}}catch(e){}})()`

/** Root wrapper for the luxury marketing pages: tokens, serif face, theme bootstrap. */
export function LuxSurface({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={cn('theme-lux min-h-dvh', luxSerif.variable, className)}>
      <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      {children}
    </div>
  )
}
