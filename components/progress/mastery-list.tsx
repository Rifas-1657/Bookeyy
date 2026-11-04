'use client'

import { CheckCircle2, Circle, CircleDashed, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import { DashboardPanel, ProgressBar } from '@/components/dashboard/dashboard-panel'
import { SegmentedControl } from '@/components/workspace/form-controls'
import { isLiveLearning, useLearnerSummary, useLearningOverview, type LearnerSummary } from '@/lib/learning'
import { STYLE_LABEL } from '@/lib/learning/model'
import { learningCourses } from '@/lib/mock/learning'
import { getChapterMastery } from '@/lib/mock/progress'

const STATUS = {
  completed: { icon: <CheckCircle2 className="text-emerald-600" />, label: 'Completed' },
  'in-progress': { icon: <CircleDashed className="text-primary" />, label: 'In progress' },
  'not-started': { icon: <Circle className="text-muted-foreground" />, label: 'Not started' },
} as const

const CONCEPT_TONE: Record<string, string> = {
  mastered: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  learning: 'bg-primary/10 text-primary',
  weak: 'bg-rose-500/15 text-rose-700 dark:text-rose-300',
  new: 'bg-muted text-muted-foreground',
}

const pct = (v: number) => Math.round(v * 100)

/** Mastery from the learner model: per chapter, with the concepts inside each. */
function LearnerMastery({ summary }: { summary: LearnerSummary }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs text-muted-foreground">
        {summary.overallMastery === null ? 'No answers yet' : `Overall ${pct(summary.overallMastery)}%`}
        {summary.style.evidence > 0 && ` · teaching style that works best for you: ${STYLE_LABEL[summary.style.style]} (${summary.style.depth})`}
        {summary.dueReviews.length > 0 && ` · ${summary.dueReviews.length} due for review`}
      </p>
      <ol className="divide-y divide-border">
        {summary.chapters.map((chapter) => {
          const concepts = summary.concepts.filter((c) => (c.chapter ?? 0) === chapter.number)
          const Icon = chapter.mastered === chapter.total ? CheckCircle2 : chapter.seen > 0 ? CircleDashed : Circle
          return (
            <li key={chapter.number} className="flex items-start gap-3 py-3">
              <span aria-hidden="true" className="pt-0.5 [&_svg]:size-5">
                <Icon className={chapter.mastered === chapter.total ? 'text-emerald-600' : chapter.seen ? 'text-primary' : 'text-muted-foreground'} />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-3">
                  <p className="truncate text-sm font-medium">
                    <span className="text-muted-foreground">Ch {chapter.number} · </span>
                    {chapter.title}
                  </p>
                  <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                    <span className="sr-only">mastery </span>
                    {pct(chapter.mastery)}%
                  </span>
                </div>
                <ProgressBar value={pct(chapter.mastery)} label={`Chapter ${chapter.number} mastery`} className="mt-2 h-1.5" />
                <ul className="mt-2 flex flex-wrap gap-1.5" aria-label={`Concepts in chapter ${chapter.number}`}>
                  {concepts.map((c) => (
                    <li
                      key={c.id}
                      title={`${c.correct}/${c.attempts} quiz answers right${c.confusion ? ` · ${c.confusion} confusion signals` : ''}`}
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[0.7rem] font-medium ${CONCEPT_TONE[c.status]}`}
                    >
                      {c.status === 'weak' && <TriangleAlert aria-hidden="true" className="size-3" />}
                      {c.label} <span className="tabular-nums opacity-80">{pct(c.mastery)}%</span>
                      <span className="sr-only"> ({c.status})</span>
                    </li>
                  ))}
                </ul>
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

export function MasteryList() {
  const live = isLiveLearning()
  const overview = useLearningOverview()
  const liveCourses = overview.data ?? []
  const options = live
    ? liveCourses.map((c) => ({ value: c.courseId, label: c.title }))
    : learningCourses.map((c) => ({ value: c.id as string, label: c.title }))
  const [picked, setPicked] = useState<string | null>(null)
  const courseId = picked && options.some((o) => o.value === picked) ? picked : (options[0]?.value ?? null)
  const mockCourse = learningCourses.find((c) => c.id === courseId)
  const chapterTitle = (n: number) => mockCourse?.chapters.find((c) => c.number === n)?.title ?? `Chapter ${n}`
  const summary = useLearnerSummary(courseId, chapterTitle)
  const hasModel = Boolean(summary.data && summary.data.concepts.length > 0)

  return (
    <DashboardPanel
      id="mastery"
      title="Chapter mastery"
      description={
        live || hasModel
          ? 'From your quiz answers, questions and code runs (Bayesian knowledge tracing).'
          : 'Take a quiz in a book (“quiz me”) to see your own.'
      }
    >
      {options.length > 1 && (
        <div className="-mt-3 mb-2">
          <SegmentedControl
            label="Book"
            hideLabel
            stacked
            name="mastery-course"
            value={courseId ?? ''}
            onChange={setPicked}
            options={options}
          />
        </div>
      )}
      {hasModel && summary.data ? (
        <LearnerMastery summary={summary.data} />
      ) : live ? (
        <p className="py-3 text-sm text-muted-foreground">
          {overview.status === 'loading' || summary.status === 'loading'
            ? 'Loading your mastery…'
            : 'No mastery data yet. Open a book and say “quiz me”, or finish a page and take the quick check.'}
        </p>
      ) : mockCourse ? (
        <ol className="divide-y divide-border">
          {getChapterMastery(mockCourse).map((entry) => {
            const status = STATUS[entry.status as keyof typeof STATUS] ?? STATUS['not-started']
            return (
              <li key={entry.chapter.number} className="flex items-center gap-3 py-3">
                <span aria-hidden="true" className="[&_svg]:size-5">
                  {status.icon}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline justify-between gap-3">
                    <p className="truncate text-sm font-medium">
                      <span className="text-muted-foreground">Ch {entry.chapter.number} · </span>
                      {entry.chapter.title}
                    </p>
                    <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                      <span className="sr-only">{status.label}, mastery </span>
                      {entry.mastery}%
                    </span>
                  </div>
                  <ProgressBar value={entry.mastery} label={`Chapter ${entry.chapter.number} mastery`} className="mt-2 h-1.5" />
                </div>
              </li>
            )
          })}
        </ol>
      ) : null}
    </DashboardPanel>
  )
}
