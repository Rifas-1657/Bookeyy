'use client'

import { useCallback, useEffect, useMemo } from 'react'
import { apiJson, isBackendConfigured } from '@/lib/api/auth'
import type { InboxCategory, InboxItem } from '@/lib/mock/inbox'
import { createRemoteResource, useRemote } from './remote'

/** Server notifications (`/me/notifications`): "your book is ready", "reviews due", … */
interface ServerNotification {
  id: string
  kind: string
  title: string
  body: string
  read: boolean
  created_at: string
  href?: string | null
}

const LIVE = isBackendConfigured()
const REFRESH_MS = 120_000

const CATEGORY: Record<string, InboxCategory> = { book_ready: 'book', review_due: 'learning' }
const CTA: Record<string, string> = { book_ready: 'Open your book', review_due: 'Start review' }

function relativeTime(iso: string): string {
  // The API sends naive UTC timestamps.
  const then = Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : `${iso}Z`)
  if (Number.isNaN(then)) return ''
  const minutes = Math.round((Date.now() - then) / 60_000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} h ago`
  const days = Math.round(hours / 24)
  return days === 1 ? 'Yesterday' : `${days} days ago`
}

function toItem(n: ServerNotification): InboxItem {
  return {
    id: n.id,
    category: CATEGORY[n.kind] ?? 'system',
    title: n.title,
    body: n.body,
    when: relativeTime(n.created_at),
    read: n.read,
    href: n.href ?? undefined,
    cta: CTA[n.kind],
  }
}

const remoteInbox = createRemoteResource<InboxItem[]>('/me/notifications', [], (raw) =>
  Array.isArray(raw) ? (raw as ServerNotification[]).map(toItem) : [],
)

/** Re-reads the inbox now (e.g. right after a book finishes building). */
export function refreshNotifications() {
  if (LIVE) void remoteInbox.load(true)
}

export function useNotificationCenter() {
  const { data: items, status } = useRemote(remoteInbox, LIVE)

  // New notifications appear without a reload: on focus and every couple of minutes while visible.
  useEffect(() => {
    if (!LIVE) return
    const refresh = () => {
      if (document.visibilityState === 'visible') void remoteInbox.load(true)
    }
    const timer = window.setInterval(refresh, REFRESH_MS)
    window.addEventListener('focus', refresh)
    return () => {
      window.clearInterval(timer)
      window.removeEventListener('focus', refresh)
    }
  }, [])

  const markRead = useCallback(
    (id: string) =>
      remoteInbox.mutate(
        (list) => list.map((item) => (item.id === id ? { ...item, read: true } : item)),
        () => apiJson(`/me/notifications/${encodeURIComponent(id)}/read`, { method: 'POST' }),
        'Could not mark as read',
      ),
    [],
  )

  const markAllRead = useCallback((ids: string[]) => {
    const wanted = new Set(ids)
    const unread = remoteInbox.get().data.filter((item) => !item.read)
    const everything = unread.every((item) => wanted.has(item.id))
    return remoteInbox.mutate(
      (list) => list.map((item) => (wanted.has(item.id) ? { ...item, read: true } : item)),
      () =>
        everything
          ? apiJson('/me/notifications/read-all', { method: 'POST' })
          : Promise.all(ids.map((id) => apiJson(`/me/notifications/${encodeURIComponent(id)}/read`, { method: 'POST' }))),
      'Could not mark as read',
    )
  }, [])

  const remove = useCallback(
    (id: string) =>
      remoteInbox.mutate(
        (list) => list.filter((item) => item.id !== id),
        () => apiJson(`/me/notifications/${encodeURIComponent(id)}`, { method: 'DELETE' }),
        'Could not remove the notification',
      ),
    [],
  )

  const unreadCount = useMemo(() => items.filter((item) => !item.read).length, [items])
  return { items, status, unreadCount, markRead, markAllRead, remove }
}
