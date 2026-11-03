'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { LOGOUT_EVENT } from '@/lib/api/auth'
import { isApiConfigured, transcribeAudio } from '@/lib/api/client'

export type HandsFreeState = 'off' | 'starting' | 'listening' | 'hearing' | 'transcribing' | 'unsupported' | 'error'

interface RecognitionLike {
  lang: string
  interimResults: boolean
  continuous: boolean
  onresult: ((event: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null
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

export interface UseHandsFreeOptions {
  enabled: boolean
  /** Push-to-talk / typing is using the mic or the learner is busy: ignore audio meanwhile. */
  paused: boolean
  /** True while our own voice (narration, an answer or an ack) is playing. */
  isPlaybackActive: () => boolean
  /** Speech energy detected: duck the narration. */
  onSpeechStart: () => void
  /** A segment ended without usable speech: undo the duck. */
  onSpeechCancel: () => void
  /** Transcribed speech (not yet echo-checked; the caller decides). */
  onUtterance: (text: string) => void
  /** The mic could not be opened, or the learner logged out / left the page. */
  onStopped: (reason: 'error' | 'logout' | 'pagehide') => void
}

const FRAME_MS = 50
/** Consecutive loud frames before we call it speech (150 ms). */
const ONSET_FRAMES = 3
/** Quiet frames that end a segment (700 ms). */
const OFFSET_FRAMES = 14
/** Segments with less voiced audio than this are clicks/coughs (300 ms). */
const MIN_VOICED_FRAMES = 6
/** Hard cap on one segment (12 s). */
const MAX_SEGMENT_FRAMES = 240
/** Restart the idle recorder this often so segments carry little leading silence (8 s). */
const IDLE_RESTART_FRAMES = 160

/**
 * Hands-free listening (wake-word free).
 *
 * Opens the mic with the browser's echo cancellation and runs an energy VAD
 * (RMS against an adaptive noise floor). While our own audio plays the
 * threshold is raised (echo guard), so leaked narration rarely registers.
 * - Backend configured: a MediaRecorder runs between utterances; when a speech
 *   segment ends, the clip goes to POST /api/voice/stt (Whisper).
 * - No backend: the browser's continuous SpeechRecognition provides text, and the
 *   VAD is used only for ducking.
 * Everything (tracks, AudioContext, recorder, recognition) is released when
 * `enabled` turns false, on unmount, on logout and on pagehide.
 */
export function useHandsFree(options: UseHandsFreeOptions) {
  const { enabled } = options
  const [state, setState] = useState<HandsFreeState>('off')
  const [error, setError] = useState<string | null>(null)
  const opts = useRef(options)
  opts.current = options
  const levelRef = useRef(0)

  useEffect(() => {
    if (!enabled) {
      setState('off')
      return
    }
    let disposed = false
    let media: MediaStream | null = null
    let ctx: AudioContext | null = null
    let timer = 0
    let recorder: MediaRecorder | null = null
    let chunks: Blob[] = []
    let recognition: RecognitionLike | null = null
    let recognitionActive = false
    const useBackend = isApiConfigured() && typeof MediaRecorder !== 'undefined'

    const startRecorder = () => {
      if (!useBackend || !media || disposed) return
      try {
        const rec = new MediaRecorder(media)
        chunks = []
        rec.ondataavailable = (e) => {
          if (e.data.size > 0) chunks.push(e.data)
        }
        rec.start(250)
        recorder = rec
      } catch {
        recorder = null
      }
    }
    const dropRecorder = () => {
      const rec = recorder
      recorder = null
      chunks = []
      if (rec && rec.state !== 'inactive') {
        rec.ondataavailable = null
        rec.onstop = null
        try {
          rec.stop()
        } catch {
          /* already stopped */
        }
      }
    }
    /** Stops the current recorder and hands its clip over; a fresh one starts immediately. */
    const finishRecorder = (): Promise<Blob | null> => {
      const rec = recorder
      if (!rec || rec.state === 'inactive') return Promise.resolve(null)
      recorder = null
      const mine = chunks
      chunks = []
      return new Promise((resolve) => {
        rec.onstop = () => resolve(mine.length ? new Blob(mine, { type: rec.mimeType || 'audio/webm' }) : null)
        try {
          rec.stop()
        } catch {
          resolve(null)
        }
        startRecorder()
      })
    }

    const stopAll = () => {
      disposed = true
      window.clearInterval(timer)
      dropRecorder()
      if (recognition) {
        recognition.onend = null
        recognition.onresult = null
        try {
          recognition.abort()
        } catch {
          /* ignore */
        }
        recognition = null
      }
      media?.getTracks().forEach((t) => t.stop())
      media = null
      void ctx?.close().catch(() => {})
      ctx = null
      levelRef.current = 0
    }

    const startRecognition = (Recognition: new () => RecognitionLike) => {
      const rec = new Recognition()
      rec.lang = 'en-IN'
      rec.continuous = true
      rec.interimResults = false
      rec.onresult = (event) => {
        for (let i = event.resultIndex; i < event.results.length; i += 1) {
          const result = event.results[i]
          if (!result.isFinal) continue
          const text = result[0].transcript.trim()
          if (text && !opts.current.paused) opts.current.onUtterance(text)
        }
      }
      rec.onerror = (event) => {
        if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
          setError('Microphone permission was denied.')
          setState('error')
          stopAll()
          opts.current.onStopped('error')
        }
      }
      // Chrome ends continuous recognition after a while; keep it going while enabled.
      // While paused (push-to-talk owns recognition) it stays stopped; the VAD tick restarts it.
      rec.onend = () => {
        recognitionActive = false
        if (disposed || opts.current.paused) return
        window.setTimeout(() => {
          if (disposed || recognition !== rec || recognitionActive || opts.current.paused) return
          try {
            rec.start()
            recognitionActive = true
          } catch {
            /* the VAD tick retries */
          }
        }, 250)
      }
      recognition = rec
      rec.start()
      recognitionActive = true
    }

    setState('starting')
    setError(null)
    void (async () => {
      const Recognition = getRecognition()
      if (!navigator.mediaDevices?.getUserMedia || (!useBackend && !Recognition)) {
        setState('unsupported')
        setError('Hands-free needs microphone support in this browser.')
        opts.current.onStopped('error')
        return
      }
      try {
        media = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        })
      } catch {
        if (disposed) return
        setError('Microphone permission was denied.')
        setState('error')
        opts.current.onStopped('error')
        return
      }
      if (disposed) {
        media.getTracks().forEach((t) => t.stop())
        media = null
        return
      }
      const AudioCtx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      ctx = new AudioCtx()
      const source = ctx.createMediaStreamSource(media)
      const analyser = ctx.createAnalyser()
      analyser.fftSize = 1024
      source.connect(analyser)
      const buffer = new Float32Array(analyser.fftSize)

      if (useBackend) startRecorder()
      else if (Recognition) {
        try {
          startRecognition(Recognition)
        } catch {
          setState('unsupported')
          stopAll()
          opts.current.onStopped('error')
          return
        }
      }
      setState('listening')

      let noiseFloor = 0.006
      let loud = 0
      let quiet = 0
      let inSpeech = false
      let voiced = 0
      let segmentFrames = 0
      let idleFrames = 0
      let transcribing = false

      const endSegment = (usable: boolean) => {
        inSpeech = false
        loud = 0
        quiet = 0
        segmentFrames = 0
        idleFrames = 0
        if (!usable) {
          opts.current.onSpeechCancel()
          if (useBackend) {
            dropRecorder()
            startRecorder()
          }
          setState('listening')
          return
        }
        if (!useBackend) {
          // SpeechRecognition delivers the text itself; just release the duck if nothing arrives.
          window.setTimeout(() => !disposed && opts.current.onSpeechCancel(), 2500)
          setState('listening')
          return
        }
        transcribing = true
        setState('transcribing')
        void finishRecorder().then(async (blob) => {
          try {
            if (!blob || disposed) {
              opts.current.onSpeechCancel()
              return
            }
            const res = await transcribeAudio(blob)
            if (disposed) return
            const text = res.transcript?.trim() ?? ''
            if (text) opts.current.onUtterance(text)
            else opts.current.onSpeechCancel()
          } catch {
            if (!disposed) opts.current.onSpeechCancel()
          } finally {
            transcribing = false
            if (!disposed) setState('listening')
          }
        })
      }

      timer = window.setInterval(() => {
        if (disposed) return
        analyser.getFloatTimeDomainData(buffer)
        let sum = 0
        for (let i = 0; i < buffer.length; i += 1) sum += buffer[i] * buffer[i]
        const rms = Math.sqrt(sum / buffer.length)
        levelRef.current = rms
        const { paused, isPlaybackActive } = opts.current
        if (recognition) {
          if (paused && recognitionActive) {
            recognitionActive = false
            try {
              recognition.abort()
            } catch {
              /* ignore */
            }
          } else if (!paused && !recognitionActive) {
            try {
              recognition.start()
              recognitionActive = true
            } catch {
              /* still ending; next tick */
            }
          }
        }
        if (paused || transcribing) {
          if (inSpeech) endSegment(false)
          return
        }
        const playing = isPlaybackActive()
        // Echo guard: our own voice leaks through even with AEC, so demand more while it plays.
        const threshold = playing ? Math.max(0.045, noiseFloor * 5) : Math.max(0.018, noiseFloor * 3)
        if (!inSpeech) {
          if (rms > threshold) {
            loud += 1
            if (loud >= ONSET_FRAMES) {
              inSpeech = true
              voiced = loud
              segmentFrames = loud
              quiet = 0
              setState('hearing')
              opts.current.onSpeechStart()
            }
          } else {
            loud = 0
            if (!playing) noiseFloor = Math.min(0.03, noiseFloor * 0.95 + rms * 0.05)
            idleFrames += 1
            if (useBackend && idleFrames >= IDLE_RESTART_FRAMES) {
              idleFrames = 0
              dropRecorder()
              startRecorder()
            }
          }
          return
        }
        segmentFrames += 1
        if (rms > threshold * 0.7) {
          voiced += 1
          quiet = 0
        } else {
          quiet += 1
        }
        if (quiet >= OFFSET_FRAMES || segmentFrames >= MAX_SEGMENT_FRAMES) endSegment(voiced >= MIN_VOICED_FRAMES)
      }, FRAME_MS)
    })()

    const onLogout = () => {
      stopAll()
      opts.current.onStopped('logout')
    }
    const onPageHide = () => {
      stopAll()
      opts.current.onStopped('pagehide')
    }
    window.addEventListener(LOGOUT_EVENT, onLogout)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      window.removeEventListener(LOGOUT_EVENT, onLogout)
      window.removeEventListener('pagehide', onPageHide)
      stopAll()
    }
  }, [enabled])

  const getLevel = useCallback(() => Math.min(1, levelRef.current * 8), [])
  return { state: enabled ? state : ('off' as HandsFreeState), error, getLevel }
}
