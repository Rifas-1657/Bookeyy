'use client'

import { Code2, Loader2, Mic, Play, Send, Square, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useReducedMotion } from '@/lib/motion'
import { cn } from '@/lib/utils'
import { DiagramCanvas } from '../diagram-canvas'
import styles from '../book-feel.module.css'
import { TutorOrb, TUTOR_NAME, type TutorPresenceState } from './tutor-orb'
import type { LatestAnswer } from './use-course-tutor'
import type { VoiceQuestionState } from './use-voice-question'

export interface TutorCardProps {
  open: boolean
  /** What the learner is doing right now ('quiz' shows `quizSlot`, the R3 graded quiz). */
  mode: 'listening' | 'typing' | 'answer' | 'quiz'
  quizSlot?: React.ReactNode
  voiceState: VoiceQuestionState
  interim: string
  voiceError: string | null
  latest: LatestAnswer | null
  presence: TutorPresenceState
  getLevel?: () => number
  busy: boolean
  speaking: boolean
  onSubmitText: (text: string) => void
  onStopListening: () => void
  onStartListening: () => void
  onStopSpeaking: () => void
  onTryCode: (code: string) => void
  onClose: () => void
  /** Narration will continue when the card closes. */
  willResume: boolean
  /** Working out whether what was said is a command or a question. */
  understanding?: boolean
}

/**
 * The in-book margin note where the tutor answers an interruption: the
 * learner's question, the answer streaming in ink, an optional diagram drawn
 * with the book's diagram canvas and a "Try it" practical.
 */
export function TutorCard(props: TutorCardProps) {
  const { open, mode, latest } = props
  const reduced = useReducedMotion()
  const [draft, setDraft] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (open && mode === 'typing') inputRef.current?.focus()
  }, [open, mode])

  return (
    <AnimatePresence>
      {open && (
        <motion.aside
          role="dialog"
          aria-label={`Ask ${TUTOR_NAME}`}
          initial={reduced ? { opacity: 0 } : { opacity: 0, y: 18, rotate: -1.2, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, rotate: 0, scale: 1 }}
          exit={reduced ? { opacity: 0 } : { opacity: 0, y: 12, scale: 0.98 }}
          transition={{ duration: 0.32, ease: [0.16, 1, 0.3, 1] }}
          data-theme="book"
          className={cn(
            'pointer-events-auto absolute inset-x-2 bottom-2 z-40 flex max-h-[78%] flex-col overflow-hidden rounded-2xl text-book-ink sm:inset-x-auto sm:right-4 sm:bottom-4 sm:w-[min(26rem,calc(100%-2rem))]',
            styles.marginNote,
          )}
        >
          <header className="flex items-center justify-between gap-2 border-b border-book-amber/30 px-4 py-2.5">
            <TutorOrb state={props.presence} getLevel={props.getLevel} size={36} showLabel />
            <div className="flex items-center gap-1">
              {props.speaking && (
                <button
                  type="button"
                  onClick={props.onStopSpeaking}
                  className="inline-flex h-8 items-center gap-1 rounded-full px-2.5 text-xs font-medium hover:bg-book-marker/30 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  <Square className="size-3" aria-hidden="true" /> Stop voice
                </button>
              )}
              <button
                type="button"
                onClick={props.onClose}
                aria-label={props.willResume ? 'Close and continue the lesson' : 'Close'}
                className="grid size-8 place-items-center rounded-full hover:bg-book-marker/30 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            </div>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3 font-serif text-[0.95rem] leading-[1.55em]">
            {mode === 'listening' && (
              <div className="flex flex-col gap-2" aria-live="polite">
                <p className="font-sans text-xs font-semibold tracking-wider text-book-deep uppercase">
                  {props.understanding
                    ? 'Understanding…'
                    : props.voiceState === 'transcribing'
                      ? 'Writing down your question…'
                      : 'I’m listening — ask a question or say a command'}
                </p>
                <p className="min-h-[3em] italic">{props.interim || '…'}</p>
                <button
                  type="button"
                  onClick={props.onStopListening}
                  className="inline-flex h-10 items-center justify-center gap-2 self-start rounded-full bg-book-orange px-4 font-sans text-sm font-semibold text-book-ink hover:bg-book-marker focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                >
                  {props.voiceState === 'transcribing' || props.understanding ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Send className="size-4" aria-hidden="true" />}
                  Done — ask
                </button>
              </div>
            )}

            {mode === 'typing' && (
              <form
                className="flex flex-col gap-2"
                onSubmit={(event) => {
                  event.preventDefault()
                  if (!draft.trim()) return
                  props.onSubmitText(draft)
                  setDraft('')
                }}
              >
                <label htmlFor="tutor-card-input" className="font-sans text-xs font-semibold tracking-wider text-book-deep uppercase">
                  {props.voiceError ?? (props.voiceState === 'unsupported' ? 'Voice isn’t available here — type your question' : 'Type a question or a command')}
                </label>
                <textarea
                  id="tutor-card-input"
                  ref={inputRef}
                  value={draft}
                  rows={2}
                  maxLength={500}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                      event.preventDefault()
                      event.currentTarget.form?.requestSubmit()
                    }
                  }}
                  placeholder="e.g. Variable-um value-um enna difference? · next page · quiz me"
                  className="w-full resize-none rounded-xl border border-book-amber/40 bg-book-page/80 px-3 py-2 font-sans text-sm focus-visible:border-book-orange focus-visible:outline-none"
                />
                <div className="flex items-center gap-2">
                  <button
                    type="submit"
                    disabled={!draft.trim() || props.busy || props.understanding}
                    className="inline-flex h-9 items-center gap-1.5 rounded-full bg-book-orange px-4 font-sans text-sm font-semibold text-book-ink hover:bg-book-marker disabled:opacity-40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    <Send className="size-4" aria-hidden="true" /> Ask
                  </button>
                  {props.voiceState !== 'unsupported' && (
                    <button
                      type="button"
                      onClick={props.onStartListening}
                      className="inline-flex h-9 items-center gap-1.5 rounded-full px-3 font-sans text-sm hover:bg-book-marker/30 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                    >
                      <Mic className="size-4" aria-hidden="true" /> Speak instead
                    </button>
                  )}
                </div>
              </form>
            )}

            {mode === 'quiz' && props.quizSlot}

            {mode === 'answer' && latest && (
              <div className="flex flex-col gap-3">
                <p className="font-sans text-sm">
                  <span className="font-semibold text-book-deep">You asked</span> · {latest.question}
                </p>
                <div aria-live="polite">
                  {latest.text ? (
                    <p className={cn('whitespace-pre-wrap', latest.offTopic && 'text-book-muted italic')}>
                      {latest.text}
                      {!latest.final && <span className="ml-0.5 inline-block h-[1em] w-1 translate-y-0.5 animate-pulse bg-book-ink/60 motion-reduce:animate-none" aria-hidden="true" />}
                    </p>
                  ) : (
                    <p className="flex items-center gap-2 font-sans text-sm text-book-muted">
                      <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> {TUTOR_NAME} is thinking…
                    </p>
                  )}
                </div>
                {latest.final && latest.diagramSpec && (
                  <div className="rounded-2xl bg-book-page/70 p-1">
                    <DiagramCanvas spec={latest.diagramSpec} />
                  </div>
                )}
                {latest.final && !latest.diagramSpec && latest.diagram && latest.diagram.length > 0 && (
                  <pre className="overflow-x-auto rounded-xl bg-book-page/70 px-3 py-2 font-mono text-xs leading-relaxed">{latest.diagram.join('\n')}</pre>
                )}
                {latest.final && latest.code && (
                  <div className="overflow-hidden rounded-xl bg-[#1d160c]">
                    <pre className="max-h-40 overflow-auto px-3 py-2 font-mono text-xs text-[#fdf6e3]">{latest.code}</pre>
                    <button
                      type="button"
                      onClick={() => props.onTryCode(latest.code!)}
                      className="flex w-full items-center justify-center gap-1.5 border-t border-white/10 py-2 font-sans text-xs font-semibold text-book-marker hover:bg-white/5 focus-visible:ring-2 focus-visible:ring-book-marker focus-visible:outline-none"
                    >
                      <Play className="size-3.5 fill-current" aria-hidden="true" /> Try it in the code lab
                      <Code2 className="size-3.5" aria-hidden="true" />
                    </button>
                  </div>
                )}
                {latest.final && (
                  <p className="font-sans text-xs text-book-muted">
                    {props.willResume ? 'Close this note and I’ll carry on with the lesson.' : 'Any other doubt? Just ask.'}
                  </p>
                )}
              </div>
            )}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  )
}
