'use client'

import { useEffect, useRef } from 'react'
import { useReducedMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'
import styles from '../book-feel.module.css'

export type TutorPresenceState = 'idle' | 'speaking' | 'listening' | 'thinking'

export const TUTOR_NAME = 'Nila'

const stateLabel: Record<TutorPresenceState, string> = {
  idle: 'is here with you',
  speaking: 'is speaking',
  listening: 'is listening',
  thinking: 'is thinking',
}

/**
 * The tutor's presence: a warm orb with a soft face glyph that breathes when
 * idle, pulses with the live voice amplitude while speaking, ripples while
 * listening and shimmers while thinking. Amplitude is written straight to CSS
 * variables from rAF, so it never re-renders React.
 */
export function TutorOrb({
  state,
  getLevel,
  size = 44,
  showLabel = false,
  className,
}: {
  state: TutorPresenceState
  getLevel?: () => number
  size?: number
  showLabel?: boolean
  className?: string
}) {
  const ref = useRef<HTMLDivElement>(null)
  const reduced = useReducedMotion()

  useEffect(() => {
    const node = ref.current
    if (!node) return
    if (state !== 'speaking' || reduced || !getLevel) {
      node.style.setProperty('--orb-level', '0')
      node.style.setProperty('--orb-level-scale', '1')
      node.style.setProperty('--orb-halo', state === 'speaking' ? '0.6' : '0.35')
      return
    }
    let raf = 0
    let smooth = 0
    const tick = () => {
      const level = Math.max(0, Math.min(1, getLevel()))
      smooth += (level - smooth) * 0.35
      node.style.setProperty('--orb-level', smooth.toFixed(3))
      node.style.setProperty('--orb-level-scale', (0.94 + smooth * 0.16).toFixed(3))
      node.style.setProperty('--orb-halo', (0.35 + smooth * 0.6).toFixed(3))
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [state, getLevel, reduced])

  return (
    <div className={cn('flex items-center gap-2', className)}>
      <div
        ref={ref}
        role="img"
        aria-label={`${TUTOR_NAME}, your tutor, ${stateLabel[state]}`}
        data-state={state}
        className={styles.orb}
        style={{ width: size, height: size }}
      >
        <span className={styles.orbHalo} />
        <span className={styles.orbRing} />
        <span className={styles.orbRing} />
        <span className={styles.orbCore} />
        {/* Soft, abstract face: closed smiling eyes and a smile. */}
        <svg viewBox="0 0 24 24" className={styles.orbFace} aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
          <path d="M7.5 10.5q1.2-1.2 2.4 0M14.1 10.5q1.2-1.2 2.4 0" />
          <path d={state === 'speaking' ? 'M9 14.5q3 3 6 0q-3 1.2-6 0' : 'M9.2 14.6q2.8 2 5.6 0'} />
        </svg>
      </div>
      {showLabel && (
        <div className="min-w-0 leading-tight">
          <p className="font-display text-sm font-semibold">{TUTOR_NAME}</p>
          <p className="text-[11px] text-muted-foreground">Your tutor {stateLabel[state]}</p>
        </div>
      )}
    </div>
  )
}
