/** Demo notification center seed. Times are relative labels so the demo never depends on today's date. */

export type InboxCategory = 'learning' | 'book' | 'system'

export interface InboxItem {
  id: string
  category: InboxCategory
  title: string
  body: string
  when: string
  /** Read state before the learner touches anything. */
  read: boolean
  href?: string
  cta?: string
}

export const INBOX_CATEGORIES: { value: InboxCategory; label: string }[] = [
  { value: 'learning', label: 'Learning' },
  { value: 'book', label: 'Book updates' },
  { value: 'system', label: 'System' },
]

export const seedInbox: InboxItem[] = []
