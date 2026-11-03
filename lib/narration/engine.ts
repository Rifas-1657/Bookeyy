/**
 * NarrationEngine: the single audio clock for a narrated page.
 *
 * - Plays an ordered list of audio segments (one prebuilt mp3, one TTS clip per
 *   sentence, or streamed `audio_chunk` frames) through HTMLAudioElements, with
 *   the next segment preloaded so hand-offs are near-gapless. HTMLAudioElement
 *   keeps pitch when the speed changes (WebAudio playbackRate would not).
 * - Derives the active word from `audio.currentTime` + per-segment word
 *   timings on every animation frame. Segments without timings get estimated
 *   timings spread over their real decoded duration.
 * - Exposes a live voice amplitude (WebAudio analyser when possible) for the
 *   tutor presence orb, plus duck/unduck for barge-in.
 *
 * The engine is UI-agnostic; `useNarrationStream` wires it to React state.
 */

import type { WordTiming } from '@/lib/contracts/book-events'
import { alignTimings, estimateTimings, timedWordAt, type TimedWord } from './timeline'
import type { NarrationWord } from './tokenize'

export type EngineState = 'idle' | 'buffering' | 'playing' | 'paused' | 'ended' | 'error'

export interface SegmentSource {
  /** Playable URL (same-origin path or blob: URL). */
  src: string
  timings?: WordTiming[]
  /** Revoke the blob URL when the engine is reset. */
  revoke?: boolean
}

export interface NarrationEngineCallbacks {
  onWord?: (wordIndex: number) => void
  onState?: (state: EngineState) => void
  onEnd?: () => void
  /** A segment could not be played; the caller should fall back to timed text from `wordIndex`. */
  onError?: (wordIndex: number, error: unknown) => void
  /** The browser blocked playback (autoplay policy); the engine paused itself. */
  onBlocked?: () => void
}

interface Slot {
  words: NarrationWord[]
  source: SegmentSource | null
  audio: HTMLAudioElement | null
  timed: TimedWord[] | null
  failed: boolean
  /** Aborting removes every listener this engine added to `audio`. */
  listeners: AbortController | null
  node: MediaElementAudioSourceNode | null
}

type AnalyserBundle = { ctx: AudioContext; analyser: AnalyserNode; data: Uint8Array<ArrayBuffer> }

let sharedAnalyser: AnalyserBundle | null = null
const connected = new WeakSet<HTMLAudioElement>()

function getAnalyser(): AnalyserBundle | null {
  if (typeof window === 'undefined') return null
  if (sharedAnalyser) return sharedAnalyser
  const Ctor =
    window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!Ctor) return null
  try {
    const ctx = new Ctor()
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 512
    analyser.smoothingTimeConstant = 0.6
    analyser.connect(ctx.destination)
    sharedAnalyser = { ctx, analyser, data: new Uint8Array(new ArrayBuffer(analyser.fftSize)) }
    return sharedAnalyser
  } catch {
    return null
  }
}

export class NarrationEngine {
  private slots: Slot[] = []
  private current = -1
  private sealed = false
  private state: EngineState = 'idle'
  private wantPlaying = false
  private pendingOffsetMs = 0
  private rate = 1
  private muted = false
  private volume = 1
  private duckTarget = 1
  private raf: number | null = null
  private lastWord = -1
  private disposed = false
  private analyserActive = false
  private fadeTimer: number | null = null

  constructor(private readonly cb: NarrationEngineCallbacks = {}) {}

  /* ------------------------------ planning ------------------------------ */

  /** Replaces the plan: one slot per word range. Sources can be provided later. */
  configure(words: NarrationWord[], ranges: Array<[number, number]>) {
    this.reset()
    this.slots = ranges.map(([start, end]) => ({
      words: words.slice(start, end),
      source: null,
      audio: null,
      timed: null,
      failed: false,
      listeners: null,
      node: null,
    }))
  }

  /** Streamed mode: no fixed plan, segments arrive in order. */
  configureStreaming() {
    this.reset()
  }

  get segmentCount() {
    return this.slots.length
  }

  get hasPlan() {
    return this.slots.length > 0
  }

  get engineState() {
    return this.state
  }

  provide(index: number, source: SegmentSource) {
    const slot = this.slots[index]
    if (!slot || this.disposed) return
    slot.source = source
    slot.failed = false
    if (index === this.current && this.wantPlaying && this.state === 'buffering') this.startSlot(index, this.pendingOffsetMs)
    else if (index === this.current + 1) this.preload(index)
  }

  /** Marks a slot as unavailable (e.g. TTS failed). */
  fail(index: number, error: unknown) {
    const slot = this.slots[index]
    if (!slot) return
    slot.failed = true
    if (index === this.current && this.wantPlaying) this.raiseError(error)
  }

  /** Appends a streamed segment covering `words`. */
  append(words: NarrationWord[], source: SegmentSource) {
    this.slots.push({ words, source, audio: null, timed: null, failed: false, listeners: null, node: null })
    const index = this.slots.length - 1
    if (this.wantPlaying && (this.state === 'buffering' || this.current === -1)) {
      if (this.current === -1 || this.current === index) this.startSlot(index, 0)
      else if (this.current >= index) this.startSlot(index, 0)
    } else if (index === this.current + 1) this.preload(index)
  }

  /** No more segments will arrive; finishing the last one ends the page. */
  seal() {
    this.sealed = true
    if (this.wantPlaying && this.state === 'buffering' && this.current >= this.slots.length) this.finish()
  }

  /* ------------------------------ transport ----------------------------- */

  play(fromWord: number) {
    if (this.disposed) return
    this.wantPlaying = true
    this.unlockAnalyser()
    const located = this.locate(fromWord)
    if (!located) {
      // Nothing planned for that word yet (streaming): wait for the next segment.
      this.current = Math.max(this.current, this.slots.length)
      this.setState('buffering')
      return
    }
    const { index, offsetMs } = located
    const slot = this.slots[index]
    if (this.current === index && slot.audio && this.state === 'paused') {
      if (Math.abs(slot.audio.currentTime * 1000 - offsetMs) > 400) this.seekAudio(slot.audio, offsetMs)
      this.resumeAudio(slot.audio)
      return
    }
    this.startSlot(index, offsetMs)
  }

  pause() {
    this.wantPlaying = false
    const audio = this.currentAudio()
    this.stopClock()
    if (audio && !audio.paused) {
      // A short fade reads as a teacher pausing, not a cut-off.
      const from = audio.volume
      let step = 0
      if (this.fadeTimer !== null) window.clearInterval(this.fadeTimer)
      this.fadeTimer = window.setInterval(() => {
        step += 1
        if (this.wantPlaying) {
          window.clearInterval(this.fadeTimer!)
          this.fadeTimer = null
          audio.volume = Math.max(0, Math.min(1, this.volume))
          return
        }
        audio.volume = Math.max(0, from * (1 - step / 8))
        if (step >= 8) {
          window.clearInterval(this.fadeTimer!)
          this.fadeTimer = null
          audio.pause()
          audio.volume = Math.max(0, Math.min(1, this.volume))
        }
      }, 18)
    }
    if (this.state !== 'idle' && this.state !== 'ended') this.setState('paused')
  }

  seekToWord(wordIndex: number) {
    const located = this.locate(wordIndex)
    if (!located) return
    this.lastWord = -1
    if (located.index === this.current && this.slots[located.index].audio) {
      this.seekAudio(this.slots[located.index].audio!, located.offsetMs)
      return
    }
    if (this.wantPlaying) this.startSlot(located.index, located.offsetMs)
    else {
      this.stopAudio(this.current)
      this.current = located.index
      this.pendingOffsetMs = located.offsetMs
      this.setState('paused')
    }
  }

  setRate(rate: number) {
    this.rate = rate
    for (const slot of this.slots) if (slot.audio) slot.audio.playbackRate = rate
  }

  setMuted(muted: boolean) {
    this.muted = muted
    for (const slot of this.slots) if (slot.audio) slot.audio.muted = muted
  }

  /** Smoothly lowers (or restores) the voice; used when the learner starts talking. */
  duck(on: boolean) {
    this.duckTarget = on ? 0.18 : 1
    this.ensureClock()
  }

  /** 0..1 voice amplitude for visualisers. Cheap: call from rAF. */
  getLevel(): number {
    if (this.state !== 'playing') return 0
    if (this.analyserActive && sharedAnalyser) {
      const { analyser, data } = sharedAnalyser
      analyser.getByteTimeDomainData(data)
      let sum = 0
      for (let i = 0; i < data.length; i += 4) {
        const v = (data[i] - 128) / 128
        sum += v * v
      }
      return Math.min(1, Math.sqrt(sum / (data.length / 4)) * 3.2)
    }
    const t = performance.now() / 1000
    return 0.35 + 0.2 * Math.sin(t * 9.3) * Math.sin(t * 3.1)
  }

  reset() {
    this.stopClock()
    if (this.fadeTimer !== null) window.clearInterval(this.fadeTimer)
    this.fadeTimer = null
    for (const slot of this.slots) {
      slot.listeners?.abort()
      slot.listeners = null
      const audio = slot.audio
      if (audio) {
        audio.pause()
        // Drop the network/decoder resources; a detached element with a src keeps them.
        audio.removeAttribute('src')
        try {
          audio.load()
        } catch {
          /* ignore */
        }
      }
      try {
        slot.node?.disconnect()
      } catch {
        /* already disconnected */
      }
      slot.node = null
      slot.audio = null
      if (slot.source?.revoke) URL.revokeObjectURL(slot.source.src)
    }
    this.slots = []
    this.current = -1
    this.sealed = false
    this.wantPlaying = false
    this.lastWord = -1
    this.pendingOffsetMs = 0
    this.duckTarget = 1
    this.volume = 1
    this.setState('idle')
  }

  dispose() {
    this.reset()
    this.disposed = true
  }

  /* ------------------------------ internals ----------------------------- */

  private locate(wordIndex: number): { index: number; offsetMs: number } | null {
    for (let i = 0; i < this.slots.length; i += 1) {
      const slot = this.slots[i]
      const first = slot.words[0]?.index
      const last = slot.words[slot.words.length - 1]?.index
      if (first === undefined || last === undefined) continue
      if (wordIndex < first || wordIndex > last) continue
      if (wordIndex === first) return { index: i, offsetMs: 0 }
      if (!slot.timed && slot.audio && slot.audio.readyState >= 1) this.computeTimings(i)
      const timed = slot.timed?.find((t) => t.wordIndex === wordIndex)
      return { index: i, offsetMs: timed ? timed.startMs : -wordIndex }
    }
    return null
  }

  private currentAudio() {
    return this.slots[this.current]?.audio ?? null
  }

  private element(index: number): HTMLAudioElement | null {
    const slot = this.slots[index]
    if (!slot?.source || slot.failed) return null
    if (slot.audio) return slot.audio
    const audio = new Audio()
    audio.preload = 'auto'
    audio.src = slot.source.src
    audio.playbackRate = this.rate
    audio.muted = this.muted
    audio.volume = this.volume
    const listeners = new AbortController()
    const { signal } = listeners
    // After a reset/reconfigure the slot is gone: a stale element must not drive the clock.
    const live = () => this.slots[index] === slot && slot.audio === audio
    audio.addEventListener('loadedmetadata', () => live() && this.computeTimings(index), { signal })
    audio.addEventListener('ended', () => live() && this.onEnded(index), { signal })
    audio.addEventListener(
      'error',
      () => {
        if (!live()) return
        slot.failed = true
        if (index === this.current && this.wantPlaying) this.raiseError(audio.error)
      },
      { signal },
    )
    slot.listeners = listeners
    slot.audio = audio
    this.connectAnalyser(slot, audio)
    return audio
  }

  private preload(index: number) {
    const audio = this.element(index)
    if (audio && audio.readyState === 0) audio.load()
  }

  private computeTimings(index: number) {
    const slot = this.slots[index]
    if (!slot || slot.timed) return
    const durationMs = Number.isFinite(slot.audio?.duration) ? (slot.audio!.duration * 1000) : 0
    slot.timed = alignTimings(slot.source?.timings, slot.words) ?? estimateTimings(slot.words, durationMs || slot.words.length * 380)
    // A seek requested before metadata (negative = "word index, resolve later").
    if (index === this.current && this.pendingOffsetMs < 0 && slot.audio) {
      const target = slot.timed.find((t) => t.wordIndex === -this.pendingOffsetMs)
      this.pendingOffsetMs = 0
      if (target) this.seekAudio(slot.audio, target.startMs)
    }
  }

  private startSlot(index: number, offsetMs: number) {
    if (index !== this.current) this.stopAudio(this.current)
    this.current = index
    this.lastWord = -1
    const slot = this.slots[index]
    if (!slot) {
      this.setState('buffering')
      return
    }
    if (slot.failed) {
      this.raiseError(new Error('segment unavailable'))
      return
    }
    const audio = this.element(index)
    if (!audio) {
      this.pendingOffsetMs = offsetMs
      this.setState('buffering')
      return
    }
    this.pendingOffsetMs = offsetMs
    const begin = () => {
      if (this.slots[index] !== slot || slot.audio !== audio || this.current !== index || !this.wantPlaying) return
      if (slot.timed === null) this.computeTimings(index)
      let target = this.pendingOffsetMs
      if (target < 0) target = slot.timed?.find((t) => t.wordIndex === -target)?.startMs ?? 0
      this.pendingOffsetMs = 0
      this.seekAudio(audio, target)
      this.resumeAudio(audio)
    }
    if (audio.readyState >= 1) begin()
    else {
      this.setState('buffering')
      audio.addEventListener('loadedmetadata', begin, { once: true, signal: slot.listeners?.signal })
      audio.load()
    }
    this.preload(index + 1)
  }

  private resumeAudio(audio: HTMLAudioElement) {
    audio.playbackRate = this.rate
    audio.muted = this.muted
    void audio
      .play()
      .then(() => {
        if (!this.wantPlaying || this.currentAudio() !== audio) {
          audio.pause()
          return
        }
        this.setState('playing')
        this.ensureClock()
      })
      .catch((error: unknown) => {
        // A reset or a newer slot already replaced this element.
        if (this.currentAudio() !== audio) return
        // Autoplay policy: surface as paused rather than failing over to text.
        if ((error as DOMException)?.name === 'NotAllowedError') {
          this.wantPlaying = false
          this.setState('paused')
          this.cb.onBlocked?.()
          return
        }
        if ((error as DOMException)?.name === 'AbortError') return
        this.raiseError(error)
      })
  }

  private seekAudio(audio: HTMLAudioElement, offsetMs: number) {
    try {
      audio.currentTime = Math.max(0, offsetMs / 1000)
    } catch {
      /* not seekable yet */
    }
  }

  private stopAudio(index: number) {
    const audio = this.slots[index]?.audio
    if (audio && !audio.paused) audio.pause()
  }

  private onEnded(index: number) {
    if (index !== this.current) return
    const next = index + 1
    if (next < this.slots.length) {
      this.startSlot(next, 0)
      return
    }
    if (this.sealed) {
      this.finish()
      return
    }
    this.current = next
    this.setState('buffering')
  }

  private finish() {
    this.wantPlaying = false
    this.stopClock()
    this.setState('ended')
    this.cb.onEnd?.()
  }

  private raiseError(error: unknown) {
    const slot = this.slots[this.current]
    const word = this.lastWord >= 0 ? this.lastWord : (slot?.words[0]?.index ?? 0)
    this.wantPlaying = false
    this.stopAudio(this.current)
    this.stopClock()
    this.setState('error')
    this.cb.onError?.(word, error)
  }

  private setState(state: EngineState) {
    if (this.state === state) return
    this.state = state
    this.cb.onState?.(state)
  }

  private ensureClock() {
    if (this.raf !== null || typeof requestAnimationFrame === 'undefined') return
    const tick = () => {
      this.raf = null
      if (this.disposed) return
      this.stepVolume()
      const slot = this.slots[this.current]
      const audio = slot?.audio
      if (slot && audio && this.state === 'playing') {
        if (!slot.timed) this.computeTimings(this.current)
        const timed = slot.timed ?? []
        const at = timedWordAt(timed, audio.currentTime * 1000)
        const word = at >= 0 ? timed[at].wordIndex : (slot.words[0]?.index ?? 0)
        if (word !== this.lastWord) {
          this.lastWord = word
          this.cb.onWord?.(word)
        }
      }
      if (this.state === 'playing' || Math.abs(this.volume - this.duckTarget) > 0.01) {
        this.raf = requestAnimationFrame(tick)
      }
    }
    this.raf = requestAnimationFrame(tick)
  }

  private stepVolume() {
    if (Math.abs(this.volume - this.duckTarget) <= 0.01) return
    this.volume += (this.duckTarget - this.volume) * 0.18
    for (const slot of this.slots) if (slot.audio) slot.audio.volume = Math.max(0, Math.min(1, this.volume))
  }

  private stopClock() {
    if (this.raf !== null) cancelAnimationFrame(this.raf)
    this.raf = null
  }

  private unlockAnalyser() {
    const bundle = getAnalyser()
    if (bundle && bundle.ctx.state === 'suspended') void bundle.ctx.resume().catch(() => {})
  }

  /**
   * Routes the element through the shared analyser, but only while the
   * AudioContext is running: a suspended context would silence the element.
   */
  private connectAnalyser(slot: Slot, audio: HTMLAudioElement) {
    const bundle = getAnalyser()
    if (!bundle || bundle.ctx.state !== 'running' || connected.has(audio)) return
    try {
      const src = bundle.ctx.createMediaElementSource(audio)
      src.connect(bundle.analyser)
      connected.add(audio)
      slot.node = src
      this.analyserActive = true
    } catch {
      /* cross-origin or already connected: amplitude falls back to a synthetic pulse */
    }
  }
}
