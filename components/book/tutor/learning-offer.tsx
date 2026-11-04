'use client'

import { ListChecks, RotateCcw, X } from 'lucide-react'

/**
 * A small margin note offering a graded check: the spaced review at the start of a lesson
 * ("Quick 1-minute review of yesterday's mistakes?") or the end-of-page check.
 */
export function LearningOffer({
  kind,
  text,
  acceptLabel,
  declineLabel,
  onAccept,
  onDecline,
}: {
  kind: 'review' | 'check'
  text: string
  acceptLabel: string
  declineLabel: string
  onAccept: () => void
  onDecline: () => void
}) {
  const Icon = kind === 'review' ? RotateCcw : ListChecks
  return (
    <div
      role="status"
      data-theme="book"
      className="pointer-events-auto absolute bottom-3 left-1/2 z-30 flex w-[min(24rem,calc(100%-1.5rem))] -translate-x-1/2 items-center gap-2.5 rounded-2xl border border-book-amber/40 bg-book-page/95 px-3 py-2.5 font-sans text-sm text-book-ink shadow-lg animate-in fade-in slide-in-from-bottom-2"
    >
      <Icon className="size-4 shrink-0 text-book-deep" aria-hidden="true" />
      <p className="min-w-0 flex-1 leading-snug">{text}</p>
      <button
        type="button"
        onClick={onAccept}
        className="shrink-0 rounded-full bg-book-orange px-3 py-1.5 text-xs font-semibold hover:bg-book-marker focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        {acceptLabel}
      </button>
      <button
        type="button"
        onClick={onDecline}
        aria-label={declineLabel}
        className="grid size-7 shrink-0 place-items-center rounded-full hover:bg-book-marker/30 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
      >
        <X className="size-3.5" aria-hidden="true" />
      </button>
    </div>
  )
}
