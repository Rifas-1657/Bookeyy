import type { Metadata } from 'next'
import { AuthHeading } from '@/components/auth/auth-heading'
import { ResetPasswordForm } from '@/components/auth/reset-password-form'

export const metadata: Metadata = {
  title: 'Choose a new password',
  description: 'Set a new password for your Bookey account.',
}

export default function ResetPasswordPage() {
  return (
    <>
      <AuthHeading eyebrow="Account help" title="Choose a new password" description="Pick something you haven't used here before." />
      <ResetPasswordForm />
    </>
  )
}
