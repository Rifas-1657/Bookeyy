import { Fragment } from 'react'
import type { NarrationWord } from '@/lib/narration/tokenize'
import { cn } from '@/lib/utils'
import styles from './book-feel.module.css'

export interface NarrationTextState {
  words: NarrationWord[]
  /** Active word; -1 = not started, >= words.length = finished. */
  activeIndex: number
  streaming: boolean
  /** Show the whole page even though guided narration hasn't reached the end (e.g. the voice failed). */
  revealAll?: boolean
}

/**
 * Word-synced "ink-in" narration: each word appears on the page at the moment
 * the tutor speaks it (driven by the audio clock when audio exists). Upcoming
 * words keep their space but stay invisible, so the line never reflows.
 */
export function NarrationText({ text, narration }: { text: string; narration?: NarrationTextState }) {
  if (!narration?.streaming) return <>{text}</>
  const { words, activeIndex } = narration
  if (activeIndex < 0) {
    return <span className="text-muted-foreground not-italic">Press play — the page fills in as your tutor teaches it.</span>
  }

  return (
    <>
      <span className="sr-only">{words.slice(0, Math.max(0, activeIndex + 1)).map((w) => w.text).join(' ')}</span>
      <span aria-hidden="true">
        {words.map((word, index) => (
          <Fragment key={index}>
            {index > 0 && ' '}
            <span
              className={cn(
                styles.word,
                index > activeIndex ? styles.wordFuture : styles.wordSpoken,
                index === activeIndex && styles.wordActive,
              )}
            >
              {word.text}
            </span>
          </Fragment>
        ))}
      </span>
    </>
  )
}
