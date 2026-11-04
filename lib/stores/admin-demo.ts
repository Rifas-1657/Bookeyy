'use client'

import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { defaultTutorSettings, seedCourses, seedDocuments, seedMembers } from '@/lib/admin/seed'
import type { AdminCourse, AdminDocument, DemoMember, TutorSettings } from '@/lib/admin/types'
import { newId, uniqueSlug } from '@/lib/admin/utils'
import { apiJson, isBackendConfigured } from '@/lib/api/auth'
import { safeStorage } from '@/lib/storage'

interface AdminDemoState {
  hydrated: boolean
  /** Demo navigation state only — this is not authentication or authorization. */
  demoAdmin: boolean
  sidebarCompact: boolean
  courses: AdminCourse[]
  documents: AdminDocument[]
  members: DemoMember[]
  tutor: TutorSettings
  setDemoAdmin: (value: boolean) => void
  setSidebarCompact: (value: boolean) => void
  saveCourse: (course: AdminCourse) => void
  duplicateCourse: (id: string, updatedAt: string) => AdminCourse | null
  togglePublish: (id: string, updatedAt: string) => void
  addDocuments: (documents: AdminDocument[]) => void
  updateDocument: (id: string, patch: Partial<AdminDocument>) => void
  removeDocument: (id: string) => void
  startProcessing: (id: string) => void
  advanceProcessing: (step: number) => void
  inviteMember: (member: DemoMember) => void
  removeMember: (id: string) => void
  saveTutor: (settings: TutorSettings) => void
  resetDemoData: () => void
  overview: AdminOverview | null
  /** Replaces members, courses and figures with what the server holds. */
  loadLive: () => Promise<void>
}

export interface AdminOverview {
  learners: number
  joinedThisWeek: number
  books: number
  pagesRead: number
}

interface ServerMember {
  id: string
  email: string
  display_name: string
  role: string
  is_active: boolean
  created_at: string
}

interface ServerCourse {
  id: string
  title: string
  description: string
  language: string
  level: string
  available: boolean
  category: string
  cover: { base?: string }
  chapter_count: number
  created_at: string
}

const seedState = () => ({
  courses: seedCourses,
  documents: seedDocuments,
  members: seedMembers,
  tutor: defaultTutorSettings,
})

export const useAdminDemo = create<AdminDemoState>()(
  persist(
    (set, get) => ({
      hydrated: false,
      overview: null,
      loadLive: async () => {
        if (!isBackendConfigured()) return
        const [members, courses, overview] = await Promise.all([
          apiJson<ServerMember[]>('/admin/members'),
          apiJson<ServerCourse[]>('/admin/courses'),
          apiJson<AdminOverview>('/admin/members/overview'),
        ])
        set({
          overview,
          members: members
            .filter((m) => m.is_active)
            .map((m) => ({
              id: m.id,
              name: m.display_name || m.email,
              email: m.email,
              courseIds: [],
              lastActiveAt: m.created_at,
              status: 'active' as const,
            })),
          courses: courses
            .filter((c) => c.category !== 'My books')
            .map((c) => ({
              id: c.id,
              sourceId: c.id,
              title: c.title,
              slug: c.id,
              description: c.description,
              language: c.language,
              level: c.level as AdminCourse['level'],
              coverColor: c.cover?.base ?? '#2A2117',
              chapters: [],
              status: c.available ? ('published' as const) : ('draft' as const),
              updatedAt: c.created_at,
            })),
        })
      },
      demoAdmin: false,
      sidebarCompact: false,
      ...seedState(),
      setDemoAdmin: (demoAdmin) => set({ demoAdmin }),
      setSidebarCompact: (sidebarCompact) => set({ sidebarCompact }),
      saveCourse: (course) =>
        set((state) => {
          const exists = state.courses.some((c) => c.id === course.id)
          return {
            courses: exists ? state.courses.map((c) => (c.id === course.id ? course : c)) : [course, ...state.courses],
          }
        }),
      duplicateCourse: (id, updatedAt) => {
        const source = get().courses.find((c) => c.id === id)
        if (!source) return null
        const copyId = newId('course')
        const copy: AdminCourse = {
          ...source,
          id: copyId,
          sourceId: null,
          title: `${source.title} (copy)`,
          slug: uniqueSlug(`${source.slug}-copy`, get().courses.map((c) => c.slug)),
          status: 'draft',
          updatedAt,
          chapters: source.chapters.map((chapter) => ({ ...chapter, id: newId('ch') })),
        }
        set((state) => ({ courses: [copy, ...state.courses] }))
        return copy
      },
      togglePublish: (id, updatedAt) => {
        if (isBackendConfigured()) {
          const course = get().courses.find((c) => c.id === id)
          const action = course?.status === 'published' ? 'unpublish' : 'publish'
          void apiJson(`/admin/courses/${encodeURIComponent(id)}/${action}`, { method: 'POST' }).then(() => get().loadLive())
          return
        }
        set((state) => ({
          courses: state.courses.map((c) =>
            c.id === id ? { ...c, status: c.status === 'published' ? 'draft' : 'published', updatedAt } : c,
          ),
        }))
      },
      addDocuments: (documents) => set((state) => ({ documents: [...documents, ...state.documents] })),
      updateDocument: (id, patch) =>
        set((state) => ({ documents: state.documents.map((d) => (d.id === id ? { ...d, ...patch } : d)) })),
      removeDocument: (id) => set((state) => ({ documents: state.documents.filter((d) => d.id !== id) })),
      startProcessing: (id) =>
        set((state) => ({
          documents: state.documents.map((d) => (d.id === id && d.stage === 'selected' ? { ...d, stage: 'processing', progress: 0 } : d)),
        })),
      advanceProcessing: (step) =>
        set((state) => ({
          documents: state.documents.map((d) => {
            if (d.stage !== 'processing') return d
            const progress = Math.min(100, d.progress + step)
            return { ...d, progress, stage: progress >= 100 ? 'ready' : 'processing' }
          }),
        })),
      inviteMember: (member) => set((state) => ({ members: [...state.members, member] })),
      removeMember: (id) => set((state) => ({ members: state.members.filter((m) => m.id !== id) })),
      saveTutor: (tutor) => set({ tutor }),
      resetDemoData: () => set(seedState()),
    }),
    {
      name: 'admin:demo',
      version: 1,
      storage: createJSONStorage(() => safeStorage),
      skipHydration: true,
      partialize: ({ demoAdmin, sidebarCompact, courses, documents, members, tutor }) => ({
        demoAdmin,
        sidebarCompact,
        courses,
        documents,
        members,
        tutor,
      }),
      onRehydrateStorage: () => () => {
        useAdminDemo.setState({ hydrated: true })
      },
    },
  ),
)

export function rehydrateAdminDemo() {
  if (useAdminDemo.getState().hydrated) return
  void useAdminDemo.persist.rehydrate()
}
