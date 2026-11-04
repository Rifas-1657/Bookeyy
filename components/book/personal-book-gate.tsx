'use client'

import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { apiJson } from '@/lib/api/auth'

const POLL_MS = 3000

interface PersonalBook {
  exists: boolean
  courseId?: string
  ready?: boolean
  status?: string
  progress?: number
}

/** `/book/python` is the template: every learner reads their own copy, or takes the survey first. */
export function PersonalBookGate() {
  const router = useRouter()
  const [writing, setWriting] = useState<number | null>(null)

  useEffect(() => {
    let timer: number | undefined
    let stopped = false

    async function check() {
      try {
        const book = await apiJson<PersonalBook>('/api/books/personal')
        if (stopped) return
        if (book.ready && book.courseId) return router.replace(`/book/${book.courseId}`)
        if (!book.exists || book.status === 'failed') return router.replace('/courses/python/survey')
        setWriting(book.progress ?? 0)
        timer = window.setTimeout(check, POLL_MS)
      } catch {
        if (!stopped) router.replace('/login?next=/book/python')
      }
    }

    void check()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [router])

  return (
    <div role="status" aria-live="polite" className="grid min-h-dvh place-items-center text-sm text-muted-foreground">
      {writing === null ? 'Opening your book…' : `Writing your book… ${writing}%. It opens by itself when it is ready.`}
    </div>
  )
}
