'use client'

import { Check, Loader2, X } from 'lucide-react'
import type { BuildStep, CourseLanguage, CourseLevel, LinkSourceType, SourcePreview } from '@/lib/api/course-builder'
import { cn } from '@/lib/utils'

export interface DraftLinkSource {
  id: string
  type: LinkSourceType
  url: string
  preview?: SourcePreview
  previewError?: string
  loading?: boolean
}

export interface DraftUpload {
  id: string
  documentId?: string
  filename: string
  sizeBytes: number
  status: 'uploading' | 'ready' | 'failed'
  error?: string
}

export interface BuilderDetails {
  title: string
  description: string
  level: CourseLevel
  language: CourseLanguage
  targetAudience: string
}

export const EMPTY_DETAILS: BuilderDetails = { title: '', description: '', level: 'beginner', language: 'tanglish', targetAudience: '' }

export const STEPS = [
  { key: 'details', label: 'Course details' },
  { key: 'sources', label: 'Sources' },
  { key: 'build', label: 'Build' },
  { key: 'outline', label: 'Outline' },
  { key: 'publish', label: 'Publish' },
] as const

export type StepKey = (typeof STEPS)[number]['key']

export function Stepper({ current, reachable, onSelect }: { current: number; reachable: number; onSelect: (i: number) => void }) {
  return (
    <nav aria-label="Builder steps">
      <ol className="flex gap-1 overflow-x-auto rounded-2xl border border-border bg-card p-1.5 [scrollbar-width:none]">
        {STEPS.map((s, i) => {
          const done = i < current
          const active = i === current
          const disabled = i > reachable
          return (
            <li key={s.key} className="min-w-0 flex-1">
              <button
                type="button"
                disabled={disabled}
                aria-current={active ? 'step' : undefined}
                onClick={() => onSelect(i)}
                className={cn(
                  'flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm whitespace-nowrap outline-none transition-colors focus-visible:ring-4 focus-visible:ring-ring/30 disabled:opacity-45',
                  active ? 'bg-secondary font-medium text-secondary-foreground' : 'text-muted-foreground hover:bg-muted',
                )}
              >
                <span
                  className={cn(
                    'grid size-6 shrink-0 place-items-center rounded-full border text-xs',
                    done ? 'border-primary bg-primary text-primary-foreground' : active ? 'border-primary text-primary' : 'border-border',
                  )}
                >
                  {done ? <Check aria-hidden="true" className="size-3.5" /> : i + 1}
                </span>
                <span className={cn(!active && 'max-sm:sr-only')}>{s.label}</span>
              </button>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}

export function StepTimeline({ steps }: { steps: BuildStep[] }) {
  if (!steps.length) return <p className="text-sm text-muted-foreground">Waiting for the first step…</p>
  return (
    <ol className="relative space-y-4 before:absolute before:top-2 before:bottom-2 before:left-[11px] before:w-px before:bg-border">
      {steps.map((step) => (
        <li key={step.key} className="relative flex gap-3">
          <span
            className={cn(
              'relative z-10 grid size-6 shrink-0 place-items-center rounded-full border bg-card',
              step.status === 'done' && 'border-primary bg-primary text-primary-foreground',
              step.status === 'running' && 'border-primary text-primary',
              step.status === 'failed' && 'border-destructive bg-destructive text-white',
            )}
          >
            {step.status === 'done' && <Check aria-hidden="true" className="size-3.5" />}
            {step.status === 'running' && <Loader2 aria-hidden="true" className="size-3.5 motion-safe:animate-spin" />}
            {step.status === 'failed' && <X aria-hidden="true" className="size-3.5" />}
          </span>
          <div className="min-w-0 pt-0.5">
            <p className={cn('text-sm', step.status === 'pending' ? 'text-muted-foreground' : 'font-medium')}>
              {step.label}
              <span className="sr-only">: {step.status}</span>
            </p>
            {step.detail && <p className="text-xs text-muted-foreground">{step.detail}</p>}
          </div>
        </li>
      ))}
    </ol>
  )
}

export function formatDuration(s?: number) {
  if (!s || !Number.isFinite(s)) return null
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = Math.floor(s % 60)
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`
}

export function formatBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export const uid = () => Math.random().toString(36).slice(2, 10)
