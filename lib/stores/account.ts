'use client'

import { isBackendConfigured, type Account } from '@/lib/api/auth'
import { createRemoteResource, useRemote } from './remote'

const LIVE = isBackendConfigured()

/** The signed-in account (`/auth/me`): name, email, and whether the email is confirmed. */
export const remoteAccount = createRemoteResource<Account | null>('/auth/me', null, (raw) => raw as Account)

export function useAccount() {
  return useRemote(remoteAccount, LIVE)
}
