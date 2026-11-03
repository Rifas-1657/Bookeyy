'use client'

import { useReducedMotion } from 'framer-motion'
import dynamic from 'next/dynamic'
import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { useFinePointer } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { useLuxTheme } from '../lux/use-lux-theme'
import { OpenBookFallback } from './open-book-fallback'
import type { SceneDrivers } from './open-book-scene'

const OpenBookScene = dynamic(() => import('./open-book-scene'), { ssr: false, loading: () => null })

type Capability = 'none' | 'lite' | 'rich'

/** WebGL + rough device class. Low-memory / data-saver / tiny-core devices get the static book. */
function detectCapability(): Capability {
  try {
    const nav = navigator as Navigator & { deviceMemory?: number; connection?: { saveData?: boolean } }
    if (nav.connection?.saveData) return 'none'
    const canvas = document.createElement('canvas')
    const gl = canvas.getContext('webgl2') ?? canvas.getContext('webgl')
    if (!gl) return 'none'
    const cores = nav.hardwareConcurrency ?? 4
    const memory = nav.deviceMemory ?? 4
    if (cores <= 2 || memory <= 2) return 'none'
    const coarse = window.matchMedia('(pointer: coarse)').matches
    return coarse || cores <= 4 || memory <= 4 ? 'lite' : 'rich'
  } catch {
    return 'none'
  }
}

const noopSubscribe = () => () => {}

export function HeroVisual({ className, drivers }: { className?: string; drivers: React.RefObject<SceneDrivers> }) {
  const reduced = useReducedMotion()
  const fine = useFinePointer()
  const theme = useLuxTheme()
  const capability = useSyncExternalStore(noopSubscribe, detectCapability, () => 'none' as Capability)
  const ref = useRef<HTMLDivElement>(null)
  const [inView, setInView] = useState(true)
  const [ready, setReady] = useState(false)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting), { rootMargin: '120px' })
    observer.observe(node)
    return () => observer.disconnect()
  }, [])

  // Defer the WebGL chunk until the main thread is idle so text paints first.
  useEffect(() => {
    if (reduced || capability === 'none') return
    const w = window as Window & { requestIdleCallback?: (cb: () => void, o?: { timeout: number }) => number; cancelIdleCallback?: (id: number) => void }
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(() => setReady(true), { timeout: 1200 })
      return () => w.cancelIdleCallback?.(id)
    }
    const id = window.setTimeout(() => setReady(true), 400)
    return () => window.clearTimeout(id)
  }, [reduced, capability])

  useEffect(() => {
    if (!fine || reduced) return
    function onMove(event: PointerEvent) {
      if (event.pointerType !== 'mouse') return
      drivers.current.px = (event.clientX / window.innerWidth) * 2 - 1
      drivers.current.py = (event.clientY / window.innerHeight) * 2 - 1
    }
    window.addEventListener('pointermove', onMove, { passive: true })
    return () => window.removeEventListener('pointermove', onMove)
  }, [fine, reduced, drivers])

  const use3D = ready && !reduced && capability !== 'none'

  return (
    <div ref={ref} className={cn('relative', className)}>
      <div data-hero-fallback-tilt className="absolute inset-0 [perspective:1400px]">
        <OpenBookFallback
          className={cn('absolute inset-0 transition-opacity duration-700', use3D && 'opacity-0 [transition-delay:900ms]')}
        />
      </div>
      {use3D && (
        <div className="absolute inset-0 animate-in fade-in [animation-duration:900ms]">
          <OpenBookScene active={inView} animate={!reduced} drivers={drivers} theme={theme} rich={capability === 'rich'} />
          <span className="sr-only">An open leather-bound book under a warm reading light; words and diagram strokes drift up from its pages.</span>
        </div>
      )}
    </div>
  )
}
