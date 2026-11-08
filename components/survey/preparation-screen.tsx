'use client'

import { motion } from 'framer-motion'
import { ArrowRight, Check, RotateCcw } from 'lucide-react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { apiJson, AuthError, isBackendConfigured } from '@/lib/api/auth'
import { easeOutExpo } from '@/lib/motion'
import { refreshNotifications } from '@/lib/stores/notifications'
import { refreshPersonalBook } from '@/lib/stores/progress'
import type { Personalization } from '@/lib/stores/personalization'
import { cn } from '@/lib/utils'

const POLL_MS = 3000
const START_TRIES = 5
const START_TIMEOUT_MS = 30000

interface BookStep {
  key: string
  label: string
  status: string
  detail?: string
}

interface PersonalBook {
  exists: boolean
  courseId?: string
  progress?: number
  steps?: BookStep[]
  status?: string
  error?: string | null
  ready?: boolean
}

interface PreparationScreenProps {
  courseId: string
  courseTitle: string
  answers: Personalization
  summary: { label: string; value: string }[]
  onEdit: () => void
}

/** Writes the learner's own book, then opens it. Nothing opens until the whole book exists. */
export function PreparationScreen({ courseId, courseTitle, answers, summary, onEdit }: PreparationScreenProps) {
  const router = useRouter()
  const personal = courseId === 'python' && isBackendConfigured()
  const [book, setBook] = useState<PersonalBook | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const [watching, setWatching] = useState(0)
  const started = useRef(-1)
  const headingRef = useRef<HTMLHeadingElement>(null)

  useEffect(() => headingRef.current?.focus(), [])

  // Start once per attempt. React may run effects twice, so this one has no cleanup.
  useEffect(() => {
    if (!personal) {
      router.replace(`/book/${courseId}`)
      return
    }
    if (started.current === attempt) return
    started.current = attempt
    const begin = async () => {
      for (let tries = 1; ; tries++) {
        try {
          await apiJson('/api/books/personal', {
            method: 'POST',
            body: JSON.stringify(answers),
            headers: { 'Content-Type': 'application/json' },
            signal: AbortSignal.timeout(START_TIMEOUT_MS),
          })
          setWatching(attempt + 1)
          return
        } catch (error) {
          const final = error instanceof AuthError && error.status < 500
          if (final || tries === START_TRIES) {
            setFailure(error instanceof Error && final ? error.message : 'We could not start your book. Please try again.')
            return
          }
          await new Promise((resolve) => window.setTimeout(resolve, 3000))
        }
      }
    }
    void begin()
  }, [answers, attempt, courseId, personal, router])

  useEffect(() => {
    if (!watching) return
    let timer: number | undefined
    let stopped = false

    async function poll() {
      try {
        const next = await apiJson<PersonalBook>('/api/books/personal')
        if (stopped) return
        setBook(next)
        if (next.ready && next.courseId) {
          refreshPersonalBook()
          refreshNotifications()
          router.replace(`/book/${next.courseId}`)
          return
        }
        if (next.status === 'failed') {
          setFailure(next.error || 'We could not finish your book.')
          return
        }
      } catch {
        // A sleeping server wakes up within a minute or so: keep waiting.
      }
      if (!stopped) timer = window.setTimeout(poll, POLL_MS)
    }

    void poll()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [watching, router])

  const steps = book?.steps ?? [
    { key: 'plan', label: 'Choosing your chapters', status: 'running' },
    { key: 'write', label: 'Writing your examples', status: 'pending' },
    { key: 'publish', label: 'Binding your book', status: 'pending' },
  ]
  const percent = Math.min(99, book?.ready ? 100 : (book?.progress ?? 2))

  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-6 py-8">
      <div className="flex flex-col gap-3">
        <h1
          ref={headingRef}
          tabIndex={-1}
          className="font-display text-4xl leading-[1.05] font-bold tracking-tight outline-none sm:text-5xl"
        >
          {failure ? 'Your book needs another try.' : 'Writing your book…'}
        </h1>
        <p className="text-lg text-muted-foreground">
          {failure ?? `${courseTitle}, rebuilt around your answers. This takes up to 5 minutes and opens by itself when it is ready.`}
        </p>
      </div>

      {!failure && (
        <div className="flex flex-col gap-2">
          <div
            role="progressbar"
            aria-label="Book progress"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            className="h-2 overflow-hidden rounded-full bg-muted"
          >
            <div
              className="h-full rounded-full bg-gradient-to-r from-primary to-brand-pink transition-[width] duration-700 ease-out motion-reduce:transition-none"
              style={{ width: `${Math.max(percent, 4)}%` }}
            />
          </div>
          <p className="text-right font-mono text-xs text-muted-foreground">{percent}%</p>
        </div>
      )}

      <ol className="flex flex-col gap-3" aria-live="polite">
        {steps.map((step, index) => {
          const done = step.status === 'done'
          const active = step.status === 'running'
          return (
            <motion.li
              key={step.key}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: !done && !active ? 0.45 : 1, y: 0 }}
              transition={{ duration: 0.5, ease: easeOutExpo, delay: index * 0.05 }}
              className={cn(
                'flex items-center gap-4 rounded-2xl border bg-card px-4 py-3',
                active ? 'border-primary/50 shadow-[0_10px_30px_-20px_var(--primary)]' : 'border-border',
              )}
            >
              <span
                className={cn(
                  'flex size-8 shrink-0 items-center justify-center rounded-full',
                  done ? 'bg-primary text-primary-foreground' : 'bg-secondary text-secondary-foreground',
                )}
              >
                {done ? (
                  <Check aria-hidden="true" className="size-4" strokeWidth={3} />
                ) : active ? (
                  <span aria-hidden="true" className="size-3 animate-pulse rounded-full bg-primary motion-reduce:animate-none" />
                ) : (
                  <span aria-hidden="true" className="font-mono text-xs">{index + 1}</span>
                )}
              </span>
              <span className="flex min-w-0 flex-col">
                <span className="font-medium">{step.label}</span>
                {step.detail && <span className="truncate text-sm text-muted-foreground">{step.detail}</span>}
              </span>
            </motion.li>
          )
        })}
      </ol>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 rounded-3xl bg-secondary/50 p-5 sm:grid-cols-3">
        {summary.map((item) => (
          <div key={item.label} className="flex flex-col gap-0.5">
            <dt className="font-mono text-[10px] tracking-[0.16em] text-muted-foreground uppercase">{item.label}</dt>
            <dd className="font-medium">{item.value}</dd>
          </div>
        ))}
      </dl>

      {failure && (
        <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
          <BookeyButton variant="ghost" onClick={onEdit}>
            <RotateCcw aria-hidden="true" />
            Edit answers
          </BookeyButton>
          <BookeyButton
            size="lg"
            onClick={() => {
              setFailure(null)
              setBook(null)
              setAttempt((n) => n + 1)
            }}
          >
            Try again
            <ArrowRight aria-hidden="true" />
          </BookeyButton>
        </div>
      )}
      {failure && (
        <Link href="/courses" className="text-center text-sm text-muted-foreground underline">
          Back to courses
        </Link>
      )}
    </div>
  )
}
