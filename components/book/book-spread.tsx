'use client'

import { BookOpenText, Shapes } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { idleCueVisuals, type CueVisualState } from '@/lib/narration/cues'
import { captureElement } from '@/lib/offline/capture'
import { notifyOfflineChanged, putActivity } from '@/lib/offline/db'
import { warmPyodideOnIntent } from '@/lib/runner'
import styles from './book-feel.module.css'
import { cn } from '@/lib/utils'
import type { BookLessonPage, ReaderViewMode } from '@/types/book'
import { useCodeLab } from './use-code-lab'
import { AnnotatedText } from './annotated-text'
import { CodeRunnerPanel } from './code-runner-panel'
import { DiagramCanvas } from './diagram-canvas'
import { NarrationText, type NarrationTextState } from './narration-text'
import { OutputPreview } from './output-preview'

interface BookSpreadProps {
  courseId: string
  page: BookLessonPage
  pageLabel: string
  chapterTitle: string
  viewMode: ReaderViewMode
  fontScale: number
  swipeHandlers: { onTouchStart: (e: React.TouchEvent) => void; onTouchEnd: (e: React.TouchEvent) => void }
  narration?: NarrationTextState
  /** Survey interests used to pick the analogy variant. */
  interests?: string[]
  cueVisuals?: CueVisualState
  /** Narration progress (0..1) while a guided pass runs; `null` shows the completed diagram. */
  diagramProgress?: number | null
  /** Active page-turn animation for this page, if any. */
  turn?: PageTurn | null
  onTurnEnd?: () => void
  /** Code the tutor asked the learner to try; `id` changes for each request. */
  tryCode?: { id: number; code: string } | null
  /** Rendered over the spread (tutor margin note). */
  overlay?: React.ReactNode
  /** "Run the code" command: each new id runs the lab once. */
  runRequest?: number
  /** "Show diagram" command: each new id brings the diagram into view. */
  diagramRequest?: number
}

export interface PageTurn {
  id: number
  pageId: string
  direction: 'next' | 'prev'
}

type MobilePanel = 'text' | 'visual'
type SplitView = 'code' | 'output' | 'both'

const SPLIT_OPTIONS: { id: SplitView; label: string }[] = [
  { id: 'code', label: 'Editor' },
  { id: 'both', label: 'Split' },
  { id: 'output', label: 'Output' },
]


function TextPage({
  page,
  pageLabel,
  chapterTitle,
  viewMode,
  fontScale,
  narration,
  interests,
}: Omit<BookSpreadProps, 'swipeHandlers' | 'cueVisuals' | 'diagramProgress' | 'turn' | 'onTurnEnd' | 'tryCode' | 'overlay' | 'runRequest' | 'diagramRequest'>) {
  // The spread is keyed by page, so this resets on every page turn.
  const [showAll, setShowAll] = useState(false)
  const guided = Boolean(narration?.streaming)
  const revealed =
    !guided ||
    showAll ||
    Boolean(narration?.revealAll) ||
    (narration?.words.length ?? 0) === 0 ||
    (narration?.activeIndex ?? 0) >= (narration?.words.length ?? 0)
  return (
    <article
      aria-labelledby="page-title"
      className="flex h-full flex-col overflow-y-auto px-6 py-8 sm:px-10 lg:px-12"
      style={{ fontSize: `${fontScale}rem` }}
    >
      <p className="text-[0.75em] font-semibold tracking-[0.18em] text-book-deep uppercase">{chapterTitle}</p>
      <h1 id="page-title" className="mt-2 font-display text-[1.875em] leading-tight font-bold">
        {page.title}
      </h1>

      <aside
        aria-label="Narration"
        className="mt-6 rounded-2xl border-l-4 border-book-orange bg-book-marker/15 px-4 py-3"
      >
        <p className="text-[0.7em] font-semibold tracking-wider text-book-deep uppercase">
          {viewMode === 'guided' ? 'Narration · guided mode' : 'Narration'}
        </p>
        <p lang="ta-Latn" className="mt-1 font-serif text-[1em] leading-relaxed italic">
          <NarrationText text={page.narrationText} narration={narration} />
        </p>
      </aside>

      {/* Guided pages hold the rest back (visually) until the narration has covered it;
          it stays in the accessibility tree so screen-reader users can read ahead. */}
      {guided && !revealed && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="mt-4 self-start rounded-full border border-border bg-card/60 px-3 py-1 text-[0.75em] font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          Show full page
        </button>
      )}
      <div className={cn(!revealed && 'sr-only')}>
      <div className="mt-6 flex flex-col gap-4 font-serif text-[1.0625em] leading-[1.75]">
        {page.paragraphs.map((paragraph, index) => (
          <p key={index}>
            <AnnotatedText text={paragraph} terms={page.keyTerms} />
          </p>
        ))}
      </div>

      <aside aria-label="Real-world comparison" className="mt-6 rounded-2xl border border-border/60 bg-card/50 px-4 py-3">
        <p className="text-[0.7em] font-semibold tracking-wider text-book-deep uppercase">Think of it like this</p>
        <p data-testid="analogy" className="mt-1 font-serif text-[1em] leading-relaxed">
          {page.analogies[interests?.includes('cricket') ? 'cricket' : interests?.includes('movies') ? 'movies' : 'neutral']}
        </p>
      </aside>

      <section aria-label="Practice prompts" className="mt-4">
        <h2 className="text-[0.7em] font-semibold tracking-wider text-book-deep uppercase">Try it</h2>
        <ul className="mt-1 flex list-disc flex-col gap-1 pl-5 font-serif text-[1em] leading-relaxed">
          {page.practicePrompts.map((prompt) => (
            <li key={prompt}>{prompt}</li>
          ))}
        </ul>
      </section>
      </div>

      <p className="mt-auto pt-8 text-center font-mono text-[0.7em] text-muted-foreground">{pageLabel}</p>
    </article>
  )
}

function VisualPage({
  courseId,
  page,
  cueVisuals = idleCueVisuals,
  diagramProgress = null,
  tryCode = null,
  onPractical,
  runRequest = 0,
  diagramRequest = 0,
  onShowVisual,
}: {
  courseId: string
  page: BookLessonPage
  cueVisuals?: CueVisualState
  diagramProgress?: number | null
  tryCode?: { id: number; code: string } | null
  runRequest?: number
  diagramRequest?: number
  /** Called when a command needs the visual page shown (mobile). */
  onShowVisual?: () => void
  /** Called when the lab starts on its own so mobile can show it. */
  onPractical?: () => void
}) {
  const lab = useCodeLab(page.id, page.codeExample, courseId)
  const [split, setSplit] = useState<SplitView>('both')
  const narrationActive = diagramProgress !== null
  const diagramRef = useRef<HTMLDivElement>(null)
  const outputRef = useRef<HTMLDivElement>(null)

  // Python (~10 MB) downloads only on intent: hovering/focusing Run (see
  // CodeRunnerPanel) or a guided pass that is about to run the practical.
  useEffect(() => {
    if (narrationActive && page.codeExample.language === 'python') warmPyodideOnIntent()
  }, [narrationActive, page.codeExample.language])

  // Pin the finished diagram into the lesson recap as a picture.
  const diagramDone = diagramProgress !== null && diagramProgress >= 0.999
  useEffect(() => {
    if (!diagramDone) return
    const timer = window.setTimeout(
      () =>
        void captureElement(diagramRef.current, {
          courseId,
          pageId: page.id,
          chapterNumber: page.chapterNumber,
          kind: 'diagram',
          title: page.diagramSpec.title ?? page.title,
        }),
      1200,
    )
    return () => window.clearTimeout(timer)
  }, [diagramDone, courseId, page])

  // Every finished run: remember the practical and photograph the output.
  const settled = lab.state.status === 'done' || lab.state.status === 'error'
  const runKey = settled ? `${lab.state.completedRunId}:${lab.state.status}:${lab.stdout.length}` : null
  useEffect(() => {
    if (!runKey || !lab.state.result) return
    void putActivity({
      id: `${courseId}/${page.id}/run/${Date.now()}`,
      courseId,
      pageId: page.id,
      chapterNumber: page.chapterNumber,
      kind: 'practical',
      code: lab.state.code,
      stdout: lab.stdout,
      stderr: lab.stderr,
      createdAt: Date.now(),
    }).then(() => notifyOfflineChanged(courseId))
    const timer = window.setTimeout(
      () =>
        void captureElement(outputRef.current, {
          courseId,
          pageId: page.id,
          chapterNumber: page.chapterNumber,
          kind: 'output',
          title: page.title,
        }),
      500,
    )
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runKey])

  // "Try it" from a tutor answer: put it in the lab and run it.
  useEffect(() => {
    if (!tryCode) return
    lab.restore(tryCode.code)
    onPractical?.()
    const timer = window.setTimeout(() => {
      lab.setTab('output')
      lab.run(false)
    }, 80)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tryCode?.id])

  // Voice/text command "run the code": finish any typing animation and run what is in the lab.
  useEffect(() => {
    if (!runRequest) return
    onPractical?.()
    lab.finishStream()
    lab.setTab('output')
    if (!lab.runActive) lab.run(false)
    const timer = window.setTimeout(() => outputRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 120)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runRequest])

  // Voice/text command "show diagram": bring it into view and flash it.
  const [diagramFlash, setDiagramFlash] = useState(false)
  useEffect(() => {
    if (!diagramRequest) return
    onShowVisual?.()
    setDiagramFlash(true)
    const scroll = window.setTimeout(() => diagramRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }), 80)
    const flash = window.setTimeout(() => setDiagramFlash(false), 1600)
    return () => {
      window.clearTimeout(scroll)
      window.clearTimeout(flash)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [diagramRequest])

  // Narration drives the lab by changing state only; it never moves keyboard focus.
  useEffect(() => {
    if (narrationActive && !lab.edited && lab.state.status === 'ready') {
      lab.startStream()
      onPractical?.()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [narrationActive])

  useEffect(() => {
    if (!cueVisuals.codeRunCued) return
    onPractical?.()
    lab.finishStream()
    lab.setTab('output')
    lab.run(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cueVisuals.codeRunCued])

  useEffect(() => {
    if (!cueVisuals.outputRevealed) return
    lab.finishStream()
    lab.setTab('output')
    if (!lab.state.result && !lab.runActive) lab.run(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cueVisuals.outputRevealed])

  return (
    <section aria-label="Visual explanation" className="flex h-full flex-col gap-4 overflow-y-auto px-5 py-8 sm:px-8">
      <div
        ref={diagramRef}
        className={cn('rounded-2xl transition-shadow duration-500', diagramFlash && 'shadow-[0_0_0_3px_rgb(249_115_22/0.55)]')}
      >
      <DiagramCanvas
        spec={page.diagramSpec}
        focusNodeId={cueVisuals.focusNodeId}
        narration={
          diagramProgress === null
            ? null
            : { active: true, progress: diagramProgress, focusNodeId: cueVisuals.focusNodeId }
        }
      />
      </div>
      <div
        role="radiogroup"
        aria-label="Code lab layout"
        className="flex self-start rounded-full border border-border bg-card/60 p-0.5 lg:hidden"
      >
        {SPLIT_OPTIONS.map((option) => (
          <button
            key={option.id}
            type="button"
            role="radio"
            aria-checked={split === option.id}
            onClick={() => setSplit(option.id)}
            className={cn(
              'rounded-full px-3 py-1 text-xs font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
              split === option.id ? 'bg-ink text-[#F3EAD8]' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {option.label}
          </button>
        ))}
      </div>
      <div className={cn(split === 'output' && 'hidden lg:block')}>
        <CodeRunnerPanel example={page.codeExample} lab={lab} focusLine={cueVisuals.focusLine} />
      </div>
      <div ref={outputRef} className={cn(split === 'code' && 'hidden lg:block')}>
        <OutputPreview lab={lab} title={page.title} expectedOutput={page.codeExample.expectedOutput} />
      </div>
    </section>
  )
}

/** Two-page spread on desktop; single page with a text/visual switcher on mobile. */
export function BookSpread(props: BookSpreadProps) {
  const { page, swipeHandlers, turn, onTurnEnd, tryCode, overlay, runRequest, diagramRequest, ...textProps } = props
  const activeTurn = turn && turn.pageId === page.id ? turn : null
  const [panel, setPanel] = useState<MobilePanel>('text')
  // On phones the code lab "opens by itself": jump to the visual page when a practical starts.
  const showPractical = () => {
    if (window.matchMedia('(max-width: 1023px)').matches) setPanel('visual')
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div role="tablist" aria-label="Page view" className="mx-auto flex rounded-full border border-border bg-card/40 p-1 backdrop-blur-md lg:hidden">
        {(
          [
            { id: 'text', label: 'Read', icon: BookOpenText },
            { id: 'visual', label: 'Visual', icon: Shapes },
          ] as const
        ).map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`tab-${id}`}
            aria-selected={panel === id}
            aria-controls={`panel-${id}`}
            onClick={() => setPanel(id)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                const next = panel === 'text' ? 'visual' : 'text'
                setPanel(next)
                document.getElementById(`tab-${next}`)?.focus()
              }
            }}
            tabIndex={panel === id ? 0 : -1}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full px-4 py-1.5 text-sm font-medium transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
              panel === id ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon className="size-4" aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>

      <div
        key={page.id}
        data-theme="book"
        {...swipeHandlers}
        className={cn(
          'relative grid min-h-0 flex-1 rounded-[1.75rem] bg-book-paper p-2 lg:grid-cols-2 lg:p-3',
          styles.spread,
          !activeTurn && 'animate-in duration-300 fade-in',
        )}
      >
        <div
          id="panel-text"
          role="tabpanel"
          aria-labelledby="tab-text"
          className={cn(
            'min-h-0 rounded-[1.25rem] bg-book-page lg:block lg:rounded-r-md',
            styles.paper,
            styles.pageLeft,
            panel === 'text' ? 'block' : 'hidden',
          )}
        >
          <TextPage page={page} {...textProps} />
        </div>
        <div
          id="panel-visual"
          role="tabpanel"
          aria-labelledby="tab-visual"
          className={cn(
            'min-h-0 rounded-[1.25rem] bg-book-page lg:block lg:rounded-l-md',
            styles.paper,
            styles.pageRight,
            panel === 'visual' ? 'block' : 'hidden',
          )}
        >
          <VisualPage
            courseId={props.courseId}
            page={page}
            cueVisuals={props.cueVisuals}
            diagramProgress={props.diagramProgress}
            tryCode={tryCode}
            onPractical={showPractical}
            runRequest={runRequest}
            diagramRequest={diagramRequest}
            onShowVisual={showPractical}
          />
        </div>
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-y-3 left-1/2 hidden w-16 -translate-x-1/2 bg-[linear-gradient(90deg,transparent,rgb(41_32_19/0.10)_45%,rgb(41_32_19/0.18)_50%,rgb(41_32_19/0.10)_55%,transparent)] lg:block"
        />
        {activeTurn && (
          <div
            key={activeTurn.id}
            aria-hidden="true"
            className={styles.turnLayer}
            data-direction={activeTurn.direction}
            onAnimationEnd={(event) => {
              if ((event.target as HTMLElement).classList.contains(styles.leaf)) onTurnEnd?.()
            }}
          >
            <div className={styles.shade} />
            <div className={styles.leaf} />
          </div>
        )}
        {overlay}
      </div>
    </div>
  )
}
