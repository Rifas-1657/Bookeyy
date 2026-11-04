import { apiJson, isBackendConfigured } from '@/lib/api/auth'
import { useReaderPreferences } from '@/lib/stores/reader-preferences'

const warmed = new Set<string>()

/** Asks the server to prepare a page's first sentences, so Play starts without waiting. */
export function warmNarration(courseId: string, pageId: string) {
  if (!isBackendConfigured() || courseId === 'python') return
  const gender = useReaderPreferences.getState().voiceGender
  const key = `${courseId}/${pageId}/${gender}`
  if (warmed.has(key)) return
  warmed.add(key)
  apiJson('/api/voice/warm', { method: 'POST', body: JSON.stringify({ courseId, pageId, gender }) }).catch(() => warmed.delete(key))
}
