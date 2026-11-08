import { BookMarked, Compass, Home, Settings, TrendingUp, type LucideIcon } from 'lucide-react'

export interface WorkspaceNavItem {
  href: string
  label: string
  icon: LucideIcon
  /** Shown in the compact mobile tab bar. */
  tab?: boolean
  /** Other routes that belong to this place (Create a book lives under My books). */
  also?: string[]
}

/** Five places, all one tap away on phones. Search lives in the header; Create is a button in My books. */
export const WORKSPACE_NAV: WorkspaceNavItem[] = [
  { href: '/dashboard', label: 'Home', icon: Home, tab: true },
  { href: '/library', label: 'My books', icon: BookMarked, tab: true, also: ['/create'] },
  { href: '/courses', label: 'Explore', icon: Compass, tab: true },
  { href: '/progress', label: 'Progress', icon: TrendingUp, tab: true },
  { href: '/settings', label: 'Settings', icon: Settings, tab: true },
]

const EXTRA_TITLES: Record<string, string> = {
  '/notifications': 'Notifications',
  '/search': 'Search',
  '/create': 'Create a book',
  '/profile': 'Settings',
}

const matches = (pathname: string, href: string) => pathname === href || pathname.startsWith(`${href}/`)

export function isNavActive(pathname: string, href: string) {
  const item = WORKSPACE_NAV.find((entry) => entry.href === href)
  return matches(pathname, href) || Boolean(item?.also?.some((extra) => matches(pathname, extra)))
}

export function getWorkspaceTitle(pathname: string) {
  if (EXTRA_TITLES[pathname]) return EXTRA_TITLES[pathname]
  const match = WORKSPACE_NAV.find((item) => isNavActive(pathname, item.href))
  return match?.label ?? EXTRA_TITLES[pathname] ?? 'Workspace'
}
