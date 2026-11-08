'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { AuthError, getSession, verifyEmail } from '@/lib/api/auth'
import { remoteAccount } from '@/lib/stores/account'
import { authLinkClass } from './auth-submit'

type State = 'checking' | 'done' | 'invalid' | 'error'

export function VerifyEmailStatus() {
  const [state, setState] = useState<State>('checking')
  const started = useRef(false)

  // The token sits in the URL fragment so it never reaches server logs; read it once.
  useEffect(() => {
    if (started.current) return
    started.current = true
    const token = new URLSearchParams(window.location.hash.slice(1)).get('token')
    window.history.replaceState(null, '', window.location.pathname)
    if (!token) {
      setState('invalid')
      return
    }
    verifyEmail(token)
      .then((account) => {
        if (getSession()?.userId === account.user_id) remoteAccount.set(account)
        setState('done')
      })
      .catch((error) => setState(error instanceof AuthError && error.status === 400 ? 'invalid' : 'error'))
  }, [])

  const signedIn = typeof window !== 'undefined' && getSession() !== null
  const next = signedIn ? { href: '/dashboard', label: 'Go to your books' } : { href: '/login', label: 'Log in' }

  return (
    <div role="status" aria-live="polite" className="flex flex-col gap-4 text-sm">
      {state === 'checking' && <p className="text-ink-muted">Confirming your email…</p>}
      {state === 'done' && (
        <p>
          Your email is confirmed. Thank you.{' '}
          <Link href={next.href} className={authLinkClass}>
            {next.label}
          </Link>
        </p>
      )}
      {state === 'invalid' && (
        <p className="text-ink-muted">
          This link has expired or is not complete. Open Bookey and choose <strong>Resend</strong> on the banner to get a new one.{' '}
          <Link href={next.href} className={authLinkClass}>
            {next.label}
          </Link>
        </p>
      )}
      {state === 'error' && <p className="text-ink-muted">We could not reach Bookey just now. Refresh this page to try again.</p>}
    </div>
  )
}
