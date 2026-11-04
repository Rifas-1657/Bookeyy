import type { Metadata } from 'next'
import { LegalDocument, type LegalSection } from '@/components/site/legal-document'

export const metadata: Metadata = {
  title: 'Privacy',
  description: 'What Bookey collects, why, and how to remove it.',
}

const SECTIONS: LegalSection[] = [
  {
    id: 'collect',
    title: 'What we collect',
    body: (
      <ul>
        <li>Your name, email and a hashed password</li>
        <li>Your survey answers, books, notes, bookmarks and reading progress</li>
        <li>Files and links you add when creating a book</li>
        <li>Voice questions, sent for transcription and not kept as recordings</li>
      </ul>
    ),
  },
  {
    id: 'use',
    title: 'How we use it',
    body: <p>We use your data to run your account, build your books and adapt your lessons. We do not sell it or use it for advertising.</p>,
  },
  {
    id: 'services',
    title: 'Services that help us',
    body: (
      <p>
        Bookey runs on hosting, database and AI providers. Text you ask about and voice you record are processed by these providers to
        answer you. They receive only what is needed for that task.
      </p>
    ),
  },
  {
    id: 'choices',
    title: 'Your choices',
    body: <p>You can edit your profile, retake your survey and clear data kept in your browser at any time. To delete your account, contact us.</p>,
  },
]

export default function PrivacyPage() {
  return <LegalDocument eyebrow="Legal" title="Privacy" lead="What Bookey collects, why, and how to remove it." updated="November 2026" sections={SECTIONS} />
}
