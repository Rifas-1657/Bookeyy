'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { speakAnswer, type SpokenAnswer } from '@/lib/narration/speak'
import type { NarrationStream } from '@/lib/narration/use-narration-stream'
import {
  ackFor,
  ackProblem,
  buildSimplerPrompt,
  type AckProblem,
  type IntentMatch,
  type VoiceIntent,
} from '@/lib/voice-commands'
import { NARRATION_RATES, type BookLessonPage, type NarrationRate } from '@/types/book'
import type { CourseTutor } from './tutor/use-course-tutor'

export type CommandOrigin = 'voice' | 'text' | 'button' | 'handsfree'

/** Longest an acknowledgement may hold up the action that follows it. */
const ACK_CAP_MS = 3000

export interface LessonCommandContext {
  page: BookLessonPage | undefined
  pages: BookLessonPage[]
  index: number | null
  language: 'tanglish' | 'english'
  rate: number
  setRate: (rate: NarrationRate) => void
  muted: boolean
  /** Guided mode (narration on). */
  guided: boolean
  /** Switches the reader to guided mode (narration needs it). */
  ensureGuided: () => void
  stream: NarrationStream
  tutor: CourseTutor
  goTo: (next: number, options?: { animate?: boolean }) => void
  goNext: () => void
  goPrevious: () => void
  /** Was the lesson voice speaking before this command (including a barge-in that paused it)? */
  wasSpeaking: () => boolean
  /** Closes the tutor card; `resume` continues an interrupted narration. */
  closeCard: (resume: boolean) => void
  /** Shows the tutor card in answer mode (pausing narration if it is speaking). */
  openAnswerCard: () => void
  stopAnswerVoice: () => void
  requestRunCode: () => void
  requestShowDiagram: () => void
  /** R3: opens the graded quiz for this page in the tutor note. */
  startQuiz: () => void
  /** R3: learner-model evidence from a command ("repeat" signals the explanation didn't land). */
  recordEvidence: (kind: 'repeat') => void
}

export interface CommandAck {
  id: number
  text: string
  intent: VoiceIntent
}

/**
 * Carries out lesson commands (from voice, typed text, hands-free or the
 * visible command buttons) with a short spoken + visual acknowledgement in the
 * learner's language and voice. The context is read through a ref, so stale
 * closures (e.g. after a page turn) always act on the latest reader state.
 */
export function useLessonCommands(context: LessonCommandContext) {
  const ctx = useRef(context)
  ctx.current = context
  /** Last acknowledgement/notice (kept for the aria-live region). */
  const [ack, setAck] = useState<CommandAck | null>(null)
  /** Whether the visual pill for `ack` is showing. */
  const [ackVisible, setAckVisible] = useState(false)
  const ackVoice = useRef<SpokenAnswer | null>(null)
  const ackSeq = useRef(0)
  const lastAckText = useRef('')

  const stopAck = useCallback(() => {
    ackVoice.current?.stop()
    ackVoice.current = null
  }, [])
  useEffect(() => stopAck, [stopAck])

  // The visual pill fades after a few seconds; the aria-live text stays until replaced.
  useEffect(() => {
    if (!ack) return
    setAckVisible(true)
    const timer = window.setTimeout(() => setAckVisible(false), 3200)
    return () => window.clearTimeout(timer)
  }, [ack])

  /** Shows/announces a notice without speaking it. */
  const announce = useCallback((text: string) => {
    ackSeq.current += 1
    setAck({ id: ackSeq.current, text, intent: 'question' })
  }, [])

  /** Shows + speaks `text`; resolves when it has been said (or after ACK_CAP_MS). */
  const say = useCallback(
    (text: string, intent: VoiceIntent): Promise<void> => {
      stopAck()
      ackSeq.current += 1
      setAck({ id: ackSeq.current, text, intent })
      lastAckText.current = text
      const { language, muted } = ctx.current
      const spoken = speakAnswer(text, { lang: language, rate: 1, muted })
      ackVoice.current = spoken
      if (!spoken.audible) return Promise.resolve()
      return new Promise<void>((resolve) => {
        const timer = window.setTimeout(() => {
          if (ackVoice.current === spoken) stopAck()
          resolve()
        }, ACK_CAP_MS)
        void spoken.done.then(() => {
          window.clearTimeout(timer)
          if (ackVoice.current === spoken) ackVoice.current = null
          resolve()
        })
      })
    },
    [stopAck],
  )

  const problem = useCallback(
    (kind: AckProblem, intent: VoiceIntent, resumeAfter: boolean) => {
      const c = ctx.current
      c.closeCard(false)
      void say(ackProblem(kind, c.language), intent).then(() => {
        if (resumeAfter) ctx.current.stream.play()
      })
    },
    [say],
  )

  /** After navigating: wait for the ack, then start teaching the new page. */
  const playAfter = useCallback((done: Promise<void>) => {
    void done.then(() => {
      const c = ctx.current
      if (c.guided) c.stream.play()
    })
  }, [])

  const execute = useCallback(
    (match: Pick<IntentMatch, 'intent' | 'args'>, origin: CommandOrigin) => {
      const c = ctx.current
      const { intent, args } = match
      if (intent === 'question' || !c.page) return
      const wasSpeaking = c.wasSpeaking()
      const lang = c.language
      const source = origin === 'text' || origin === 'button' ? 'text' : 'voice'
      const line = ackFor(intent, args, lang)
      /** Keep teaching if she was teaching before the command. */
      const carryOn = () => {
        const s = ctx.current.stream
        if (!wasSpeaking) return
        if (s.status === 'speaking') s.duck(false)
        else s.play()
      }

      switch (intent) {
        case 'stop': {
          c.stopAnswerVoice()
          c.closeCard(false)
          c.stream.pause()
          c.stream.duck(false)
          void say(line, intent)
          return
        }
        case 'resume': {
          c.closeCard(false)
          if (c.stream.status === 'completed' && c.index !== null && c.index < c.pages.length - 1) {
            c.goNext()
            playAfter(say(ackFor('next_page', {}, lang), 'next_page'))
            return
          }
          c.ensureGuided()
          void say(line, intent).then(() => ctx.current.stream.play())
          return
        }
        case 'next_page': {
          if (c.index === null || c.index >= c.pages.length - 1) return problem('no_next_page', intent, wasSpeaking)
          c.closeCard(false)
          c.stream.pause()
          c.goNext()
          playAfter(say(line, intent))
          return
        }
        case 'previous_page': {
          if (!c.index) return problem('no_previous_page', intent, wasSpeaking)
          c.closeCard(false)
          c.stream.pause()
          c.goPrevious()
          playAfter(say(line, intent))
          return
        }
        case 'goto_chapter':
        case 'goto_page': {
          let target = -1
          if (intent === 'goto_page' && args.page) {
            target = args.page - 1 < c.pages.length ? args.page - 1 : -1
          } else {
            const chapterNumber = args.chapterDelta ? c.page.chapterNumber + args.chapterDelta : args.chapter
            const inChapter = c.pages.filter((entry) => entry.chapterNumber === chapterNumber)
            const wanted = inChapter[(args.page ?? 1) - 1] ?? (args.page ? undefined : inChapter[0])
            target = wanted ? c.pages.indexOf(wanted) : -1
          }
          if (target < 0) return problem(intent === 'goto_page' ? 'no_page' : 'no_chapter', intent, wasSpeaking)
          c.closeCard(false)
          c.stream.pause()
          if (target !== c.index) c.goTo(target, { animate: true })
          const resolvedLine =
            args.chapterDelta && c.pages[target]
              ? ackFor('goto_chapter', { chapter: c.pages[target].chapterNumber }, lang)
              : line
          playAfter(say(resolvedLine, intent))
          return
        }
        case 'repeat': {
          c.recordEvidence('repeat')
          c.stopAnswerVoice()
          c.closeCard(false)
          c.stream.pause()
          c.ensureGuided()
          const s = c.stream
          const sentence = s.currentSentence
          const fromSentence = args.scope !== 'page' && sentence && s.status !== 'completed' && s.wordIndex >= 0
          void say(line, intent).then(() => {
            const latest = ctx.current.stream
            if (fromSentence && sentence) {
              latest.seekToWord(sentence.start)
              latest.play()
            } else {
              latest.restart()
            }
          })
          return
        }
        case 'slower':
        case 'faster': {
          const rates = NARRATION_RATES as readonly NarrationRate[]
          const at = rates.indexOf(c.rate as NarrationRate)
          const nextIndex = intent === 'slower' ? Math.max(0, (at < 0 ? 1 : at) - 1) : Math.min(rates.length - 1, (at < 0 ? 1 : at) + 1)
          if (rates[nextIndex] === c.rate) return problem(intent === 'slower' ? 'slowest' : 'fastest', intent, wasSpeaking)
          c.setRate(rates[nextIndex])
          c.closeCard(false)
          c.stream.pause()
          void say(line, intent).then(carryOn)
          return
        }
        case 'quiz': {
          // R3: a graded quiz (lesson items / verified generated questions), not a tutor reply.
          c.stopAnswerVoice()
          c.startQuiz()
          // The quiz speaks its own intro, so the acknowledgement is shown, not spoken.
          announce(line)
          return
        }
        case 'simpler': {
          if (c.tutor.busy) {
            void say(ackProblem('busy', lang), intent)
            return
          }
          const prompt = buildSimplerPrompt(c.page.title, lang, c.stream.currentSentence?.text)
          const display = lang === 'english' ? 'Explain this page more simply' : 'Innum simple-a sollunga'
          c.stopAnswerVoice()
          if (!c.tutor.ask(prompt, source, { display, kind: 'simpler' })) {
            void say(ackProblem('busy', lang), intent)
            return
          }
          c.openAnswerCard()
          void say(line, intent)
          return
        }
        case 'run_code': {
          if (!c.page.codeExample?.source?.trim()) return problem('no_code', intent, wasSpeaking)
          c.closeCard(false)
          c.stream.pause()
          c.requestRunCode()
          void say(line, intent).then(carryOn)
          return
        }
        case 'show_diagram': {
          c.closeCard(false)
          c.stream.pause()
          c.requestShowDiagram()
          void say(line, intent).then(carryOn)
          return
        }
      }
    },
    [say, problem, playAfter, announce],
  )

  const ackPlaying = useCallback(() => ackVoice.current !== null, [])

  return { execute, ack, ackVisible, announce, stopAck, ackPlaying, lastAckText }
}
