'use client'

import { AlertCircle, Loader2, Mic, Play, RefreshCw, Square } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { BookeyBadge } from '@/components/ui-kit/bookey-badge'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { FormSection } from '@/components/workspace/form-controls'
import { AUDITION_LINES, listVoices, previewVoice, speakWithBrowser, voiceApiAvailable, type AuditionLang, type TutorVoice } from '@/lib/admin/voice-audition'
import { cn } from '@/lib/utils'
import { textareaClass } from '../admin-ui'

type Playing = { key: string; state: 'loading' | 'playing' } | null

export function VoiceAuditionCard() {
  const [voices, setVoices] = useState<TutorVoice[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'offline' | 'error'>('loading')
  const [message, setMessage] = useState('')
  const [lang, setLang] = useState<AuditionLang>('tanglish')
  const [text, setText] = useState<Record<AuditionLang, string>>(AUDITION_LINES)
  const [playing, setPlaying] = useState<Playing>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const objectUrl = useRef<string | null>(null)
  const abort = useRef<AbortController | null>(null)

  const load = useCallback(() => {
    if (!voiceApiAvailable()) {
      setStatus('offline')
      return
    }
    setStatus('loading')
    const ctl = new AbortController()
    listVoices(ctl.signal)
      .then((v) => {
        setVoices(v)
        setStatus('ready')
      })
      .catch((e: Error) => {
        if (ctl.signal.aborted) return
        setMessage(e.message.includes('fetch') ? 'Voice service is unreachable.' : e.message)
        setStatus('error')
      })
    return () => ctl.abort()
  }, [])

  useEffect(() => load(), [load])

  const stop = useCallback(() => {
    abort.current?.abort()
    audio.current?.pause()
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel()
    setPlaying(null)
  }, [])

  useEffect(
    () => () => {
      stop()
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
    },
    [stop],
  )

  const play = async (voice: TutorVoice | null) => {
    const key = voice?.id ?? 'browser'
    if (playing?.key === key) return stop()
    stop()
    if (!voice) {
      const u = speakWithBrowser(text[lang], lang)
      if (!u) {
        setMessage('This browser has no speech synthesis.')
        return
      }
      setPlaying({ key, state: 'playing' })
      u.onend = () => setPlaying(null)
      return
    }
    const ctl = new AbortController()
    abort.current = ctl
    setPlaying({ key, state: 'loading' })
    setMessage('')
    try {
      const blob = await previewVoice(text[lang], lang, voice.id, ctl.signal)
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
      objectUrl.current = URL.createObjectURL(blob)
      const el = audio.current ?? new Audio()
      audio.current = el
      el.src = objectUrl.current
      el.onended = () => setPlaying(null)
      await el.play()
      setPlaying({ key, state: 'playing' })
    } catch (e) {
      if (ctl.signal.aborted) return
      setPlaying(null)
      setMessage((e as Error).message)
    }
  }

  const shown = voices.filter((v) => v.languages.includes(lang))

  return (
    <FormSection id="tutor-audition" title="Voice audition" icon={<Mic aria-hidden="true" />}>
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div role="radiogroup" aria-label="Audition language" className="flex rounded-full border border-border p-1 text-sm">
            {(['tanglish', 'english'] as const).map((l) => (
              <button
                key={l}
                type="button"
                role="radio"
                aria-checked={lang === l}
                onClick={() => {
                  stop()
                  setLang(l)
                }}
                className={cn(
                  'rounded-full px-3.5 py-1 capitalize outline-none focus-visible:ring-4 focus-visible:ring-ring/30',
                  lang === l ? 'bg-secondary font-medium text-secondary-foreground' : 'text-muted-foreground',
                )}
              >
                {l}
              </button>
            ))}
          </div>
          {status === 'ready' && <BookeyBadge tone="success">Live voices</BookeyBadge>}
          {(status === 'offline' || status === 'error') && <BookeyBadge tone="warning">Browser preview</BookeyBadge>}
        </div>

        <div className="space-y-1">
          <label htmlFor="audition-text" className="text-sm font-medium">
            Sample line
          </label>
          <textarea id="audition-text" rows={3} value={text[lang]} onChange={(e) => setText({ ...text, [lang]: e.target.value })} className={textareaClass} />
        </div>

        {status === 'loading' && (
          <p className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 aria-hidden="true" className="size-4 motion-safe:animate-spin" /> Loading voices…
          </p>
        )}

        {status === 'ready' && (
          <ul className="space-y-2">
            {shown.length === 0 && <li className="text-sm text-muted-foreground">No female {lang} voices returned by the API.</li>}
            {shown.map((v) => {
              const p = playing?.key === v.id ? playing.state : null
              return (
                <li key={v.id} className={cn('flex items-center gap-3 rounded-xl border p-3', p ? 'border-primary bg-secondary' : 'border-border')}>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">{v.label}</p>
                    <p className="truncate text-xs text-muted-foreground">{v.description ?? v.id}</p>
                  </div>
                  <BookeyButton type="button" size="sm" variant={p ? 'primary' : 'outline'} onClick={() => play(v)} aria-pressed={!!p}>
                    {p === 'loading' ? <Loader2 aria-hidden="true" className="motion-safe:animate-spin" /> : p === 'playing' ? <Square aria-hidden="true" /> : <Play aria-hidden="true" />}
                    {p === 'playing' ? 'Stop' : 'Play'}
                    <span className="sr-only"> {v.label}</span>
                  </BookeyButton>
                </li>
              )
            })}
          </ul>
        )}

        {(status === 'offline' || status === 'error') && (
          <div className="space-y-3">
            <p className="flex items-start gap-2 text-sm text-muted-foreground">
              <AlertCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {status === 'offline' ? 'No voice API configured (NEXT_PUBLIC_BOOKEY_API_URL).' : message} You can still hear a rough browser-voice preview — it is not the tutor’s real voice.
            </p>
            <div className="flex flex-wrap gap-2">
              <BookeyButton type="button" size="sm" variant="outline" onClick={() => play(null)}>
                {playing?.key === 'browser' ? <Square aria-hidden="true" /> : <Play aria-hidden="true" />}
                {playing?.key === 'browser' ? 'Stop' : 'Play browser preview'}
              </BookeyButton>
              {status === 'error' && (
                <BookeyButton type="button" size="sm" variant="ghost" onClick={load}>
                  <RefreshCw aria-hidden="true" /> Retry
                </BookeyButton>
              )}
            </div>
          </div>
        )}
        {status === 'ready' && message && (
          <p role="alert" className="text-xs text-destructive">
            {message}
          </p>
        )}
      </div>
    </FormSection>
  )
}
