'use client'

import { authorizedFetch } from '@/lib/api/auth'
import { isApiConfigured } from '@/lib/api/client'
import { ACT_THRESHOLD, type IntentMatch, isConfident, matchIntent, sanitizeIntent } from './matcher'

/** How long an ambiguous utterance may wait for the backend classifier. */
const BACKEND_TIMEOUT_MS = 2500
/** Backend answers below this are treated as a question for the tutor. */
const BACKEND_MIN_CONFIDENCE = 0.6

export interface ResolveOptions {
  lang: 'tanglish' | 'english'
  pageTitle?: string
  signal?: AbortSignal
}

/**
 * Local matcher first; only an ambiguous utterance goes to `POST /api/voice/intent`
 * (LLM classifier, short timeout). Without a backend — or if it is slow, rate
 * limited or unsure — a fairly likely command (≥ 0.6) still acts and anything
 * else goes to the tutor as a question.
 */
export async function resolveIntent(text: string, options: ResolveOptions): Promise<IntentMatch> {
  const local = matchIntent(text)
  if (isConfident(local)) return local
  const fallback = (): IntentMatch =>
    local.intent !== 'question' && local.confidence >= BACKEND_MIN_CONFIDENCE
      ? { ...local, source: 'fallback' }
      : { intent: 'question', args: {}, confidence: 1 - Math.min(local.confidence, ACT_THRESHOLD), normalized: local.normalized, source: 'fallback' }
  if (!isApiConfigured()) return fallback()

  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), BACKEND_TIMEOUT_MS)
  const onAbort = () => controller.abort()
  options.signal?.addEventListener('abort', onAbort, { once: true })
  try {
    const res = await authorizedFetch('/api/voice/intent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text: text.slice(0, 300),
        lang: options.lang,
        page_title: options.pageTitle?.slice(0, 120),
        local_intent: local.intent,
        local_confidence: local.confidence,
      }),
      signal: controller.signal,
    })
    if (!res.ok) return fallback()
    const body = (await res.json()) as { intent?: unknown; args?: unknown; confidence?: unknown }
    const clean = sanitizeIntent(body.intent, body.args)
    const confidence = typeof body.confidence === 'number' ? Math.max(0, Math.min(1, body.confidence)) : 0
    if (!clean) return fallback()
    if (clean.intent !== 'question' && confidence < BACKEND_MIN_CONFIDENCE) return fallback()
    return { intent: clean.intent, args: clean.args, confidence, normalized: local.normalized, source: 'backend' }
  } catch {
    return fallback()
  } finally {
    window.clearTimeout(timer)
    options.signal?.removeEventListener('abort', onAbort)
  }
}
