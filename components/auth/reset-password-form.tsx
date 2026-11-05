'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'
import { notify } from '@/components/ui-kit/toast'
import { AuthError, confirmPasswordReset } from '@/lib/api/auth'
import { PasswordField } from './auth-field'
import { AuthSubmit, AuthSwitch, authLinkClass } from './auth-submit'
import { PasswordStrengthMeter } from './password-strength'
import { validateConfirmPassword, validateNewPassword } from './validation'

export function ResetPasswordForm() {
  const router = useRouter()
  const [token, setToken] = useState<string | null>(null)
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [errors, setErrors] = useState<{ password?: string; confirm?: string; link?: string }>({})
  const [pending, setPending] = useState(false)

  // The token sits in the URL fragment; drop it from the address bar once read.
  useEffect(() => {
    const fromLink = new URLSearchParams(window.location.hash.slice(1)).get('token')
    setToken((current) => fromLink ?? current ?? '')
    if (fromLink) window.history.replaceState(null, '', window.location.pathname)
  }, [])

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending || !token) return
    const next = { password: validateNewPassword(password), confirm: validateConfirmPassword(password, confirm) }
    setErrors(next)
    if (next.password || next.confirm) return
    setPending(true)
    try {
      await confirmPasswordReset(token, password)
      notify.success('Password updated', 'Log in with your new password.')
      router.replace('/login')
    } catch (error) {
      setPending(false)
      if (error instanceof AuthError && error.status === 400) {
        setErrors({ link: 'This link has expired or was already used. Ask for a new one.' })
      } else {
        notify.error('Could not update your password', 'Try again in a moment.')
      }
    }
  }

  if (token === '' || errors.link) {
    return (
      <p role="alert" className="text-sm text-ink-muted">
        {errors.link ?? 'This reset link is not complete.'}{' '}
        <Link href="/forgot-password" className={authLinkClass}>
          Get a new link
        </Link>
      </p>
    )
  }

  return (
    <>
      <form onSubmit={handleSubmit} noValidate aria-busy={pending}>
        <fieldset disabled={pending} className="flex flex-col gap-5">
          <legend className="sr-only">Choose a new password</legend>
          <PasswordField
            id="reset-password"
            label="New password"
            name="password"
            autoComplete="new-password"
            placeholder="At least 8 characters"
            value={password}
            error={errors.password}
            onChange={(e) => setPassword(e.target.value)}
          >
            <PasswordStrengthMeter id="reset-password-strength" password={password} />
          </PasswordField>
          <PasswordField
            id="reset-confirm"
            label="Confirm password"
            name="confirm"
            autoComplete="new-password"
            placeholder="Type it again"
            value={confirm}
            error={errors.confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
          <AuthSubmit pending={pending} label="Update password" pendingLabel="Updating…" />
        </fieldset>
      </form>
      <AuthSwitch>
        Remembered it?{' '}
        <Link href="/login" className={authLinkClass}>
          Log in
        </Link>
      </AuthSwitch>
    </>
  )
}
