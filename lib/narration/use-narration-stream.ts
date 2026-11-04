'use client'

import { useEffect, useEffectEvent, useMemo, useReducer, useRef, useState } from 'react'
import { useBookClient, useBookEvent } from '@/lib/book-client/react'
import { segmentIdFor, type TutorLanguage } from '@/lib/contracts/book-events'
import { getPageAudio } from '@/lib/offline/db'
import { useReaderPreferences } from '@/lib/stores/reader-preferences'
import type { BookLessonPage, NarrationCueEvent, NarrationStatus } from '@/types/book'
import { NarrationEngine, type EngineState } from './engine'
import { getPrebuiltAudioUrl, loadPrebuiltTimings } from './python-demo-audio'
import { synthesize, ttsAvailable } from './tts'
import { buildNarrationCues } from './cues'
import { tokenizeNarration, type NarrationSentence, type NarrationWord } from './tokenize'

const THINKING_MS = 1400

export interface TutorExchange {
  question: string
  phase: 'thinking' | 'answered'
}

export interface NarrationState {
  pageId: string
  wordCount: number
  status: NarrationStatus
  /** Active word; -1 before the first play, `wordCount` once completed. */
  wordIndex: number
  /** Increments on every fresh pass (play from start / restart) so cues can re-fire. */
  pass: number
  tutor: TutorExchange | null
  /** Set by the "Next page" CTA so the following page starts speaking on arrival. */
  autoplay: boolean
}

type NarrationAction =
  | { type: 'reset'; pageId: string; wordCount: number; autoplay: boolean }
  | { type: 'play' }
  | { type: 'pause' }
  | { type: 'word'; wordIndex: number }
  | { type: 'complete' }
  | { type: 'seek'; wordIndex: number }
  | { type: 'restart' }
  | { type: 'stop' }
  | { type: 'listen' }
  | { type: 'stopListening' }
  | { type: 'ask'; question: string }
  | { type: 'answered' }
  | { type: 'dismissTutor' }
  | { type: 'queueAutoplay' }

function initialState(pageId: string, wordCount: number, autoplay = false, pass = 0): NarrationState {
  const start = autoplay && wordCount > 0
  return {
    pageId,
    wordCount,
    status: start ? 'speaking' : 'idle',
    wordIndex: start ? 0 : -1,
    pass: start ? pass + 1 : pass,
    tutor: null,
    autoplay: false,
  }
}

function settledStatus(state: NarrationState): NarrationStatus {
  if (state.wordIndex < 0) return 'idle'
  if (state.wordIndex >= state.wordCount) return 'completed'
  return 'paused'
}

function reducer(state: NarrationState, action: NarrationAction): NarrationState {
  switch (action.type) {
    case 'reset':
      return initialState(action.pageId, action.wordCount, action.autoplay, state.pass)
    case 'play': {
      if (state.wordCount === 0) return state
      const fresh = state.wordIndex < 0 || state.wordIndex >= state.wordCount
      return {
        ...state,
        status: 'speaking',
        tutor: null,
        wordIndex: fresh ? 0 : state.wordIndex,
        pass: fresh ? state.pass + 1 : state.pass,
      }
    }
    case 'pause':
      return state.status === 'speaking' ? { ...state, status: 'paused' } : state
    case 'word': {
      if (state.status !== 'speaking') return state
      return action.wordIndex >= state.wordCount
        ? { ...state, wordIndex: state.wordCount, status: 'completed' }
        : { ...state, wordIndex: Math.max(0, action.wordIndex) }
    }
    case 'complete':
      return state.status === 'speaking' ? { ...state, wordIndex: state.wordCount, status: 'completed' } : state
    case 'seek': {
      if (state.wordIndex < 0) return state
      const wordIndex = Math.min(state.wordCount, Math.max(0, action.wordIndex))
      if (wordIndex >= state.wordCount) return { ...state, wordIndex, status: 'completed' }
      const status = state.status === 'completed' ? 'paused' : state.status
      return { ...state, wordIndex, status }
    }
    case 'restart':
      return state.wordCount === 0
        ? state
        : { ...state, status: 'speaking', wordIndex: 0, pass: state.pass + 1, tutor: null }
    case 'stop':
      return { ...state, status: 'idle', wordIndex: -1, tutor: null }
    case 'listen':
      return { ...state, status: 'listening', tutor: null }
    case 'stopListening':
      return state.status === 'listening' ? { ...state, status: settledStatus(state) } : state
    case 'ask':
      return { ...state, status: 'thinking', tutor: { question: action.question, phase: 'thinking' } }
    case 'answered':
      return state.status === 'thinking' && state.tutor
        ? { ...state, status: settledStatus(state), tutor: { ...state.tutor, phase: 'answered' } }
        : state
    case 'dismissTutor': {
      const status = state.status === 'thinking' ? settledStatus(state) : state.status
      return { ...state, status, tutor: null }
    }
    case 'queueAutoplay':
      return { ...state, autoplay: true }
  }
}

/** Which server session backs the current narration pass. */
interface SessionLink {
  /** `${pageId}:${pass}` the session was requested for, or null when none. */
  key: string | null
  sessionId: string | null
  playing: boolean
}

const NO_LINK: SessionLink = { key: null, sessionId: null, playing: false }

/**
 * Where the page's voice comes from.
 * - `prebuilt`: static demo mp3 (`public/audio/python-demo`)
 * - `offline`: audio saved in IndexedDB by "Download for offline"
 * - `tts`: backend REST TTS, one clip per sentence
 * - `stream`: `audio_chunk` frames from the WebSocket session
 * - `none`: no audio; words follow the server's timed `text_chunk` frames
 */
export type NarrationAudioMode = 'prebuilt' | 'offline' | 'tts' | 'stream' | 'none'

interface UseNarrationStreamOptions {
  courseId: string
  page: BookLessonPage | undefined
  /** False in full-book mode; any in-progress stream is reset. */
  enabled: boolean
  rate: number
  muted: boolean
  language?: TutorLanguage
  onCue?: (cue: NarrationCueEvent) => void
  onComplete?: () => void
}

export interface NarrationStream {
  status: NarrationStatus
  wordIndex: number
  pass: number
  words: NarrationWord[]
  sentences: NarrationSentence[]
  cues: NarrationCueEvent[]
  reachedCues: NarrationCueEvent[]
  currentSentence: NarrationSentence | null
  /** 0..1 progress through the page narration. */
  progress: number
  tutor: TutorExchange | null
  canSeek: boolean
  /** The voice failed for this page (timed text continues); guided mode should show the whole page. */
  audioFailed: boolean
  /** The browser blocked playback until the learner taps Play (autoplay policy). */
  autoplayBlocked: boolean
  /** Compatibility view of the voice source for the dock and dev panel. */
  player: { kind: NarrationAudioMode; producesSound: boolean }
  audioMode: NarrationAudioMode
  /** True while the voice is loading/buffering for the current position. */
  buffering: boolean
  /** Live 0..1 voice amplitude; call from requestAnimationFrame. */
  getLevel: () => number
  play: () => void
  pause: () => void
  toggle: () => void
  restart: () => void
  /** Stops narration and returns to the idle (not started) state. */
  stop: () => void
  previousSentence: () => void
  nextSentence: () => void
  seekToWord: (wordIndex: number) => void
  startListening: () => void
  stopListening: () => void
  ask: (question: string) => void
  dismissTutor: () => void
  queueAutoplay: () => void
  /** Barge-in: fades the voice down and stops to listen. Returns true if narration was speaking. */
  interrupt: () => boolean
  /** Continues narration after an interruption (no-op if it wasn't interrupted). */
  resumeAfterInterrupt: () => void
  /** Lowers/restores the voice without pausing (e.g. while the learner starts speaking). */
  duck: (on: boolean) => void
}

function blobUrl(blob: Blob) {
  return URL.createObjectURL(blob)
}

function base64Blob(base64: string, mime: string) {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return new Blob([bytes], { type: mime })
}

/**
 * Narration state machine for one page.
 *
 * With audio, the NarrationEngine is the clock: the active word comes from the
 * playing audio's time + word timings (rAF), so text reveal, highlights,
 * diagram steps and code cues follow the voice exactly. Without audio, word
 * progress comes from the book client's timed `text_chunk` / `page_complete`
 * frames, and local actions are mirrored as `start_page` / `pause` / `resume`
 * / `seek` / `speed`.
 */
export function useNarrationStream({
  courseId,
  page,
  enabled,
  rate,
  muted,
  language,
  onCue,
  onComplete,
}: UseNarrationStreamOptions): NarrationStream {
  const client = useBookClient()
  const voiceGender = useReaderPreferences((s) => s.voiceGender)
  const pageId = page?.id ?? ''
  const narrationText = page?.narrationText ?? ''
  const { words, sentences } = useMemo(() => tokenizeNarration(narrationText), [narrationText])
  const cues = useMemo(() => buildNarrationCues(page, words.length), [page, words.length])

  const [state, dispatch] = useReducer(reducer, undefined, () => initialState(pageId, words.length))

  // Page change / full-book mode: reset during render so no stale word survives a frame.
  if (state.pageId !== pageId) {
    dispatch({ type: 'reset', pageId, wordCount: words.length, autoplay: state.autoplay && enabled })
  } else if (!enabled && state.wordIndex >= 0) {
    dispatch({ type: 'reset', pageId, wordCount: words.length, autoplay: false })
  }

  const { status, wordIndex, pass } = state
  const sessionKey = `${pageId}:${pass}`
  const wordIndexRef = useRef(wordIndex)
  wordIndexRef.current = wordIndex

  /* ------------------------------ audio engine ------------------------------ */

  const [engine] = useState(
    () =>
      new NarrationEngine({
        onWord: (index) => engineCallbacks.current.onWord(index),
        onState: (next) => engineCallbacks.current.onState(next),
        onEnd: () => engineCallbacks.current.onEnd(),
        onError: (index, error) => engineCallbacks.current.onError(index, error),
        onBlocked: () => engineCallbacks.current.onBlocked(),
      }),
  )
  const [engineState, setEngineState] = useState<EngineState>('idle')
  const [autoplayBlocked, setAutoplayBlocked] = useState(false)
  const [audio, setAudio] = useState<{ pageId: string; mode: NarrationAudioMode; ready: boolean; failed?: boolean }>({
    pageId: '',
    mode: 'none',
    ready: false,
  })
  /** Bumps whenever the engine is (re)configured, so playback and the TTS cursor restart on the new plan. */
  const [engineEpoch, setEngineEpoch] = useState(0)
  const failedPages = useRef(new Set<string>())
  const audioForPage: { pageId: string; mode: NarrationAudioMode; ready: boolean; failed?: boolean } =
    audio.pageId === pageId ? audio : { pageId, mode: 'none', ready: false }
  const audioMode = audioForPage.mode
  const audioDriven = audioMode !== 'none' && audioForPage.ready
  const isSocket = client.kind === 'websocket'
  // The server session paces words when there's no audio, and always on sockets (audio arrives over it).
  const serverDriven = isSocket || !audioDriven

  const engineCallbacks = useRef({
    onWord: (_index: number) => {},
    onState: (_state: EngineState) => {},
    onEnd: () => {},
    onError: (_index: number, _error: unknown) => {},
    onBlocked: () => {},
  })
  engineCallbacks.current = {
    onWord: (index) => dispatch({ type: 'word', wordIndex: index }),
    onState: setEngineState,
    onEnd: () => dispatch({ type: 'complete' }),
    onError: (index, error) => {
      if (process.env.NODE_ENV === 'development') console.warn('[narration] audio failed, using timed text', error)
      // A rate limit is temporary: use timed text now, but try the voice again next visit.
      if (!(error as { retryable?: boolean } | null)?.retryable) failedPages.current.add(pageId)
      setAudio({ pageId, mode: 'none', ready: true, failed: true })
      if (index > wordIndexRef.current) dispatch({ type: 'seek', wordIndex: index })
    },
    onBlocked: () => {
      // The engine paused itself; mirror that so the UI offers Play instead of showing "speaking".
      setAutoplayBlocked(true)
      dispatch({ type: 'pause' })
    },
  }

  useEffect(() => () => engine.reset(), [engine])
  useEffect(() => engine.setRate(rate), [engine, rate])
  useEffect(() => engine.setMuted(muted), [engine, muted])

  // Resolve the voice source for this page (offline store → prebuilt mp3 → backend TTS → none).
  useEffect(() => {
    let cancelled = false
    engine.reset()
    // Any reconfiguration (page, voice, transport) restarts playback from the current word.
    const configured = () => setEngineEpoch((n) => n + 1)
    configured()
    if (!pageId || words.length === 0) return
    if (isSocket) {
      // Prebuilt page audio wins over streamed TTS (the server then only paces text).
      const prebuiltUrl = getPrebuiltAudioUrl(courseId, pageId, voiceGender)
      if (prebuiltUrl) {
        void (async () => {
          const timings = await loadPrebuiltTimings(courseId, pageId, voiceGender)
          if (cancelled) return
          engine.configure(words, [[0, words.length]])
          engine.provide(0, { src: prebuiltUrl, timings })
          setAudio({ pageId, mode: 'prebuilt', ready: true })
          configured()
        })()
        return () => {
          cancelled = true
        }
      }
      engine.configureStreaming()
      setAudio({ pageId, mode: 'none', ready: true })
      return
    }
    if (failedPages.current.has(pageId)) {
      setAudio({ pageId, mode: 'none', ready: true })
      return
    }
    void (async () => {
      const stored = await getPageAudio(courseId, pageId, voiceGender).catch(() => [])
      if (cancelled) return
      const whole = stored.length === 1 && stored[0].segment === 0 && (stored[0].whole !== false || sentences.length <= 1)
      if (whole) {
        engine.configure(words, [[0, words.length]])
        engine.provide(0, { src: blobUrl(stored[0].blob), timings: stored[0].timings, revoke: true })
        setAudio({ pageId, mode: 'offline', ready: true })
        configured()
        return
      }
      if (stored.length > 0) {
        engine.configure(words, sentences.map((s) => [s.start, s.end] as [number, number]))
        for (const entry of stored) {
          engine.provide(entry.segment, { src: blobUrl(entry.blob), timings: entry.timings, revoke: true })
        }
        setAudio({ pageId, mode: 'offline', ready: true })
        configured()
        return
      }
      const prebuilt = getPrebuiltAudioUrl(courseId, pageId, voiceGender)
      if (prebuilt) {
        const timings = await loadPrebuiltTimings(courseId, pageId, voiceGender)
        if (cancelled) return
        engine.configure(words, [[0, words.length]])
        engine.provide(0, { src: prebuilt, timings })
        setAudio({ pageId, mode: 'prebuilt', ready: true })
        configured()
        return
      }
      if (ttsAvailable()) {
        engine.configure(words, sentences.map((s) => [s.start, s.end] as [number, number]))
        setAudio({ pageId, mode: 'tts', ready: true })
        configured()
        return
      }
      setAudio({ pageId, mode: 'none', ready: true })
    })()
    return () => {
      cancelled = true
    }
    // `words`/`sentences` derive from the page; `pageId` covers them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine, courseId, pageId, isSocket, voiceGender])

  // Backend TTS: synthesize sentences in order from the current one while speaking.
  // The cursor is replaced whenever the engine is reconfigured, so an in-flight
  // loop for the old plan stops providing clips.
  const ttsKey = `${pageId}:${engineEpoch}`
  const ttsCursor = useRef<{ key: string; next: number; busy: boolean }>({ key: '', next: 0, busy: false })
  const currentSentenceIndex = wordIndex >= 0 && wordIndex < words.length ? words[wordIndex].sentence : 0
  const currentSentenceRef = useRef(currentSentenceIndex)
  currentSentenceRef.current = currentSentenceIndex
  useEffect(() => {
    if (audioMode !== 'tts' || !audioForPage.ready || status !== 'speaking') return
    let cursor = ttsCursor.current
    if (cursor.key !== ttsKey) {
      cursor = { key: ttsKey, next: currentSentenceIndex, busy: false }
      ttsCursor.current = cursor
    }
    if (currentSentenceIndex > cursor.next) cursor.next = currentSentenceIndex
    if (cursor.busy) return
    cursor.busy = true
    const live = () => ttsCursor.current === cursor
    void (async () => {
      // Keep two sentences of lookahead ahead of the voice.
      while (live() && cursor.next < sentences.length && cursor.next <= currentSentenceRef.current + 2) {
        const index = cursor.next
        try {
          const clip = await synthesize(sentences[index].text, { lang: language, speed: 1 })
          if (!live()) break
          engine.provide(index, { src: blobUrl(clip.blob), timings: clip.timings, revoke: true })
        } catch (error) {
          if (live()) engine.fail(index, error)
          break
        }
        cursor.next = Math.max(cursor.next, index + 1)
      }
      cursor.busy = false
    })()
  }, [audioMode, audioForPage.ready, status, ttsKey, currentSentenceIndex, sentences, engine, language])

  // Drive the engine from the state machine. `engineEpoch` re-runs this after a
  // voice/page reconfigure so speaking resumes from the current word on the new plan.
  useEffect(() => {
    if (!audioDriven) return
    if (status === 'speaking') engine.play(Math.max(0, wordIndexRef.current))
    else engine.pause()
  }, [engine, audioDriven, status, pass, engineEpoch])

  // A fresh page starts un-ducked with no pending interruption or autoplay hint.
  const interrupted = useRef(false)
  useEffect(() => {
    interrupted.current = false
    setAutoplayBlocked(false)
    engine.duck(false)
  }, [engine, pageId])

  /* ---------------------------- client sync ---------------------------- */

  const link = useRef<SessionLink>(NO_LINK)
  const [linkVersion, setLinkVersion] = useState(0)

  const segmentFor = (index: number) => {
    const word = words[Math.min(index, words.length - 1)]
    return segmentIdFor(pageId, word?.sentence ?? 0)
  }

  // Declared before the sync effect so Strict Mode's remount re-requests the session.
  useEffect(() => {
    return () => {
      const current = link.current
      if (current.sessionId && current.playing) client.pause({ sessionId: current.sessionId })
      link.current = NO_LINK
    }
  }, [client])

  useEffect(() => {
    const wantPlaying = status === 'speaking' && pageId !== '' && serverDriven && audioForPage.ready
    const current = link.current

    if (current.key !== sessionKey) {
      if (!wantPlaying) {
        if (current.sessionId && current.playing) {
          client.pause({ sessionId: current.sessionId })
          current.playing = false
        }
        if (current.key && current.key.split(':')[0] !== pageId) link.current = NO_LINK
        return
      }
      if (current.sessionId && current.playing) client.pause({ sessionId: current.sessionId })
      link.current = { key: sessionKey, sessionId: null, playing: false }
      client.startPage({ courseId, pageId, gender: voiceGender })
      return
    }

    // Requested but `session_started` hasn't arrived yet.
    if (!current.sessionId) return

    if (wantPlaying && !current.playing) {
      const target = Math.max(0, wordIndexRef.current)
      client.seek({ sessionId: current.sessionId, segmentId: segmentFor(target), wordIndex: target })
      client.resume({ sessionId: current.sessionId })
      current.playing = true
    } else if (!wantPlaying && current.playing) {
      client.pause({ sessionId: current.sessionId })
      current.playing = false
    }
    // `segmentFor` only reads `words`/`pageId`, both covered by `sessionKey`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, status, sessionKey, linkVersion, courseId, pageId, serverDriven, audioForPage.ready])

  useEffect(() => {
    const sessionId = link.current.sessionId
    if (sessionId) client.speed({ sessionId, value: rate })
  }, [client, rate, linkVersion])

  // Socket reconnected: the old server session is gone. Drop the link so the
  // sync effect re-requests `start_page` (if speaking) and `session_started`
  // seeks to the current word.
  useEffect(() => {
    if (!client.onReconnect) return
    return client.onReconnect(() => {
      if (!link.current.key) return
      link.current = NO_LINK
      setLinkVersion((v) => v + 1)
    })
  }, [client])

  useBookEvent('session_started', ({ payload }) => {
    const current = link.current
    if (current.key !== sessionKey || current.sessionId || payload.pageId !== pageId) return
    // The server starts narrating from word 0 as soon as the session opens.
    link.current = { key: sessionKey, sessionId: payload.sessionId, playing: true }
    if (wordIndexRef.current > 0) {
      client.seek({ sessionId: payload.sessionId, segmentId: segmentFor(wordIndexRef.current), wordIndex: wordIndexRef.current })
    }
    setLinkVersion((v) => v + 1)
  })

  useBookEvent('text_chunk', ({ sessionId, payload }) => {
    if (!sessionId || sessionId !== link.current.sessionId || audioDriven) return
    dispatch({ type: 'word', wordIndex: payload.wordStart })
  })

  // WebSocket audio: each chunk is a playable clip; timings map it onto display words.
  useBookEvent('audio_chunk', ({ sessionId, payload }) => {
    if (!isSocket || audioMode === 'prebuilt' || !sessionId || sessionId !== link.current.sessionId || failedPages.current.has(pageId)) return
    const base64 = payload.base64 || payload.data || ''
    if (!base64) return
    let covered: NarrationWord[] = []
    const indexes = (payload.wordTimings ?? []).map((t) => t.wordIndex).filter((n): n is number => typeof n === 'number')
    if (indexes.length) covered = words.slice(Math.min(...indexes), Math.max(...indexes) + 1)
    if (covered.length === 0) {
      const match = /#s(\d+)$/.exec(payload.segmentId)
      const sentence = match ? sentences[Number(match[1])] : sentences[words[Math.max(0, wordIndexRef.current)]?.sentence ?? 0]
      if (sentence) covered = words.slice(sentence.start, sentence.end)
    }
    if (covered.length === 0) return
    try {
      const src = blobUrl(base64Blob(base64, payload.mime || 'audio/mpeg'))
      engine.append(covered, { src, timings: payload.wordTimings, revoke: true })
      if (audioMode !== 'stream') setAudio({ pageId, mode: 'stream', ready: true })
    } catch {
      /* undecodable chunk: keep timed text */
    }
  })

  useBookEvent('page_complete', ({ sessionId }) => {
    if (!sessionId || sessionId !== link.current.sessionId) return
    link.current = { ...link.current, playing: false }
    if (audioDriven) {
      // The voice decides when the page ends.
      engine.seal()
      return
    }
    dispatch({ type: 'complete' })
  })

  useBookEvent('error', ({ sessionId, payload }) => {
    if (sessionId && sessionId === link.current.sessionId && !payload.recoverable) dispatch({ type: 'pause' })
  })

  /* ------------------------------ local UI ----------------------------- */

  useEffect(() => {
    if (status !== 'thinking') return
    const timer = window.setTimeout(() => dispatch({ type: 'answered' }), THINKING_MS)
    return () => window.clearTimeout(timer)
  }, [status])

  const emitCue = useEffectEvent((cue: NarrationCueEvent) => onCue?.(cue))
  const emitComplete = useEffectEvent(() => onComplete?.())
  const fired = useRef<{ key: string; ids: Set<string> }>({ key: '', ids: new Set() })

  useEffect(() => {
    const key = `${pageId}:${pass}`
    if (fired.current.key !== key) fired.current = { key, ids: new Set() }
    const ids = fired.current.ids
    for (const cue of cues) {
      const reached = wordIndex >= 0 && cue.atWord <= wordIndex
      if (reached && !ids.has(cue.id)) {
        ids.add(cue.id)
        emitCue(cue)
      } else if (!reached && ids.has(cue.id)) {
        // Seeking backwards re-arms cues ahead of the new position.
        ids.delete(cue.id)
      }
    }
  }, [cues, pageId, pass, wordIndex])

  useEffect(() => {
    if (status === 'completed') emitComplete()
  }, [status, pass])

  const sentenceIndex = wordIndex >= 0 && wordIndex < words.length ? words[wordIndex].sentence : -1

  const reachedCues = useMemo(
    () => (wordIndex < 0 ? [] : cues.filter((cue) => cue.atWord <= wordIndex)),
    [cues, wordIndex],
  )

  const currentSentence = sentenceIndex >= 0 ? sentences[sentenceIndex] : null

  function seekToWord(target: number) {
    if (wordIndex < 0) return
    const clamped = Math.min(words.length, Math.max(0, target))
    dispatch({ type: 'seek', wordIndex: clamped })
    if (audioDriven) {
      if (clamped >= words.length) engine.pause()
      else engine.seekToWord(clamped)
    }
    const current = link.current
    if (current.key === sessionKey && current.sessionId) {
      client.seek({ sessionId: current.sessionId, segmentId: segmentFor(clamped), wordIndex: clamped })
      if (clamped >= words.length) current.playing = false
    }
  }

  function previousSentence() {
    if (wordIndex < 0) return
    if (wordIndex >= words.length) {
      seekToWord(sentences.at(-1)?.start ?? 0)
      return
    }
    const current = sentences[words[wordIndex].sentence]
    const target = wordIndex - current.start > 1 ? current.start : (sentences[current.index - 1]?.start ?? 0)
    seekToWord(target)
  }

  function nextSentence() {
    if (wordIndex < 0 || wordIndex >= words.length) return
    const current = words[wordIndex].sentence
    seekToWord(sentences[current + 1]?.start ?? words.length)
  }

  const statusRef = useRef(status)
  statusRef.current = status

  const play = () => {
    // Pressing Play always restores full volume (a barge-in may have ducked the voice).
    interrupted.current = false
    setAutoplayBlocked(false)
    engine.duck(false)
    dispatch({ type: 'play' })
  }

  return {
    status,
    wordIndex,
    pass,
    words,
    sentences,
    cues,
    reachedCues,
    currentSentence,
    progress: words.length === 0 || wordIndex < 0 ? 0 : Math.min(1, wordIndex / words.length),
    tutor: state.tutor,
    canSeek: wordIndex >= 0 && words.length > 0,
    audioFailed: Boolean(audioForPage.failed),
    autoplayBlocked,
    player: { kind: audioMode, producesSound: audioMode !== 'none' },
    audioMode,
    buffering: audioDriven && status === 'speaking' && engineState === 'buffering',
    getLevel: () => {
      if (statusRef.current !== 'speaking') return 0
      if (audioDriven) return engine.getLevel()
      const t = performance.now() / 1000
      return 0.28 + 0.18 * Math.abs(Math.sin(t * 7.1) * Math.sin(t * 2.3))
    },
    play,
    pause: () => dispatch({ type: 'pause' }),
    toggle: () => (status === 'speaking' ? dispatch({ type: 'pause' }) : play()),
    restart: () => {
      engine.duck(false)
      setAutoplayBlocked(false)
      dispatch({ type: 'restart' })
    },
    stop: () => {
      interrupted.current = false
      engine.duck(false)
      dispatch({ type: 'stop' })
    },
    previousSentence,
    nextSentence,
    seekToWord,
    startListening: () => dispatch({ type: 'listen' }),
    stopListening: () => dispatch({ type: 'stopListening' }),
    ask: (question) => dispatch({ type: 'ask', question }),
    dismissTutor: () => dispatch({ type: 'dismissTutor' }),
    queueAutoplay: () => dispatch({ type: 'queueAutoplay' }),
    interrupt: () => {
      if (statusRef.current !== 'speaking') return false
      interrupted.current = true
      engine.duck(true)
      dispatch({ type: 'pause' })
      return true
    },
    resumeAfterInterrupt: () => {
      engine.duck(false)
      if (!interrupted.current) return
      interrupted.current = false
      if (statusRef.current === 'paused' || statusRef.current === 'listening') dispatch({ type: 'play' })
    },
    duck: (on) => engine.duck(on),
  }
}
