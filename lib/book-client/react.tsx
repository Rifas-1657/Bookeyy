'use client'

import { createContext, useContext, useEffect, useEffectEvent, useState } from 'react'
import type { BookClient } from '@/lib/contracts/book-client'
import type { ServerEventOf, ServerEventType } from '@/lib/contracts/book-events'
import { LOGOUT_EVENT } from '@/lib/api/auth'
import { createBookClient } from './index'

const BookClientContext = createContext<BookClient | null>(null)

export function BookClientProvider({ client: provided, children }: { client?: BookClient; children: React.ReactNode }) {
  // The client is created in the effect, not during render: the WebSocket client connects in its
  // constructor, and Strict Mode's mount → cleanup → mount would otherwise dispose the instance the
  // tree keeps using (start_page then goes nowhere and narration hangs). Each mount owns its client.
  const [owned, setOwned] = useState<BookClient | null>(null)

  useEffect(() => {
    if (provided) return
    const client = createBookClient()
    setOwned(client)
    // Logout closes the socket right away (the page reloads after cleanup).
    const onLogout = () => client.dispose()
    window.addEventListener(LOGOUT_EVENT, onLogout)
    return () => {
      window.removeEventListener(LOGOUT_EVENT, onLogout)
      client.dispose()
      setOwned((current) => (current === client ? null : current))
    }
  }, [provided])

  const client = provided ?? owned
  // One frame without a client on first mount; children all expect a live one.
  if (!client) return null
  return <BookClientContext.Provider value={client}>{children}</BookClientContext.Provider>
}

export function useBookClient(): BookClient {
  const client = useContext(BookClientContext)
  if (!client) throw new Error('useBookClient must be used inside <BookClientProvider>.')
  return client
}

/** Subscribes to one server event type; the handler always sees the latest render's values. */
export function useBookEvent<T extends ServerEventType>(type: T, handler: (event: ServerEventOf<T>) => void) {
  const client = useBookClient()
  const onEvent = useEffectEvent(handler)
  useEffect(() => client.on(type, (event) => onEvent(event)), [client, type])
}
