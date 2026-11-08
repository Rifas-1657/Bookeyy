'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useRef, useState } from 'react'
import { notify } from '@/components/ui-kit/toast'
import { AuthError, isBackendConfigured, safeNextPath, signup } from '@/lib/api/auth'
import { AuthCheckbox, AuthField, PasswordField } from './auth-field'
import { AuthSubmit, AuthSwitch, authLinkClass } from './auth-submit'
import { PasswordStrengthMeter } from './password-strength'
import {
  focusFirstError,
  validateConfirmPassword,
  validateEmail,
  validateName,
  validateNewPassword,
  type FieldErrors,
} from './validation'

type SignupField = 'name' | 'email' | 'password' | 'confirm' | 'terms'

const DEMO_DELAY_MS = 800
const NEW_LEARNER_PATH = '/courses/python/survey'

export function SignupForm() {
  const router = useRouter()
  const formRef = useRef<HTMLFormElement>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [terms, setTerms] = useState(false)
  const live = isBackendConfigured()
  const [errors, setErrors] = useState<FieldErrors<SignupField>>({})
  const [pending, setPending] = useState(false)

  useEffect(() => () => clearTimeout(timer.current), [])

  function validate(field: SignupField, values = { name, email, password, confirm, terms }) {
    switch (field) {
      case 'name':
        return validateName(values.name)
      case 'email':
        return validateEmail(values.email)
      case 'password':
        return validateNewPassword(values.password)
      case 'confirm':
        return validateConfirmPassword(values.password, values.confirm)
      case 'terms':
        return values.terms ? undefined : 'Please accept the Terms and Privacy Policy to continue.'
    }
  }

  function revalidate(field: SignupField, overrides?: Partial<{ name: string; email: string; password: string; confirm: string; terms: boolean }>) {
    if (!errors[field]) return
    setErrors((prev) => ({ ...prev, [field]: validate(field, { name, email, password, confirm, terms, ...overrides }) }))
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending) return
    const fields: SignupField[] = ['name', 'email', 'password', 'confirm', 'terms']
    const next = Object.fromEntries(fields.map((f) => [f, validate(f)])) as FieldErrors<SignupField>
    setErrors(next)
    if (fields.some((f) => next[f])) {
      requestAnimationFrame(() => focusFirstError(formRef.current))
      return
    }
    setPending(true)
    if (live) {
      try {
        await signup({ email: email.trim(), password, displayName: name.trim() })
        // A new account has no book yet: go straight to the survey that writes it.
        router.replace(safeNextPath(NEW_LEARNER_PATH))
      } catch (err) {
        setPending(false)
        if (err instanceof AuthError && err.status === 409) {
          setErrors({ email: 'An account with this email already exists. Log in instead.' })
        } else if (err instanceof AuthError && err.status === 422) {
          setErrors({ email: 'Check your email and password, then try again.' })
        } else {
          notify.error('Could not create your account', 'The Bookey server did not respond. Try again in a moment.')
          return
        }
        requestAnimationFrame(() => focusFirstError(formRef.current))
      }
      return
    }
    timer.current = setTimeout(() => {
      router.push('/dashboard')
      notify.demo('Demo sign-up — no account was created', 'Authentication is not connected. Nothing was saved to a server.')
    }, DEMO_DELAY_MS)
  }

  return (
    <>
      <form ref={formRef} onSubmit={handleSubmit} noValidate aria-busy={pending}>
        <fieldset disabled={pending} className="flex flex-col gap-5">
          <legend className="sr-only">Create a Bookey profile</legend>
          <AuthField
            id="signup-name"
            label="Name"
            name="name"
            autoComplete="name"
            placeholder="Ada Lovelace"
            value={name}
            error={errors.name}
            onChange={(e) => setName(e.target.value)}
            onBlur={() => revalidate('name')}
          />
          <AuthField
            id="signup-email"
            label="Email"
            type="email"
            name="email"
            autoComplete="email"
            inputMode="email"
            placeholder="name@example.com"
            value={email}
            error={errors.email}
            onChange={(e) => setEmail(e.target.value)}
            onBlur={() => revalidate('email')}
          />
          <PasswordField
            id="signup-password"
            label="Password"
            name="password"
            autoComplete="new-password"
            placeholder="At least 8 characters"
            value={password}
            error={errors.password}
            describedBy="signup-password-strength"
            onChange={(e) => {
              setPassword(e.target.value)
              revalidate('password', { password: e.target.value })
              if (confirm) revalidate('confirm', { password: e.target.value })
            }}
          >
            <PasswordStrengthMeter id="signup-password-strength" password={password} />
          </PasswordField>
          <PasswordField
            id="signup-confirm"
            label="Confirm password"
            name="confirm"
            autoComplete="new-password"
            placeholder="Type it again"
            value={confirm}
            error={errors.confirm}
            onChange={(e) => {
              setConfirm(e.target.value)
              revalidate('confirm', { confirm: e.target.value })
            }}
          />
          <AuthCheckbox
            id="signup-terms"
            name="terms"
            checked={terms}
            error={errors.terms}
            onChange={(e) => {
              setTerms(e.target.checked)
              revalidate('terms', { terms: e.target.checked })
            }}
          >
            I agree to the{' '}
            <Link href="/terms" className={authLinkClass}>
              Terms
            </Link>{' '}
            and{' '}
            <Link href="/privacy" className={authLinkClass}>
              Privacy Policy
            </Link>
            .
          </AuthCheckbox>
          <div className="pt-1">
            <AuthSubmit pending={pending} label="Create account" pendingLabel={live ? 'Creating account…' : 'Setting up…'} />
          </div>
        </fieldset>
        <p aria-live="polite" className="sr-only">
          {pending ? (live ? 'Creating your account.' : 'Opening your workspace.') : ''}
        </p>
      </form>
      <AuthSwitch>
        Already have an account?{' '}
        <Link href="/login" className={authLinkClass}>
          Log in
        </Link>
      </AuthSwitch>
    </>
  )
}
