'use client'

import { ArrowLeft, ArrowRight, Award, Camera, Code2, MessageCircleQuestion, Shapes, Sparkles } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { listActivity, listCaptures, OFFLINE_EVENT, type LessonActivity, type LessonCapture } from '@/lib/offline/db'
import { cn } from '@/lib/utils'
import type { BookLessonPage } from '@/types/book'
import styles from './book-feel.module.css'
import { TUTOR_NAME } from './tutor/tutor-orb'

interface LessonRecapProps {
  courseId: string
  chapterNumber: number
  chapterTitle: string
  pages: BookLessonPage[]
  hasNextChapter: boolean
  onBack: () => void
  onNextChapter: () => void
  offlineSlot?: React.ReactNode
}

function useObjectUrls(captures: LessonCapture[]) {
  const urls = useMemo(() => new Map(captures.map((c) => [c.id, URL.createObjectURL(c.blob)])), [captures])
  useEffect(() => () => urls.forEach((url) => URL.revokeObjectURL(url)), [urls])
  return urls
}

function useLessonRecords(courseId: string, chapterNumber: number) {
  const [captures, setCaptures] = useState<LessonCapture[]>([])
  const [activity, setActivity] = useState<LessonActivity[]>([])

  useEffect(() => {
    let alive = true
    const load = async () => {
      const [c, a] = await Promise.all([listCaptures(courseId), listActivity(courseId)])
      if (!alive) return
      setCaptures(c.filter((x) => x.chapterNumber === chapterNumber).sort((x, y) => x.createdAt - y.createdAt))
      setActivity(a.filter((x) => x.chapterNumber === chapterNumber).sort((x, y) => x.createdAt - y.createdAt))
    }
    void load()
    const onChange = () => void load()
    window.addEventListener(OFFLINE_EVENT, onChange)
    return () => {
      alive = false
      window.removeEventListener(OFFLINE_EVENT, onChange)
    }
  }, [courseId, chapterNumber])

  return { captures, activity }
}

function SectionTitle({ icon: Icon, children }: { icon: typeof Shapes; children: React.ReactNode }) {
  return (
    <h3 className="flex items-center gap-2 font-sans text-[0.7rem] font-semibold tracking-[0.16em] text-book-deep uppercase">
      <Icon className="size-3.5" aria-hidden="true" />
      {children}
    </h3>
  )
}

/**
 * "Lesson complete" recap spread: what was learned, the diagrams and code
 * outputs captured as pictures while the tutor taught, the practicals run and
 * the questions asked. Everything comes from IndexedDB, so it opens offline.
 */
export function LessonRecap({
  courseId,
  chapterNumber,
  chapterTitle,
  pages,
  hasNextChapter,
  onBack,
  onNextChapter,
  offlineSlot,
}: LessonRecapProps) {
  const { captures, activity } = useLessonRecords(courseId, chapterNumber)
  const urls = useObjectUrls(captures)
  const practicals = activity.filter((a): a is Extract<LessonActivity, { kind: 'practical' }> => a.kind === 'practical')
  const questions = activity.filter((a): a is Extract<LessonActivity, { kind: 'question' }> => a.kind === 'question')
  const diagrams = captures.filter((c) => c.kind === 'diagram')
  const outputs = captures.filter((c) => c.kind === 'output')
  const pageTitle = (pageId: string) => pages.find((p) => p.id === pageId)?.title ?? 'Lesson page'
  const keyTerms = [...new Set(pages.flatMap((p) => p.keyTerms ?? []))].slice(0, 12)

  return (
    <div
      data-theme="book"
      className={cn('relative grid min-h-0 flex-1 rounded-[1.75rem] bg-book-paper p-2 text-book-ink lg:grid-cols-2 lg:p-3', styles.spread)}
    >
      <section
        aria-labelledby="recap-title"
        className={cn('min-h-0 overflow-y-auto rounded-[1.25rem] px-6 py-8 sm:px-10 lg:rounded-r-md', styles.paper, styles.pageLeft)}
      >
        <p className="flex items-center gap-2 font-sans text-xs font-semibold tracking-[0.18em] text-book-deep uppercase">
          <Award className="size-4" aria-hidden="true" /> Lesson complete
        </p>
        <h1 id="recap-title" className="mt-2 font-display text-3xl leading-tight font-bold">
          Chapter {chapterNumber} · {chapterTitle}
        </h1>
        <p className="mt-3 font-serif text-lg leading-relaxed italic">
          Romba nalla pannineenga! You finished this lesson with {TUTOR_NAME}. Here is everything we did together, kept in
          your book.
        </p>

        <div className="mt-6 flex flex-col gap-3">
          <SectionTitle icon={Sparkles}>What you learned</SectionTitle>
          <ol className="flex flex-col gap-2 font-serif leading-relaxed">
            {pages.map((page, i) => (
              <li key={page.id} className="flex gap-3">
                <span className="font-display text-book-deep">{i + 1}.</span>
                <span>
                  <strong className="font-semibold">{page.title}</strong>
                  {page.paragraphs[0] ? <span className="block text-sm text-book-muted">{page.paragraphs[0]}</span> : null}
                </span>
              </li>
            ))}
          </ol>
          {keyTerms.length > 0 && (
            <ul aria-label="Key terms" className="flex flex-wrap gap-1.5 pt-1">
              {keyTerms.map((term) => (
                <li key={term} className="rounded-full bg-book-marker/30 px-2.5 py-0.5 font-sans text-xs font-medium">
                  {term}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-8 flex flex-col gap-3">
          <SectionTitle icon={MessageCircleQuestion}>Your questions</SectionTitle>
          {questions.length === 0 ? (
            <p className="font-serif text-sm text-book-muted italic">No questions this time. You can always interrupt me and ask.</p>
          ) : (
            <ul className="flex flex-col gap-3">
              {questions.map((q) => (
                <li key={q.id} className="rounded-xl border-l-4 border-book-orange bg-book-marker/12 px-3 py-2">
                  <p className="font-sans text-sm font-semibold">{q.question}</p>
                  <p className="mt-1 line-clamp-4 font-serif text-sm leading-relaxed">{q.answer}</p>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="mt-8 flex flex-wrap items-center gap-2 font-sans">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex h-10 items-center gap-1.5 rounded-full border border-book-amber/50 px-4 text-sm font-medium hover:bg-book-marker/20 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            <ArrowLeft className="size-4" aria-hidden="true" /> Back to the pages
          </button>
          {hasNextChapter && (
            <button
              type="button"
              onClick={onNextChapter}
              className="inline-flex h-10 items-center gap-1.5 rounded-full bg-book-orange px-4 text-sm font-semibold hover:bg-book-marker focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
            >
              Next lesson <ArrowRight className="size-4" aria-hidden="true" />
            </button>
          )}
          {offlineSlot}
        </div>
      </section>

      <section
        aria-label="Lesson pictures and practicals"
        className={cn('min-h-0 overflow-y-auto rounded-[1.25rem] px-6 py-8 sm:px-10 lg:rounded-l-md', styles.paper, styles.pageRight)}
      >
        <SectionTitle icon={Shapes}>Diagrams we drew</SectionTitle>
        {diagrams.length === 0 ? (
          <p className="mt-2 font-serif text-sm text-book-muted italic">Diagrams are pinned here as the tutor finishes drawing them.</p>
        ) : (
          <ul className="mt-3 grid gap-4 sm:grid-cols-2">
            {diagrams.map((capture, i) => (
              <li key={capture.id} className={cn(styles.polaroid, i % 2 ? 'rotate-1' : '-rotate-1', 'motion-reduce:rotate-0')}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={urls.get(capture.id)} alt={`Diagram: ${capture.title}`} className="w-full rounded" />
                <p className="mt-2 text-center font-serif text-xs italic">{pageTitle(capture.pageId)}</p>
              </li>
            ))}
          </ul>
        )}

        <div className="mt-8">
          <SectionTitle icon={Code2}>Practicals you ran</SectionTitle>
          {practicals.length === 0 && outputs.length === 0 ? (
            <p className="mt-2 font-serif text-sm text-book-muted italic">Run the code lab and your programs will be saved here.</p>
          ) : (
            <ul className="mt-3 flex flex-col gap-4">
              {outputs.map((capture) => (
                <li key={capture.id} className={styles.polaroid}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={urls.get(capture.id)} alt={`Program output: ${capture.title}`} className="w-full rounded" />
                  <p className="mt-2 flex items-center justify-center gap-1 text-center font-serif text-xs italic">
                    <Camera className="size-3" aria-hidden="true" /> {pageTitle(capture.pageId)}
                  </p>
                </li>
              ))}
              {practicals.slice(-6).map((run) => (
                <li key={run.id} className="grid gap-2 sm:grid-cols-2">
                  <pre className="max-h-36 overflow-auto rounded-lg bg-[#1d160c] px-3 py-2 font-mono text-xs text-[#fdf6e3]">{run.code}</pre>
                  <pre className="max-h-36 overflow-auto rounded-lg bg-book-page px-3 py-2 font-mono text-xs">
                    {run.stdout || run.stderr || '(no output)'}
                  </pre>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-y-3 left-1/2 hidden w-16 -translate-x-1/2 bg-[linear-gradient(90deg,transparent,rgb(41_32_19/0.10)_45%,rgb(41_32_19/0.18)_50%,rgb(41_32_19/0.10)_55%,transparent)] lg:block"
      />
    </div>
  )
}
