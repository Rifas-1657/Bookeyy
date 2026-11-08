import type { Metadata } from 'next'
import { AuthHeading } from '@/components/auth/auth-heading'
import { VerifyEmailStatus } from '@/components/auth/verify-email-status'

export const metadata: Metadata = {
  title: 'Confirm your email',
  description: 'Confirm the email address on your Bookey account.',
}

export default function VerifyEmailPage() {
  return (
    <>
      <AuthHeading eyebrow="Account" title="Confirm your email" description="One moment while we check your link." />
      <VerifyEmailStatus />
    </>
  )
}
