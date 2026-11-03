'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { isApiConfigured, transcribeAudio } from '@/lib/api/client'

export type VoiceQuestionState = 'idle' | 'listening' | 'transcribing' | 'unsupported' | 'error'

interface RecognitionLike {
  lang: string
  interimResults: boolean
  continuous: boolean
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}

function getRecognition(): (new () => RecognitionLike) | null {
  if (typeof window === 'undefined') return null
  const w = window as unknown as { SpeechRecognition?: new () => RecognitionLike; webkitSpeechRecognition?: new () => RecognitionLike }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null
}

/**
 * Push-to-talk question capture.
 * - Backend configured: MediaRecorder → POST /api/voice/stt (Whisper handles Tanglish).
 * - No backend: the browser's SpeechRecognition (Chrome/Edge/Safari), en-IN.
 * - Neither: `unsupported`, and the UI offers typing instead.
 */
export function useVoiceQuestion() {
  const [state, setState] = useState<VoiceQuestionState>('idle')
  const [interim, setInterim] = useState('')
  const [error, setError] = useState<string | null>(null)
  const recognition = useRef<RecognitionLike | null>(null)
  const finalText = useRef('')
  const recorder = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const stream = useRef<MediaStream | null>(null)
  const pendingDone = useRef<((text: string) => void) | null>(null)
  /** Bumped by cancel/unmount/new start, so a slow getUserMedia can tell it was abandoned. */
  const generation = useRef(0)

  const cleanup = useCallback(() => {
    stream.current?.getTracks().forEach((t) => t.stop())
    stream.current = null
    recorder.current = null
    chunks.current = []
  }, [])

  useEffect(
    () => () => {
      generation.current += 1
      recognition.current?.abort()
      cleanup()
    },
    [cleanup],
  )

  const start = useCallback(async () => {
    generation.current += 1
    const token = generation.current
    setError(null)
    setInterim('')
    finalText.current = ''
    if (isApiConfigured() && typeof MediaRecorder !== 'undefined' && navigator.mediaDevices?.getUserMedia) {
      try {
        const media = await navigator.mediaDevices.getUserMedia({ audio: true })
        if (token !== generation.current) {
          // Cancelled or unmounted while the permission prompt was open: release the mic.
          media.getTracks().forEach((t) => t.stop())
          return false
        }
        stream.current = media
        const rec = new MediaRecorder(media)
        recorder.current = rec
        chunks.current = []
        rec.ondataavailable = (e) => e.data.size > 0 && chunks.current.push(e.data)
        rec.start(250)
        setState('listening')
        return true
      } catch {
        if (token !== generation.current) return false
        cleanup()
        setError('Microphone permission was denied.')
        setState('error')
        return false
      }
    }
    const Recognition = getRecognition()
    if (!Recognition) {
      setState('unsupported')
      return false
    }
    try {
      const rec = new Recognition()
      rec.lang = 'en-IN'
      rec.interimResults = true
      rec.continuous = true
      rec.onresult = (event) => {
        let text = ''
        let finals = ''
        for (let i = 0; i < event.results.length; i += 1) {
          const result = event.results[i]
          if (result.isFinal) finals += result[0].transcript
          else text += result[0].transcript
        }
        finalText.current = finals
        setInterim((finals + ' ' + text).trim())
      }
      rec.onerror = (event) => {
        if (event.error === 'not-allowed') setError('Microphone permission was denied.')
        else if (event.error !== 'aborted' && event.error !== 'no-speech') setError('Could not hear that. Try again or type.')
      }
      rec.onend = () => {
        const done = pendingDone.current
        pendingDone.current = null
        recognition.current = null
        setState('idle')
        done?.(finalText.current.trim() || '')
      }
      recognition.current = rec
      rec.start()
      setState('listening')
      return true
    } catch {
      setState('unsupported')
      return false
    }
  }, [cleanup])

  /** Stops listening and hands the transcript to `onText` (empty string when nothing was heard). */
  const stop = useCallback(
    (onText: (text: string) => void) => {
      const rec = recognition.current
      if (rec) {
        const heard = interim
        pendingDone.current = (text) => onText(text || heard)
        rec.stop()
        return
      }
      const media = recorder.current
      if (!media) {
        onText('')
        return
      }
      setState('transcribing')
      media.onstop = async () => {
        const blob = new Blob(chunks.current, { type: media.mimeType || 'audio/webm' })
        cleanup()
        try {
          const res = await transcribeAudio(blob)
          setState('idle')
          onText(res.transcript?.trim() ?? '')
        } catch {
          setError('Speech transcription failed. You can type instead.')
          setState('error')
          onText('')
        }
      }
      media.stop()
    },
    [cleanup, interim],
  )

  const cancel = useCallback(() => {
    generation.current += 1
    pendingDone.current = null
    recognition.current?.abort()
    recognition.current = null
    if (recorder.current && recorder.current.state !== 'inactive') {
      recorder.current.onstop = null
      recorder.current.stop()
    }
    cleanup()
    setInterim('')
    setState('idle')
  }, [cleanup])

  return { state, interim, error, start, stop, cancel }
}
