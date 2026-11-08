'use client'

import { MailCheck, X } from 'lucide-react'
import { useState } from 'react'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { notify } from '@/components/ui-kit/toast'
import { resendVerification } from '@/lib/api/auth'
import { useAccount } from '@/lib/stores/account'

/** A quiet reminder until the email is confirmed. Never blocks learning. */
export function VerifyEmailBanner() {
  const { data: account, status } = useAccount()
  const [hidden, setHidden] = useState(false)
  const [sending, setSending] = useState(false)

  if (hidden || status !== 'ready' || !account || account.email_verified) return null

  const resend = async () => {
    setSending(true)
    try {
      await resendVerification()
      notify.success('Email sent', `Check ${account.email} for the confirmation link.`)
    } catch {
      notify.error('Could not send the email', 'Try again in a few minutes.')
    } finally {
      setSending(false)
    }
  }

  return (
    <div role="status" className="border-b border-border bg-secondary/50">
      <div className="flex items-center gap-3 px-4 py-2.5 text-sm sm:px-6 lg:px-8">
        <MailCheck aria-hidden="true" className="size-4 shrink-0 text-primary" />
        <p className="min-w-0 flex-1 text-pretty">
          Confirm your email so you can always get back into your account.{' '}
          <span className="text-muted-foreground">We sent a link to {account.email}.</span>
        </p>
        <BookeyButton size="sm" variant="ghost" className="shrink-0" disabled={sending} onClick={resend}>
          {sending ? 'Sending…' : 'Resend'}
        </BookeyButton>
        <BookeyButton size="icon" variant="ghost" className="size-8 shrink-0" aria-label="Hide for now" onClick={() => setHidden(true)}>
          <X aria-hidden="true" />
        </BookeyButton>
      </div>
    </div>
  )
}
