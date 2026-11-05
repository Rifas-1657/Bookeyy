'use client'

import { Award, ChevronLeft, ChevronRight } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle } from '@/components/ui/drawer'
import { notify } from '@/components/ui-kit/toast'
import { isBackendConfigured } from '@/lib/api/auth'
import { findPageIndex, pageInChapter } from '@/lib/mock/book-pages'
import { setSpeechHints } from '@/lib/api/client'
import { warmNarration } from '@/lib/narration/warm'
import type { LearningChapter, LearningCourseId } from '@/lib/mock/learning'
import type { LibraryBookmark, LibraryNote, PageRef } from '@/lib/mock/library'
import { samePage, useBookmarks, useNotes } from '@/lib/stores/library'
import { rehydrateReaderPreferences, useReaderPreferences } from '@/lib/stores/reader-preferences'
import { rehydrateReaderSession, useReaderSession } from '@/lib/stores/reader-session'
import { cn } from '@/lib/utils'
import type { BookLessonPage, ReaderViewMode } from '@/types/book'
import { BookSpread, type PageTurn } from './book-spread'
import { BookIntro } from './intro/book-intro'
import { BookMissing, BookSpreadSkeleton } from './book-states'
import { BookTopBar } from './book-top-bar'
import { ChapterRail } from './chapter-rail'
import { ReaderSidePanel, type SidePanelTab } from './reader-side-panel'
import { useArrowKeyNavigation, useFullscreen, useMediaQuery, useOnlineStatus, useSwipe } from './use-reader-hooks'
import { NarrationDevPanel } from './narration-dev-panel'
import { VoiceDock } from './voice-dock'
import type { QueuedQuestion } from './tutor/any-questions-panel'
import { CourseChatPanel } from './tutor/course-chat-panel'
import { useCourseTutor } from './tutor/use-course-tutor'
import { rehydrateCourseChat } from '@/lib/stores/course-chat'
import { usePersonalization } from '@/lib/stores/personalization'
import { markPageComplete } from '@/lib/stores/progress'
import { deriveCueVisuals } from '@/lib/narration/cues'
import { useNarrationStream } from '@/lib/narration/use-narration-stream'
import type { NarrationCueEvent } from '@/types/book'
import { getOfflineBook } from '@/lib/offline/db'
import { prefetchSpeech, speakAnswer, type SpokenAnswer } from '@/lib/narration/speak'
import { LessonRecap } from './lesson-recap'
import { OfflineControl } from './offline-control'
import { TutorCard } from './tutor/tutor-card'
import { GradedQuiz, type QuizMode } from './tutor/graded-quiz'
import { LearningOffer } from './tutor/learning-offer'
import { recordEvidence, useLearnerSummary } from '@/lib/learning'
import type { TutorPresenceState } from './tutor/tutor-orb'
import { useVoiceQuestion } from './tutor/use-voice-question'
import { useHandsFree } from './tutor/use-hands-free'
import { useLessonCommands } from './use-lesson-commands'
import {
  isConfident,
  isLikelyEcho,
  isNoiseTranscript,
  matchIntent,
  normalizeUtterance,
  resolveIntent,
  type VoiceIntent,
} from '@/lib/voice-commands'

export interface BookReaderCourse {
  id: LearningCourseId
  title: string
  chapters: LearningChapter[]
}

interface BookReaderProps {
  course: BookReaderCourse
  pages: BookLessonPage[]
  /** Deep link from `?chapter=&page=`; overrides the saved position. */
  initialChapter?: number
  initialPage?: number
}

const navButton =
  'inline-flex h-11 items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 text-sm font-medium backdrop-blur-md transition-colors hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-35 sm:px-4'

export function BookReader(props: BookReaderProps) {
  const [offlinePages, setOfflinePages] = useState<BookLessonPage[] | null>(null)
  const needsOffline = props.pages.length === 0
  useEffect(() => {
    if (!needsOffline) return
    // Network/server gave no pages: read the book saved for offline, if any.
    void getOfflineBook(props.course.id).then((book) => {
      if (book?.pages.length) setOfflinePages(book.pages)
    })
  }, [needsOffline, props.course.id])
  return <BookReaderInner {...props} pages={needsOffline && offlinePages ? offlinePages : props.pages} />
}

function BookReaderInner({ course, pages, initialChapter, initialPage }: BookReaderProps) {
  const deepLinkIndex = initialChapter ? findPageIndex(pages, initialChapter, initialPage ?? 1) : -1
  const deepLinkMissing = initialChapter !== undefined && deepLinkIndex === -1

  const hydrated = useReaderSession((s) => s.hydrated)
  const savedPageId = useReaderSession((s) => s.lastPage[course.id])
  const visited = useReaderSession((s) => s.visited[course.id]) ?? []
  const storedViewMode = useReaderSession((s) => s.viewMode)
  const viewModeChosen = useReaderSession((s) => s.viewModeChosen)
  const railOpen = useReaderSession((s) => s.railOpen)
  const { setLastPage, setViewMode, setRailOpen } = useReaderSession.getState()
  const fontScale = useReaderPreferences((s) => s.fontScale)
  const setFontScale = useReaderPreferences((s) => s.setFontScale)

  const [chosenIndex, setChosenIndex] = useState<number | null>(deepLinkIndex >= 0 ? deepLinkIndex : null)
  const [panelOpen, setPanelOpen] = useState(false)
  const [panelTab, setPanelTab] = useState<SidePanelTab>('notes')
  const [railDrawerOpen, setRailDrawerOpen] = useState(false)

  const isDesktop = useMediaQuery('(min-width: 1024px)')
  const isWide = useMediaQuery('(min-width: 1280px)')
  const online = useOnlineStatus()
  const fullscreen = useFullscreen<HTMLDivElement>()
  const bookmarksApi = useBookmarks()
  const notesApi = useNotes()

  useEffect(() => {
    rehydrateReaderSession()
    rehydrateReaderPreferences()
    rehydrateCourseChat()
  }, [])

  const [chatOpen, setChatOpen] = useState(false)
  const [queueState, setQueueState] = useState<{ pageId: string; items: QueuedQuestion[] }>({ pageId: '', items: [] })
  const { personalization, completed: surveyCompleted } = usePersonalization(course.id)
  // Guided by default: the page fills in as Nila speaks instead of showing everything up front.
  const viewMode: ReaderViewMode = viewModeChosen ? storedViewMode : 'guided'

  const savedIndex = pages.findIndex((entry) => entry.id === savedPageId)
  const index = chosenIndex ?? (hydrated ? Math.max(savedIndex, 0) : null)
  const page = index !== null ? pages[index] : undefined
  const nextPageId = index !== null ? pages[index + 1]?.id : undefined

  useEffect(() => {
    setSpeechHints({
      language: personalization.language === 'english' ? 'en' : undefined,
      context: page ? [page.title, ...(page.keyTerms ?? [])].join(', ') : undefined,
    })
    return () => setSpeechHints({})
  }, [page, personalization.language])

  useEffect(() => {
    if (page) warmNarration(course.id, page.id)
    if (nextPageId) warmNarration(course.id, nextPageId)
  }, [course.id, page?.id, nextPageId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (hydrated && page) setLastPage(course.id, page.id)
  }, [hydrated, page, course.id, setLastPage])

  const [introDone, setIntroDone] = useState(false)
  const finishIntro = useCallback(() => setIntroDone(true), [])

  const reducedMotion = useMediaQuery('(prefers-reduced-motion: reduce)')
  const [turn, setTurn] = useState<PageTurn | null>(null)
  const turningRef = useRef(false)
  const turnIdRef = useRef(0)
  const surfaceRef = useRef<HTMLDivElement>(null)
  const restoreFocusRef = useRef(false)

  const endTurn = useCallback(() => {
    turningRef.current = false
    setTurn(null)
  }, [])

  useEffect(() => {
    endTurn()
  }, [course.id, endTurn])

  useEffect(() => {
    if (!turn) return
    const safety = window.setTimeout(endTurn, 1200)
    return () => window.clearTimeout(safety)
  }, [turn, endTurn])

  const goTo = useCallback(
    (next: number, options?: { animate?: boolean }) => {
      if (next < 0 || next >= pages.length || next === index) return
      const animate = options?.animate ?? false
      if (animate && turningRef.current) return
      if (surfaceRef.current?.contains(document.activeElement)) restoreFocusRef.current = true
      if (animate && !reducedMotion && index !== null) {
        turningRef.current = true
        turnIdRef.current += 1
        setTurn({ id: turnIdRef.current, pageId: pages[next].id, direction: next > index ? 'next' : 'prev' })
      } else {
        endTurn()
      }
      setChosenIndex(next)
    },
    [pages, index, reducedMotion, endTurn],
  )
  const goPrevious = useCallback(() => index !== null && goTo(index - 1, { animate: true }), [index, goTo])
  const goNext = useCallback(() => {
    if (index === null) return false
    // Moving forward past a page counts as finishing it.
    if (page) {
      markPageComplete(course.id, page.id)
      recordEvidence(course.id, page, 'page_complete')
    }
    return goTo(index + 1, { animate: true })
  }, [index, page, course.id, goTo])

  useEffect(() => {
    if (!restoreFocusRef.current) return
    restoreFocusRef.current = false
    const active = document.activeElement
    if (!active || active === document.body || !active.isConnected) surfaceRef.current?.focus({ preventScroll: true })
  }, [index])

  const narrationRate = useReaderPreferences((s) => s.narrationRate)
  const narrationMuted = useReaderPreferences((s) => s.narrationMuted)
  const setNarrationRate = useReaderPreferences((s) => s.setNarrationRate)
  const setNarrationMuted = useReaderPreferences((s) => s.setNarrationMuted)
  const streamingEnabled = viewMode === 'guided'
  const [cueLog, setCueLog] = useState<NarrationCueEvent[]>([])
  const stream = useNarrationStream({
    courseId: course.id,
    page,
    enabled: streamingEnabled,
    rate: narrationRate,
    muted: narrationMuted,
    language: personalization.language,
    onCue:
      process.env.NODE_ENV === 'development' ? (cue) => setCueLog((log) => [...log, cue].slice(-30)) : undefined,
  })
  const cueVisuals = streamingEnabled ? deriveCueVisuals(stream.reachedCues, page) : undefined

  const audioStatus: 'idle' | 'speaking' | 'paused' | 'loading' | 'error' =
    stream.status === 'speaking'
      ? 'speaking'
      : stream.status === 'paused'
        ? 'paused'
        : 'idle'

  useEffect(() => {
    if (stream.status === 'completed' && page) {
      markPageComplete(course.id, page.id)
      recordEvidence(course.id, page, 'page_complete')
    }
  }, [stream.status, page, course.id])

  const handleToggleListen = useCallback(() => {
    if (viewMode !== 'guided') {
      setViewMode('guided')
    }
    if (stream.status === 'completed') {
      // Listening to the whole page again: a (weak) sign it did not fully land.
      if (page) recordEvidence(course.id, page, 'relisten')
      stream.restart()
    } else {
      stream.toggle()
    }
  }, [viewMode, setViewMode, stream, page, course.id])

  const handleStopListen = useCallback(() => {
    stream.stop()
  }, [stream])

  const tutor = useCourseTutor({
    courseId: course.id,
    courseTitle: course.title,
    page,
    pageIndex: index ?? 0,
    language: personalization.language,
    reducedMotion,
  })
  const queue = page && queueState.pageId === page.id ? queueState.items : []

  /* ------------------------- interruptions & answers ------------------------- */

  const voice = useVoiceQuestion()
  const [card, setCard] = useState<{ open: boolean; mode: 'listening' | 'typing' | 'answer' | 'quiz'; resume: boolean }>({
    open: false,
    mode: 'typing',
    resume: false,
  })
  const [answerSpeaking, setAnswerSpeaking] = useState(false)
  const spokenRef = useRef<SpokenAnswer | null>(null)
  const spokenForRef = useRef<string | null>(null)
  const [tryCode, setTryCode] = useState<{ id: number; code: string } | null>(null)

  const stopAnswerVoice = useCallback(() => {
    spokenRef.current?.stop()
    spokenRef.current = null
    setAnswerSpeaking(false)
  }, [])

  const openCard = useCallback(
    (mode: 'listening' | 'typing') => {
      if (!page) return
      if (viewMode !== 'guided') setViewMode('guided')
      stopAnswerVoice()
      const wasSpeaking = stream.interrupt()
      setCard((current) => ({ open: true, mode, resume: wasSpeaking || (current.open && current.resume) }))
      if (mode === 'listening') {
        void voice.start().then((ok) => {
          if (!ok) setCard((current) => ({ ...current, mode: 'typing' }))
        })
      }
    },
    [page, viewMode, setViewMode, stopAnswerVoice, stream, voice],
  )

  const submitQuestion = useCallback(
    (text: string, source: 'voice' | 'text') => {
      const sent = tutor.ask(text, source)
      if (sent) setCard((current) => ({ ...current, mode: 'answer' }))
      else toast('One moment — I’m still answering your last question.')
    },
    [tutor],
  )

  /* --------------------------- voice/text commands --------------------------- */

  const [runRequest, setRunRequest] = useState(0)
  const [diagramRequest, setDiagramRequest] = useState(0)
  const [understanding, setUnderstanding] = useState(false)

  /** Closes the tutor card for a command; `resume` continues an interrupted narration. */
  const dismissCard = useCallback(
    (resume: boolean) => {
      stopAnswerVoice()
      voice.cancel()
      setQuiz(null)
      setCard((current) => {
        if (!current.open) return current
        if (resume && current.resume) stream.resumeAfterInterrupt()
        return { open: false, mode: 'typing', resume: false }
      })
    },
    [stopAnswerVoice, voice, stream],
  )

  /** Card in answer mode for a tutor reply the learner didn't type (quiz, simpler, hands-free question). */
  const openAnswerCard = useCallback(() => {
    if (viewMode !== 'guided') setViewMode('guided')
    const wasSpeaking = stream.interrupt()
    setCard((current) => ({ open: true, mode: 'answer', resume: wasSpeaking || (current.open && current.resume) }))
  }, [viewMode, setViewMode, stream])

  /* ------------------------- R3: graded quizzes & review ------------------------- */

  const [quiz, setQuiz] = useState<{ mode: QuizMode; key: number } | null>(null)
  const startQuiz = useCallback(
    (mode: QuizMode) => {
      if (!page) return
      stopAnswerVoice()
      voice.cancel()
      const wasSpeaking = stream.interrupt()
      setQuiz({ mode, key: Date.now() })
      setCard((current) => ({ open: true, mode: 'quiz', resume: wasSpeaking || (current.open && current.resume) }))
    },
    [page, stopAnswerVoice, voice, stream],
  )

  const commands = useLessonCommands({
    page,
    pages,
    index,
    language: personalization.language,
    rate: narrationRate,
    setRate: setNarrationRate,
    muted: narrationMuted,
    guided: viewMode === 'guided',
    ensureGuided: () => {
      if (viewMode !== 'guided') setViewMode('guided')
    },
    stream,
    tutor,
    goTo,
    goNext,
    goPrevious,
    wasSpeaking: () => stream.status === 'speaking' || (card.open && card.resume),
    closeCard: dismissCard,
    openAnswerCard,
    stopAnswerVoice,
    requestRunCode: () => setRunRequest((n) => n + 1),
    requestShowDiagram: () => setDiagramRequest((n) => n + 1),
    startQuiz: () => startQuiz('page'),
    recordEvidence: (kind) => {
      if (page) recordEvidence(course.id, page, kind)
    },
  })

  /** Speaks a quiz line in the teacher's voice (shares the answer voice, so "stop voice" works). */
  const speakQuizLine = useCallback(
    (text: string) => {
      spokenRef.current?.stop()
      commands.stopAck()
      const spoken = speakAnswer(text, { lang: personalization.language, rate: narrationRate, muted: narrationMuted })
      spokenRef.current = spoken
      setAnswerSpeaking(spoken.audible)
      return spoken.done.then(() => {
        if (spokenRef.current !== spoken) return
        spokenRef.current = null
        setAnswerSpeaking(false)
      })
    },
    [commands, personalization.language, narrationRate, narrationMuted],
  )

  // Offers: a spaced review at the start of the lesson, a quick check when a page is finished.
  const learner = useLearnerSummary(course.id, (n) => course.chapters.find((c) => c.number === n)?.title ?? `Chapter ${n}`)
  const dueCount = learner.data?.dueReviews.length ?? 0
  const [reviewOffer, setReviewOffer] = useState<'pending' | 'shown' | 'done'>('pending')
  useEffect(() => {
    if (reviewOffer === 'pending' && introDone && dueCount > 0) setReviewOffer('shown')
  }, [reviewOffer, introDone, dueCount])
  const [checkOfferFor, setCheckOfferFor] = useState<string | null>(null)
  const checkedPages = useRef(new Set<string>())
  useEffect(() => {
    if (stream.status !== 'completed' || !page || !streamingEnabled || checkedPages.current.has(page.id)) return
    checkedPages.current.add(page.id)
    const timer = window.setTimeout(() => setCheckOfferFor(page.id), 900)
    return () => window.clearTimeout(timer)
  }, [stream.status, page, streamingEnabled])

  /** Command or question? Clear commands act at once; unclear ones may ask the backend first. */
  const routeUtterance = useCallback(
    async (text: string, source: 'voice' | 'text') => {
      const sure = isConfident(matchIntent(text))
      if (!sure) setUnderstanding(true)
      const match = await resolveIntent(text, { lang: personalization.language, pageTitle: page?.title })
      if (!sure) setUnderstanding(false)
      if (match.intent === 'question') submitQuestion(text, source)
      else commands.execute(match, source)
    },
    [personalization.language, page?.title, submitQuestion, commands],
  )

  const finishListening = useCallback(() => {
    voice.stop((text) => {
      if (text.trim()) void routeUtterance(text, 'voice')
      else setCard((current) => ({ ...current, mode: 'typing' }))
    })
  }, [voice, routeUtterance])

  const closeCard = useCallback(() => {
    stopAnswerVoice()
    voice.cancel()
    tutor.dismissLatest()
    setQuiz(null)
    setCard((current) => {
      if (current.resume) stream.resumeAfterInterrupt()
      return { open: false, mode: 'typing', resume: false }
    })
  }, [stopAnswerVoice, voice, tutor, stream])

  // Speak the answer once it has fully streamed in, then carry on with the lesson.
  const latest = tutor.latest
  useEffect(() => {
    if (card.open && card.mode === 'answer' && latest?.text && !latest.final && !narrationMuted) {
      prefetchSpeech(latest.text, personalization.language)
    }
  }, [card.open, card.mode, latest?.text, latest?.final, narrationMuted, personalization.language])
  useEffect(() => {
    if (!card.open || card.mode !== 'answer' || !latest?.final || spokenForRef.current === latest.questionId) return
    spokenForRef.current = latest.questionId
    commands.stopAck()
    const spoken = speakAnswer(latest.text, { lang: personalization.language, rate: narrationRate, muted: narrationMuted })
    spokenRef.current = spoken
    setAnswerSpeaking(spoken.audible)
    let resumeTimer = 0
    void spoken.done.then(() => {
      if (spokenRef.current !== spoken) return
      spokenRef.current = null
      setAnswerSpeaking(false)
      // A plain answer hands back to the lesson; diagrams/code stay up until the learner closes the note.
      const hasExtras = Boolean(latest.code || latest.diagramSpec || latest.diagram?.length)
      if (!hasExtras && !latest.offTopic) {
        resumeTimer = window.setTimeout(() => {
          setCard((current) => {
            if (current.resume) stream.resumeAfterInterrupt()
            return { ...current, resume: false }
          })
        }, 1400)
      }
    })
    return () => window.clearTimeout(resumeTimer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [card.open, card.mode, latest?.final, latest?.questionId])

  // A new page closes any open note (and drops a barge-in's ducking).
  useEffect(() => {
    stopAnswerVoice()
    setQuiz(null)
    setCheckOfferFor(null)
    setCard({ open: false, mode: 'typing', resume: false })
    stream.duck(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page?.id])

  const presence: TutorPresenceState =
    voice.state === 'listening' || (card.open && card.mode === 'listening')
      ? 'listening'
      : (card.open && card.mode === 'answer' && !latest?.final) || stream.buffering
        ? 'thinking'
        : answerSpeaking || stream.status === 'speaking'
          ? 'speaking'
          : 'idle'
  const presenceLevel = useCallback(
    () => (spokenRef.current ? spokenRef.current.getLevel() : stream.getLevel()),
    [stream],
  )

  /* -------------------------------- hands-free -------------------------------- */

  const [handsFreeOn, setHandsFreeOn] = useState(false)
  const lastPlaybackAt = useRef(0)
  const echoLog = useRef<number[]>([])

  const handleHandsFreeUtterance = async (text: string) => {
    if (isNoiseTranscript(text)) {
      stream.duck(false)
      return
    }
    // Echo guard: drop what is mostly our own recent voice coming back through the speakers.
    if (Date.now() - lastPlaybackAt.current < 4000) {
      const sentence = stream.currentSentence
      const recent = [
        sentence?.text ?? '',
        sentence ? (stream.sentences[sentence.index - 1]?.text ?? '') : '',
        commands.lastAckText.current,
        latest?.text ?? '',
      ]
      if (isLikelyEcho(text, recent)) {
        stream.duck(false)
        const now = Date.now()
        echoLog.current = [...echoLog.current.filter((t) => now - t < 30_000), now]
        if (echoLog.current.length >= 3) {
          echoLog.current = []
          setHandsFreeOn(false)
          commands.announce('Hands-free turned off: the speakers echo into the microphone.')
          toast('Hands-free turned off', {
            description: 'Your speakers were echoing into the microphone. Use headphones, or tap the mic to talk.',
          })
        }
        return
      }
    }
    const match = await resolveIntent(text, { lang: personalization.language, pageTitle: page?.title })
    if (match.intent !== 'question') {
      commands.execute(match, 'handsfree')
      return
    }
    // Background chatter rarely makes a whole sentence; one stray word is ignored.
    if (normalizeUtterance(text).split(' ').filter(Boolean).length < 2) {
      stream.duck(false)
      return
    }
    stopAnswerVoice()
    if (!tutor.ask(text, 'voice')) {
      stream.duck(false)
      toast('One moment — I’m still answering your last question.')
      return
    }
    openAnswerCard()
  }

  const handsFree = useHandsFree({
    enabled: handsFreeOn && Boolean(page),
    // Push-to-talk / typing owns the conversation meanwhile.
    paused:
      (card.open && card.mode !== 'answer') ||
      voice.state === 'listening' ||
      voice.state === 'transcribing' ||
      understanding,
    isPlaybackActive: () => {
      const active = stream.status === 'speaking' || spokenRef.current !== null || commands.ackPlaying()
      if (active) lastPlaybackAt.current = Date.now()
      return active
    },
    onSpeechStart: () => stream.duck(true),
    onSpeechCancel: () => stream.duck(false),
    onUtterance: (text) => void handleHandsFreeUtterance(text),
    onStopped: (reason) => {
      setHandsFreeOn(false)
      stream.duck(false)
      if (reason === 'error') {
        toast('Hands-free is unavailable', { description: 'The microphone could not be opened. Tap the mic to ask instead.' })
        commands.announce('Hands-free is unavailable. Tap the mic to ask instead.')
      }
    },
  })

  const toggleHandsFree = useCallback(() => {
    const next = !handsFreeOn
    echoLog.current = []
    if (!next) stream.duck(false)
    setHandsFreeOn(next)
    commands.announce(
      next
        ? personalization.language === 'english'
          ? 'Hands-free on. Say stop, next page, repeat, or ask a question.'
          : 'Hands-free on. “Niruthu”, “aduththa page”, “thirumba sollu” nu sollunga, illa kelvi kelunga.'
        : 'Hands-free off.',
    )
  }, [handsFreeOn, stream, commands, personalization.language])

  const runVisibleCommand = useCallback(
    (intent: VoiceIntent) => commands.execute({ intent, args: {} }, 'button'),
    [commands],
  )

  /* ------------------------------- lesson recap ------------------------------- */

  const [recapChapter, setRecapChapter] = useState<number | null>(null)
  const chapterPages = useCallback((n: number) => pages.filter((entry) => entry.chapterNumber === n), [pages])
  const isLastOfChapter = Boolean(page && chapterPages(page.chapterNumber).at(-1)?.id === page.id)
  useEffect(() => {
    if (stream.status === 'completed' && page && isLastOfChapter && streamingEnabled) {
      const timer = window.setTimeout(() => setRecapChapter(page.chapterNumber), 1600)
      return () => window.clearTimeout(timer)
    }
  }, [stream.status, page, isLastOfChapter, streamingEnabled])

  const queueQuestion = useCallback(
    (kind: 'ask' | 'mic') => {
      if (!page) return
      const sentence = stream.currentSentence?.text ?? page.title
      const excerpt = sentence.length > 48 ? `${sentence.slice(0, 47).trimEnd()}…` : sentence
      const prompt =
        kind === 'mic' ? `Innoru example la sollunga: "${excerpt}"` : `Explain this part again: "${excerpt}"`
      setQueueState((prev) => {
        const items = prev.pageId === page.id ? prev.items : []
        return { pageId: page.id, items: [...items, { id: `${Date.now()}-${items.length}`, kind, excerpt, prompt }].slice(-5) }
      })
      toast('I’ll queue that question for the end of this section', {
        description: kind === 'mic' ? 'Voice question' : 'Narration keeps playing.',
      })
    },
    [page, stream.currentSentence],
  )

  const askQueued = useCallback(
    (item: QueuedQuestion) => {
      if (!tutor.ask(item.prompt)) return
      setQueueState((prev) => ({ ...prev, items: prev.items.filter((entry) => entry.id !== item.id) }))
    },
    [tutor],
  )

  const overlayOpen = railDrawerOpen || ((panelOpen || chatOpen) && !isWide)
  useArrowKeyNavigation(goPrevious, goNext, Boolean(page) && !overlayOpen && introDone)
  const swipeHandlers = useSwipe(goPrevious, goNext)

  if (pages.length === 0) {
    return (
      <ReaderFrame>
        <BookMissing
          title="This book isn’t ready yet"
          description={`${course.title} doesn’t have pages yet.`}
          primary={{ href: '/book/python', label: 'Open the Python book' }}
          secondary={{ href: `/courses/${course.id}`, label: 'Back to course' }}
        />
      </ReaderFrame>
    )
  }

  const chapter = page ? course.chapters.find((entry) => entry.number === page.chapterNumber) : undefined
  const chapterLabel = chapter ? `Chapter ${chapter.number} · ${chapter.title}` : 'Loading…'
  const currentRef: PageRef | null = page
    ? { courseId: course.id, chapter: page.chapterNumber, page: pageInChapter(pages, page) }
    : null
  const bookmarked = currentRef ? bookmarksApi.bookmarks.some((item) => samePage(item, currentRef)) : false
  const showMissing = deepLinkMissing && chosenIndex === null

  function toggleRail() {
    if (isDesktop) setRailOpen(!railOpen)
    else setRailDrawerOpen((open) => !open)
  }

  function openPanel(tab: SidePanelTab) {
    if (panelOpen && panelTab === tab) {
      setPanelOpen(false)
      return
    }
    setPanelTab(tab)
    setPanelOpen(true)
    setChatOpen(false)
  }

  function openChat() {
    setChatOpen(true)
    setPanelOpen(false)
  }

  function openSourcePage(pageIndex: number) {
    goTo(pageIndex)
    if (!isWide) setChatOpen(false)
  }

  function toggleBookmark() {
    if (!page || !currentRef) return
    bookmarksApi.toggle(currentRef, page.paragraphs[0] ?? page.title, page.id)
    notify.success(
      bookmarked ? 'Bookmark removed' : 'Page bookmarked',
      isBackendConfigured() ? 'Synced to your account.' : 'Saved in this browser only.',
    )
  }

  function selectPage(next: number) {
    goTo(next)
    setRailDrawerOpen(false)
  }

  function jumpTo(ref: PageRef) {
    if (ref.courseId !== course.id) return false
    const target = findPageIndex(pages, ref.chapter, ref.page)
    if (target === -1) return false
    goTo(target)
    if (!isWide) setPanelOpen(false)
    return true
  }

  function removeNote(note: LibraryNote) {
    const position = notesApi.notes.findIndex((item) => item.id === note.id)
    notesApi.remove(note.id)
    toast('Note deleted', { action: { label: 'Undo', onClick: () => notesApi.restore(note, position) } })
  }

  function removeBookmark(bookmark: LibraryBookmark) {
    const position = bookmarksApi.bookmarks.findIndex((item) => item.id === bookmark.id)
    bookmarksApi.remove(bookmark.id)
    toast('Bookmark removed', { action: { label: 'Undo', onClick: () => bookmarksApi.restore(bookmark, position) } })
  }

  const rail = (
    <ChapterRail
      chapters={course.chapters}
      pages={pages}
      currentIndex={index ?? -1}
      visited={visited}
      onSelectPage={selectPage}
    />
  )

  const sidePanel = currentRef && page && (
    <ReaderSidePanel
      tab={panelTab}
      onTabChange={setPanelTab}
      currentRef={currentRef}
      currentTitle={page.title}
      notes={notesApi.notes}
      bookmarks={bookmarksApi.bookmarks}
      maxNoteLength={notesApi.maxLength}
      onAddNote={(text) => notesApi.add(currentRef, text, undefined, page.id)}
      onEditNote={notesApi.edit}
      onRemoveNote={removeNote}
      onRemoveBookmark={removeBookmark}
      onJump={jumpTo}
    />
  )

  const firstChapter = course.chapters[0]
  const introSubtitle = `${course.chapters.length} chapters${firstChapter ? ` · begins with ${firstChapter.title}` : ''}`

  return (
    <>
    <ReaderFrame frameRef={fullscreen.ref} inert={!introDone}>
      <BookTopBar
        courseId={course.id}
        courseTitle={course.title}
        chapterLabel={chapterLabel}
        pageNumber={index !== null ? index + 1 : null}
        totalPages={pages.length}
        online={online}
        railOpen={isDesktop ? railOpen : railDrawerOpen}
        onToggleRail={toggleRail}
        bookmarked={bookmarked}
        onToggleBookmark={toggleBookmark}
        notesOpen={panelOpen}
        onToggleNotes={() => openPanel('notes')}
        chatOpen={chatOpen}
        onToggleChat={() => (chatOpen ? setChatOpen(false) : openChat())}
        viewMode={viewMode}
        onViewModeChange={setViewMode}
        fontScale={fontScale}
        onFontScaleChange={setFontScale}
        fullscreen={fullscreen}
        disabled={!page}
        audioStatus={audioStatus}
        onToggleListen={handleToggleListen}
        onStopListen={handleStopListen}
        playbackRate={narrationRate}
        onRateChange={setNarrationRate}
        offlineSlot={
          <>
            {page && (
              <button
                type="button"
                onClick={() => setRecapChapter(recapChapter === null ? page.chapterNumber : null)}
                aria-pressed={recapChapter !== null}
                aria-label="Lesson recap"
                className="inline-flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-2.5 text-xs font-medium backdrop-blur-md transition-colors hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none aria-pressed:border-primary/50 aria-pressed:bg-primary/20"
              >
                <Award className="size-3.5" aria-hidden="true" />
                <span className="hidden sm:inline">Recap</span>
              </button>
            )}
            <OfflineControl course={course} pages={pages} compact />
          </>
        }
      />

      <div className="relative z-10 flex min-h-0 flex-1">
        {isDesktop && railOpen && (
          <aside
            aria-label="Chapter navigation"
            className="w-64 shrink-0 overflow-y-auto border-r border-white/10 bg-black/15 p-3 backdrop-blur-md"
          >
            <p className="px-3 pt-1 pb-3 text-xs font-semibold tracking-[0.16em] text-muted-foreground uppercase">
              Contents
            </p>
            {rail}
          </aside>
        )}

        <main className="flex min-w-0 flex-1 flex-col gap-3 px-3 pt-4 pb-3 sm:px-6 lg:px-8 lg:pt-6">
          <div className="mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col">
            {showMissing ? (
              <BookMissing
                title="That page isn’t ready yet"
                description={`Chapter ${initialChapter}${initialPage ? `, page ${initialPage}` : ''} hasn’t been written for this bomo. You can keep reading from the seeded pages.`}
                primary={{ href: `/book/${course.id}`, label: 'Continue reading' }}
                secondary={{ href: `/courses/${course.id}`, label: 'Back to course' }}
              />
            ) : page && recapChapter !== null ? (
              <LessonRecap
                courseId={course.id}
                chapterNumber={recapChapter}
                chapterTitle={course.chapters.find((c) => c.number === recapChapter)?.title ?? ''}
                pages={chapterPages(recapChapter)}
                hasNextChapter={pages.some((entry) => entry.chapterNumber === recapChapter + 1)}
                onBack={() => setRecapChapter(null)}
                onNextChapter={() => {
                  const next = pages.findIndex((entry) => entry.chapterNumber === recapChapter + 1)
                  setRecapChapter(null)
                  if (next >= 0) goTo(next, { animate: true })
                }}
                offlineSlot={<OfflineControl course={course} pages={pages} />}
              />
            ) : page ? (
              <div ref={surfaceRef} tabIndex={-1} aria-label="Book pages" className="flex min-h-0 flex-1 flex-col outline-none">
              <BookSpread
                courseId={course.id}
                turn={turn}
                onTurnEnd={endTurn}
                page={page}
                pageLabel={`Page ${(index ?? 0) + 1} of ${pages.length}`}
                chapterTitle={chapterLabel}
                viewMode={viewMode}
                fontScale={fontScale}
                interests={personalization.interests}
                swipeHandlers={swipeHandlers}
                narration={{
                  words: stream.words,
                  activeIndex: stream.wordIndex,
                  // Guided pages start blank and fill in as Nila speaks.
                  streaming: streamingEnabled,
                  // No voice for this page: don't hold the text back behind a narration that can't be heard.
                  revealAll: stream.audioFailed,
                }}
                cueVisuals={cueVisuals}
                diagramProgress={streamingEnabled ? Math.max(0, stream.progress) : null}
                tryCode={tryCode}
                runRequest={runRequest}
                diagramRequest={diagramRequest}
                overlay={
                  <>
                  {commands.ack && commands.ackVisible && (
                    <p
                      aria-hidden="true"
                      key={commands.ack.id}
                      lang={personalization.language === 'english' ? 'en' : 'ta-Latn'}
                      className="pointer-events-none absolute top-3 left-1/2 z-40 max-w-[90%] -translate-x-1/2 truncate rounded-full bg-book-ink/85 px-3.5 py-1.5 font-sans text-sm font-medium text-book-page shadow-lg animate-in fade-in zoom-in-95"
                    >
                      {commands.ack.text}
                    </p>
                  )}
                  <TutorCard
                    open={card.open}
                    mode={card.mode}
                    voiceState={voice.state}
                    interim={voice.interim}
                    voiceError={voice.error}
                    latest={latest}
                    presence={presence}
                    getLevel={presenceLevel}
                    busy={tutor.busy}
                    speaking={answerSpeaking}
                    onSubmitText={(text) => void routeUtterance(text, 'text')}
                    understanding={understanding}
                    onStopListening={finishListening}
                    onStartListening={() => openCard('listening')}
                    onStopSpeaking={stopAnswerVoice}
                    onTryCode={(code) => {
                      stopAnswerVoice()
                      setTryCode({ id: Date.now(), code })
                    }}
                    onClose={closeCard}
                    willResume={card.resume}
                    quizSlot={
                      quiz && page ? (
                        <GradedQuiz
                          key={quiz.key}
                          courseId={course.id}
                          page={page}
                          pages={pages}
                          language={personalization.language}
                          mode={quiz.mode}
                          speak={speakQuizLine}
                          stopSpeaking={stopAnswerVoice}
                          onClose={closeCard}
                        />
                      ) : null
                    }
                  />
                  {!card.open && reviewOffer === 'shown' && (
                    <LearningOffer
                      kind="review"
                      text={
                        personalization.language === 'english'
                          ? `Quick 1-minute review of ${dueCount === 1 ? 'an idea' : `${dueCount} ideas`} you missed earlier?`
                          : `Munnaadi miss panna ${dueCount === 1 ? 'oru point' : `${dueCount} points`}-a 1-minute quick review pannalaama?`
                      }
                      acceptLabel={personalization.language === 'english' ? 'Start review' : 'Start pannunga'}
                      declineLabel="Not now"
                      onAccept={() => {
                        setReviewOffer('done')
                        startQuiz('review')
                      }}
                      onDecline={() => setReviewOffer('done')}
                    />
                  )}
                  {!card.open && reviewOffer !== 'shown' && page && checkOfferFor === page.id && (
                    <LearningOffer
                      kind="check"
                      text={
                        personalization.language === 'english'
                          ? 'Quick 3-question check before you move on?'
                          : 'Move panradhukku munnaadi oru quick check?'
                      }
                      acceptLabel={personalization.language === 'english' ? 'Check me' : 'Check pannunga'}
                      declineLabel="Skip the check"
                      onAccept={() => {
                        setCheckOfferFor(null)
                        startQuiz('check')
                      }}
                      onDecline={() => setCheckOfferFor(null)}
                    />
                  )}
                  </>
                }
              />
              </div>
            ) : (
              <BookSpreadSkeleton />
            )}
          </div>

          {stream.autoplayBlocked && stream.status !== 'speaking' && (
            <p
              role="status"
              className="mx-auto rounded-full border border-book-orange/40 bg-book-marker/15 px-3 py-1 text-xs font-medium"
            >
              Tap play to start
            </p>
          )}

          <nav
            aria-label="Page navigation"
            className="mx-auto flex w-full max-w-6xl flex-wrap items-end justify-between gap-x-3 gap-y-2 sm:flex-nowrap"
          >
            <button
              type="button"
              className={cn(navButton, 'sm:mb-0.5')}
              onClick={goPrevious}
              disabled={!page || index === 0 || showMissing}
            >
              <ChevronLeft className="size-4" aria-hidden="true" />
              <span className="hidden sm:inline">Previous</span>
              <span className="sr-only sm:hidden">Previous page</span>
            </button>

            <div className="order-first flex min-w-0 basis-full justify-center sm:order-none sm:flex-1 sm:basis-auto">
              {page && !showMissing ? (
                <VoiceDock
                  key={page.id}
                  page={page}
                  streamingEnabled={streamingEnabled}
                  stream={stream}
                  rate={narrationRate}
                  onRateChange={setNarrationRate}
                  muted={narrationMuted}
                  onMutedChange={setNarrationMuted}
                  onEnableGuided={() => setViewMode('guided')}
                  hasNextPage={index !== null && index < pages.length - 1}
                  onNextPage={() => {
                    stream.queueAutoplay()
                    goNext()
                  }}
                  queue={queue}
                  onQueueQuestion={queueQuestion}
                  tutorBusy={tutor.busy}
                  onAskTutor={tutor.ask}
                  onAskQueued={askQueued}
                  onOpenChat={openChat}
                  onBargeIn={() => (voice.state === 'listening' ? finishListening() : openCard('listening'))}
                  onAskInline={() => openCard('typing')}
                  presence={presence}
                  handsFree={{ on: handsFreeOn, state: handsFree.state, onToggle: toggleHandsFree }}
                  onCommand={runVisibleCommand}
                />
              ) : (
                <div className="h-12" aria-hidden="true" />
              )}
            </div>

            <button
              type="button"
              className={cn(navButton, 'sm:mb-0.5')}
              onClick={goNext}
              disabled={!page || index === pages.length - 1 || showMissing}
            >
              <span className="hidden sm:inline">Next</span>
              <span className="sr-only sm:hidden">Next page</span>
              <ChevronRight className="size-4" aria-hidden="true" />
            </button>
          </nav>

          <p className="sr-only" aria-live="polite">
            {page && !showMissing ? `Page ${(index ?? 0) + 1} of ${pages.length}: ${page.title}` : ''}
          </p>
          {/* Spoken-command acknowledgements and hands-free notices. */}
          <div className="sr-only" aria-live="polite" aria-atomic="true">
            {commands.ack && <p key={commands.ack.id}>{commands.ack.text}</p>}
          </div>
        </main>

        {isWide && panelOpen && (
          <aside
            aria-label="Notes and bookmarks"
            className="w-80 shrink-0 border-l border-white/10 bg-black/15 p-4 backdrop-blur-md"
          >
            {sidePanel}
          </aside>
        )}

        {isWide && chatOpen && (
          <aside
            aria-label="Course chat"
            className="flex w-96 shrink-0 flex-col border-l border-white/10 bg-black/15 p-4 backdrop-blur-md animate-in fade-in slide-in-from-right-4 duration-200"
          >
            <CourseChatPanel
              courseTitle={course.title}
              pageTitle={page?.title}
              tutor={tutor}
              onOpenSource={openSourcePage}
              onClose={() => setChatOpen(false)}
            />
          </aside>
        )}
      </div>

      {process.env.NODE_ENV === 'development' && <NarrationDevPanel stream={stream} cueLog={cueLog} />}

      {!isDesktop && (
        <Drawer open={railDrawerOpen} onOpenChange={setRailDrawerOpen} swipeDirection="left">
          <DrawerContent className="theme-night">
            <DrawerHeader>
              <DrawerTitle className="font-display text-lg">Contents</DrawerTitle>
            </DrawerHeader>
            <div className="overflow-y-auto px-3 pb-6">{rail}</div>
          </DrawerContent>
        </Drawer>
      )}

      {!isWide && (
        <Drawer open={panelOpen} onOpenChange={setPanelOpen} swipeDirection="right">
          <DrawerContent className="theme-night">
            <DrawerHeader>
              <DrawerTitle className="font-display text-lg">Notes &amp; bookmarks</DrawerTitle>
            </DrawerHeader>
            <div className="flex min-h-0 flex-1 flex-col px-4 pb-6">{sidePanel}</div>
          </DrawerContent>
        </Drawer>
      )}

      {!isWide && (
        <Drawer open={chatOpen} onOpenChange={setChatOpen} swipeDirection="right">
          <DrawerContent className="theme-night w-full sm:w-[26rem]">
            <DrawerHeader>
              <DrawerTitle className="font-display text-lg">Course chat</DrawerTitle>
            </DrawerHeader>
            <div className="flex min-h-0 flex-1 flex-col px-4 pb-[max(1.5rem,env(safe-area-inset-bottom))]">
              <CourseChatPanel
                courseTitle={course.title}
                pageTitle={page?.title}
                tutor={tutor}
                onOpenSource={openSourcePage}
                onClose={() => setChatOpen(false)}
                showTitle={false}
              />
            </div>
          </DrawerContent>
        </Drawer>
      )}
    </ReaderFrame>
    {!introDone && (
      <BookIntro courseId={course.id} title={course.title} subtitle={introSubtitle} onFinish={finishIntro} />
    )}
    </>
  )
}

function ReaderFrame({
  children,
  frameRef,
  inert,
}: {
  children: React.ReactNode
  frameRef?: React.Ref<HTMLDivElement>
  inert?: boolean
}) {
  return (
    <div
      ref={frameRef}
      inert={inert}
      className={cn('theme-night relative flex h-dvh flex-col overflow-hidden bg-night-gradient text-foreground')}
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 bg-[radial-gradient(60%_50%_at_50%_45%,rgb(250_204_21/0.08),transparent_70%),radial-gradient(40%_40%_at_10%_0%,rgb(244_114_182/0.10),transparent_70%)]"
      />
      {children}
    </div>
  )
}
