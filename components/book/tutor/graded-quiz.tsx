'use client'

import { Check, ListChecks, Loader2, Mic, RotateCcw, Send, Sparkles, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { gradeAnswer, startQuiz, type GradeResponse, type QuizQuestion } from '@/lib/learning'
import { quizSummaryLine, type Lang, type QuizPage } from '@/lib/learning/model'
import { cn } from '@/lib/utils'
import { useVoiceQuestion } from './use-voice-question'

export type QuizMode = 'page' | 'check' | 'review'

interface GradedQuizProps {
  courseId: string
  page: QuizPage
  pages: QuizPage[]
  language: Lang
  mode: QuizMode
  /** Speaks a line in the teacher's voice; resolves when said. */
  speak: (text: string) => Promise<void>
  /** Stops any line being spoken (e.g. when the learner moves on). */
  stopSpeaking: () => void
  onClose: () => void
  /** The quiz finished (score is reported for the reader's own UI). */
  onFinished?: (correct: number, total: number) => void
}

type Phase =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'empty' }
  | { kind: 'asking'; index: number }
  | { kind: 'grading'; index: number }
  | { kind: 'feedback'; index: number; result: GradeResponse }
  | { kind: 'done' }

const TITLE: Record<QuizMode, Record<Lang, string>> = {
  page: { english: 'Quick quiz', tanglish: 'Quick quiz' },
  check: { english: 'End-of-page check', tanglish: 'Page mudinjadhu — quick check' },
  review: { english: '1-minute review', tanglish: '1-minute review' },
}

/** Picks the MCQ option a spoken answer refers to ("B", "second one", or the option's words). */
function optionFromSpeech(text: string, options: string[]): string | null {
  const t = text.toLowerCase().trim()
  const letter = /^(?:option\s*)?([a-d])\b\.?$/.exec(t)?.[1] ?? /\b(?:option|answer)\s+([a-d])\b/.exec(t)?.[1]
  if (letter) return options['abcd'.indexOf(letter)] ?? null
  const ordinal = ['first', 'second', 'third', 'fourth'].findIndex((w) => t.includes(w))
  if (ordinal >= 0 && options[ordinal]) return options[ordinal]
  const hit = options.filter((o) => t.includes(o.toLowerCase()))
  return hit.length === 1 ? hit[0] : null
}

/**
 * R3 graded quiz in the tutor note: 2–3 questions on the current page (or the concepts due
 * for review). Multiple choice by tapping (or saying the option), short answers by typing or
 * speaking. Each answer is graded at once, the feedback is spoken in the teacher's voice, and
 * the result feeds the learner model.
 */
export function GradedQuiz({ courseId, page, pages, language, mode, speak, stopSpeaking, onClose, onFinished }: GradedQuizProps) {
  const en = language === 'english'
  const [questions, setQuestions] = useState<QuizQuestion[]>([])
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' })
  const [draft, setDraft] = useState('')
  const [chosen, setChosen] = useState<string | null>(null)
  const [score, setScore] = useState(0)
  const askedAt = useRef(0)
  const voice = useVoiceQuestion()
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Load once per quiz (page + mode).
  useEffect(() => {
    let alive = true
    setPhase({ kind: 'loading' })
    setScore(0)
    startQuiz(courseId, mode === 'review' ? null : page, language, mode === 'review' ? 'review' : 'page', pages)
      .then((quiz) => {
        if (!alive) return
        setQuestions(quiz.questions)
        if (quiz.questions.length === 0) {
          setPhase({ kind: 'empty' })
          return
        }
        setPhase({ kind: 'asking', index: 0 })
        const intro = mode === 'review'
          ? en ? 'Quick review of the ideas you missed earlier.' : 'Munnaadi miss panna points-a quick-a review pannalaam.'
          : mode === 'check'
            ? en ? 'Page done! A quick check before we move on.' : 'Page mudinjadhu! Move panradhukku munnaadi oru quick check.'
            : en ? `Here is a ${quiz.questions.length}-question quiz on this page.` : `Indha page-la ${quiz.questions.length} questions. Ready-a?`
        void speak(`${intro} ${quiz.questions[0].prompt}`)
      })
      .catch((err: unknown) => {
        if (alive) setPhase({ kind: 'error', message: err instanceof Error ? err.message : 'Could not start the quiz.' })
      })
    return () => {
      alive = false
      stopSpeaking()
      voice.cancel()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [courseId, page.id, mode])

  useEffect(() => {
    if (phase.kind === 'asking') {
      askedAt.current = Date.now()
      setDraft('')
      setChosen(null)
      if (questions[phase.index]?.kind === 'short') inputRef.current?.focus()
    }
  }, [phase, questions])

  const submit = useCallback(
    async (index: number, answer: string) => {
      const q = questions[index]
      if (!q || !answer.trim()) return
      stopSpeaking()
      voice.cancel()
      setPhase({ kind: 'grading', index })
      try {
        const result = await gradeAnswer(courseId, q.id, answer, {
          responseMs: Date.now() - askedAt.current,
          lang: language,
          page: page,
          pages,
          pageId: q.pageId,
        })
        if (result.correct) setScore((s) => s + 1)
        setPhase({ kind: 'feedback', index, result })
        void speak(result.feedback)
      } catch (err) {
        setPhase({ kind: 'error', message: err instanceof Error ? err.message : 'Could not check that answer.' })
      }
    },
    [questions, courseId, language, page, pages, speak, stopSpeaking, voice],
  )

  const next = useCallback(
    (index: number) => {
      stopSpeaking()
      if (index + 1 < questions.length) {
        setPhase({ kind: 'asking', index: index + 1 })
        void speak(questions[index + 1].prompt)
        return
      }
      setPhase({ kind: 'done' })
      onFinished?.(score, questions.length)
      void speak(quizSummaryLine(score, questions.length, language))
    },
    [questions, score, language, speak, stopSpeaking, onFinished],
  )

  const listen = useCallback(
    (index: number) => {
      const q = questions[index]
      if (!q) return
      if (voice.state === 'listening') {
        voice.stop((text) => {
          if (!text.trim()) return
          if (q.kind === 'mcq') {
            const option = optionFromSpeech(text, q.options)
            if (option) {
              setChosen(option)
              void submit(index, option)
            } else setDraft(text)
          } else {
            setDraft(text)
            void submit(index, text)
          }
        })
        return
      }
      stopSpeaking()
      void voice.start()
    },
    [questions, voice, submit, stopSpeaking],
  )

  const total = questions.length
  const current = phase.kind === 'asking' || phase.kind === 'grading' || phase.kind === 'feedback' ? questions[phase.index] : undefined
  const index = 'index' in phase ? phase.index : 0

  return (
    <section aria-label={TITLE[mode][language]} className="flex flex-col gap-3">
      <header className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 font-sans text-xs font-semibold tracking-wider text-book-deep uppercase">
          {mode === 'review' ? <RotateCcw className="size-4" aria-hidden="true" /> : <ListChecks className="size-4" aria-hidden="true" />}
          {TITLE[mode][language]}
        </p>
        {total > 0 && phase.kind !== 'done' && (
          <span className="font-mono text-xs text-book-muted tabular-nums" aria-label={`Question ${index + 1} of ${total}`}>
            {index + 1}/{total}
          </span>
        )}
      </header>

      {phase.kind === 'loading' && (
        <p className="flex items-center gap-2 font-sans text-sm text-book-muted">
          <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
          {en ? 'Writing questions for this page…' : 'Indha page-ku questions ready pannren…'}
        </p>
      )}

      {phase.kind === 'error' && (
        <div className="flex flex-col gap-2 font-sans text-sm">
          <p className="text-book-muted">{phase.message}</p>
          <button type="button" onClick={onClose} className="self-start rounded-full px-3 py-1.5 font-semibold hover:bg-book-marker/30">
            {en ? 'Close' : 'Close pannunga'}
          </button>
        </div>
      )}

      {phase.kind === 'empty' && (
        <p className="font-sans text-sm text-book-muted">
          {mode === 'review'
            ? en ? 'Nothing is due for review right now — nice!' : 'Ippo review panna edhuvum illa — super!'
            : en ? 'This page has no key ideas I can quiz you on yet.' : 'Indha page-la quiz panna key points illa.'}
        </p>
      )}

      {current && (
        <div className="flex flex-col gap-2.5">
          <p className="font-serif text-[0.98rem] leading-snug" lang={en ? 'en' : 'ta-Latn'}>
            {current.prompt}
          </p>
          {current.kind === 'mcq' ? (
            <ul className="flex flex-col gap-1.5" role="list">
              {current.options.map((option, i) => {
                const result = phase.kind === 'feedback' ? phase.result : null
                const isAnswer = result && option.trim().toLowerCase() === result.answer.trim().toLowerCase()
                const isChosen = chosen === option
                return (
                  <li key={option}>
                    <button
                      type="button"
                      disabled={phase.kind !== 'asking'}
                      onClick={() => {
                        setChosen(option)
                        void submit(index, option)
                      }}
                      aria-pressed={isChosen}
                      className={cn(
                        'flex w-full items-start gap-2 rounded-xl border px-3 py-2 text-left font-sans text-sm transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
                        phase.kind === 'asking' && 'border-book-amber/40 bg-book-page/70 hover:border-book-orange hover:bg-book-marker/20',
                        isAnswer && 'border-emerald-600/60 bg-emerald-500/15',
                        result && isChosen && !isAnswer && 'border-rose-500/60 bg-rose-500/10',
                        result && !isAnswer && !isChosen && 'border-transparent opacity-60',
                      )}
                    >
                      <span className="font-mono text-xs text-book-muted">{'ABCD'[i]}</span>
                      <span className="flex-1">{option}</span>
                      {isAnswer && <Check className="size-4 text-emerald-700" aria-label="Correct answer" />}
                      {result && isChosen && !isAnswer && <X className="size-4 text-rose-600" aria-label="Your answer" />}
                    </button>
                  </li>
                )
              })}
            </ul>
          ) : (
            <form
              className="flex flex-col gap-2"
              onSubmit={(event) => {
                event.preventDefault()
                void submit(index, draft)
              }}
            >
              <label htmlFor="quiz-answer" className="sr-only">
                Your answer
              </label>
              <textarea
                id="quiz-answer"
                ref={inputRef}
                value={voice.state === 'listening' ? voice.interim || draft : draft}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault()
                    event.currentTarget.form?.requestSubmit()
                  }
                }}
                disabled={phase.kind !== 'asking'}
                rows={2}
                maxLength={500}
                placeholder={en ? 'Type or say your answer' : 'Answer-a type pannunga illa sollunga'}
                className="w-full resize-none rounded-xl border border-book-amber/40 bg-book-page/80 px-3 py-2 font-sans text-sm focus-visible:border-book-orange focus-visible:outline-none disabled:opacity-70"
              />
              {phase.kind === 'asking' && (
                <div className="flex items-center gap-2">
                  <button
                    type="submit"
                    disabled={!draft.trim()}
                    className="inline-flex h-9 items-center gap-1.5 rounded-full bg-book-orange px-4 font-sans text-sm font-semibold text-book-ink hover:bg-book-marker disabled:opacity-40 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
                  >
                    <Send className="size-4" aria-hidden="true" /> {en ? 'Check' : 'Check pannunga'}
                  </button>
                </div>
              )}
            </form>
          )}

          {phase.kind === 'asking' && voice.state !== 'unsupported' && (
            <button
              type="button"
              onClick={() => listen(index)}
              aria-pressed={voice.state === 'listening'}
              className="inline-flex h-9 items-center gap-1.5 self-start rounded-full px-3 font-sans text-sm hover:bg-book-marker/30 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none aria-pressed:bg-book-marker/40"
            >
              {voice.state === 'transcribing' ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Mic className="size-4" aria-hidden="true" />}
              {voice.state === 'listening' ? (en ? 'Done — check my answer' : 'Mudinjadhu — check pannunga') : en ? 'Say the answer' : 'Answer-a sollunga'}
            </button>
          )}

          {phase.kind === 'grading' && (
            <p className="flex items-center gap-2 font-sans text-sm text-book-muted">
              <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" /> {en ? 'Checking…' : 'Check pannren…'}
            </p>
          )}

          {phase.kind === 'feedback' && (
            <div
              aria-live="polite"
              className={cn(
                'rounded-xl px-3 py-2 font-sans text-sm',
                phase.result.correct ? 'bg-emerald-500/12 text-book-ink' : 'bg-book-marker/25 text-book-ink',
              )}
            >
              <p lang={en ? 'en' : 'ta-Latn'}>{phase.result.feedback}</p>
              {phase.result.mastery !== null && (
                <p className="mt-1 text-xs text-book-muted">
                  {phase.result.conceptLabel}: {Math.round(phase.result.mastery * 100)}% {en ? 'mastery' : 'mastery'}
                </p>
              )}
              <button
                type="button"
                onClick={() => next(phase.index)}
                className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-full bg-book-orange px-3 text-xs font-semibold text-book-ink hover:bg-book-marker focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              >
                {phase.index + 1 < total ? (en ? 'Next question' : 'Adutha question') : en ? 'See my score' : 'Score paakalaam'}
              </button>
            </div>
          )}
        </div>
      )}

      {phase.kind === 'done' && (
        <div className="flex flex-col gap-2 font-sans text-sm" aria-live="polite">
          <p className="flex items-center gap-1.5 font-semibold">
            <Sparkles className="size-4 text-book-orange" aria-hidden="true" /> {score}/{total}
          </p>
          <p lang={en ? 'en' : 'ta-Latn'}>{quizSummaryLine(score, total, language)}</p>
          <button
            type="button"
            onClick={onClose}
            className="self-start rounded-full bg-book-orange px-3 py-1.5 text-xs font-semibold text-book-ink hover:bg-book-marker focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            {en ? 'Back to the lesson' : 'Lesson-ku poalaam'}
          </button>
        </div>
      )}
    </section>
  )
}
