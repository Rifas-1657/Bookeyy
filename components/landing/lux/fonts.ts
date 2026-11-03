import { Fraunces } from 'next/font/google'

/** Display serif for the luxury marketing surface. Exposed as `--font-fraunces` → `font-serif`. */
export const luxSerif = Fraunces({
  subsets: ['latin'],
  style: ['normal', 'italic'],
  axes: ['opsz', 'SOFT'],
  variable: '--font-fraunces',
  display: 'swap',
})
