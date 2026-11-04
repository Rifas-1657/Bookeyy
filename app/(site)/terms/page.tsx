import type { Metadata } from 'next'
import { LegalDocument, type LegalSection } from '@/components/site/legal-document'

export const metadata: Metadata = {
  title: 'Terms',
  description: 'The rules for using Bookey.',
}

const SECTIONS: LegalSection[] = [
  {
    id: 'account',
    title: 'Your account',
    body: <p>Keep your password safe. You are responsible for what happens under your account.</p>,
  },
  {
    id: 'content',
    title: 'Your content',
    body: <p>Only add material you have the right to use. It stays yours; you let Bookey process it to build your book.</p>,
  },
  {
    id: 'ai',
    title: 'AI-written content',
    body: <p>Parts of your books are written by AI and can contain mistakes. Check anything important before relying on it.</p>,
  },
  {
    id: 'use',
    title: 'Fair use',
    body: <p>Do not try to disrupt the service, reach other people&apos;s data, or use Bookey to break the law. We may suspend accounts that do.</p>,
  },
  {
    id: 'service',
    title: 'The service',
    body: <p>Bookey is provided as it is, and features may change. We do our best to keep it running but cannot promise it always will be.</p>,
  },
]

export default function TermsPage() {
  return <LegalDocument eyebrow="Legal" title="Terms" lead="The rules for using Bookey, in plain language." updated="November 2026" sections={SECTIONS} />
}
