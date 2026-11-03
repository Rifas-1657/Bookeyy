'use client'

import { CloudDownload, CloudOff, Loader2, ShieldCheck } from 'lucide-react'
import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { getOfflineBook, OFFLINE_EVENT } from '@/lib/offline/db'
import { downloadCourseForOffline, type DownloadCourse, type DownloadProgress } from '@/lib/offline/download'
import { cn } from '@/lib/utils'
import type { BookLessonPage } from '@/types/book'

export function useOfflineBook(courseId: string) {
  const [saved, setSaved] = useState<{ complete: boolean; savedAt: number; audioBytes: number } | null>(null)
  useEffect(() => {
    let alive = true
    const load = () =>
      void getOfflineBook(courseId).then((book) => {
        if (alive) setSaved(book ? { complete: book.complete, savedAt: book.savedAt, audioBytes: book.audioBytes } : null)
      })
    load()
    window.addEventListener(OFFLINE_EVENT, load)
    return () => {
      alive = false
      window.removeEventListener(OFFLINE_EVENT, load)
    }
  }, [courseId])
  return saved
}

/** "Download for offline" button that turns into the "Available offline" badge. */
export function OfflineControl({
  course,
  pages,
  compact = false,
  className,
}: {
  course: DownloadCourse
  pages: BookLessonPage[]
  compact?: boolean
  className?: string
}) {
  const saved = useOfflineBook(course.id)
  const [progress, setProgress] = useState<DownloadProgress | null>(null)

  async function download() {
    if (progress) return
    setProgress({ done: 0, total: pages.length + 2, label: 'Starting' })
    try {
      const book = await downloadCourseForOffline(course, pages, setProgress)
      toast.success('Book saved for offline', {
        description: `${pages.length} pages, narration (${Math.round(book.audioBytes / 1e6)} MB) and the Python lab are on this device.`,
      })
    } catch {
      toast.error('Could not finish the download', { description: 'Check your connection and try again.' })
    } finally {
      setProgress(null)
    }
  }

  if (saved?.complete && !progress) {
    return (
      <span
        className={cn(
          'inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-emerald-400/30 bg-emerald-400/10 px-2.5 text-xs font-medium text-emerald-700 dark:text-emerald-300',
          className,
        )}
        title={`Saved ${new Date(saved.savedAt).toLocaleString()}`}
      >
        <ShieldCheck className="size-3.5" aria-hidden="true" />
        <span className={cn(compact && 'sr-only sm:not-sr-only')}>Available offline</span>
      </span>
    )
  }

  const online = typeof navigator === 'undefined' || navigator.onLine
  return (
    <button
      type="button"
      onClick={download}
      disabled={Boolean(progress) || !online}
      aria-label={progress ? `Downloading: ${progress.label}` : 'Download this book for offline'}
      className={cn(
        'inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-border bg-card/40 px-2.5 text-xs font-medium backdrop-blur-md transition-colors hover:bg-card/70 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-60',
        className,
      )}
    >
      {progress ? (
        <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />
      ) : online ? (
        <CloudDownload className="size-3.5" aria-hidden="true" />
      ) : (
        <CloudOff className="size-3.5" aria-hidden="true" />
      )}
      <span className={cn(compact && 'sr-only sm:not-sr-only')}>
        {progress ? `Saving ${Math.round((progress.done / progress.total) * 100)}%` : 'Download for offline'}
      </span>
    </button>
  )
}
