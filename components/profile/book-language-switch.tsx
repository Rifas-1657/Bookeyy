'use client'

import { Languages } from 'lucide-react'
import { useState } from 'react'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { notify } from '@/components/ui-kit/toast'
import { apiJson } from '@/lib/api/auth'
import { usePersonalization, type PreferredLanguage } from '@/lib/stores/personalization'
import { usePersonalBook } from '@/lib/stores/progress'

const LABELS: Record<PreferredLanguage, string> = { tanglish: 'Tanglish', english: 'English' }

/** Swaps the learner's own book between languages in a few seconds, keeping their progress. */
export function BookLanguageSwitch() {
  const book = usePersonalBook()
  const { personalization } = usePersonalization('python')
  const [busy, setBusy] = useState(false)
  if (!book) return null
  const other: PreferredLanguage = personalization.language === 'english' ? 'tanglish' : 'english'

  async function change() {
    setBusy(true)
    try {
      await apiJson('/api/books/personal/language', { method: 'POST', body: JSON.stringify({ language: other }) })
      window.location.reload()
    } catch (error) {
      setBusy(false)
      notify.error('Could not change the language', error instanceof Error ? error.message : undefined)
    }
  }

  return (
    <div className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p className="text-sm font-medium">Your book is in {LABELS[personalization.language]}</p>
        <p className="text-xs text-muted-foreground">Switching takes a few seconds and keeps your progress.</p>
      </div>
      <BookeyButton type="button" variant="outline" disabled={busy} onClick={() => void change()}>
        <Languages aria-hidden="true" />
        {busy ? 'Switching…' : `Switch to ${LABELS[other]}`}
      </BookeyButton>
    </div>
  )
}
