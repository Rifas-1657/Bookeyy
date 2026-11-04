'use client'

import { Check, Code2, ListChecks, RotateCcw, Sparkles } from 'lucide-react'
import Link from 'next/link'
import { useState } from 'react'
import { isLiveLearning, useDueReviews, type DueReview } from '@/lib/learning'
import { getLearningCourse, todaysPractice } from '@/lib/mock/learning'
import { cn } from '@/lib/utils'
import { DashboardPanel } from './dashboard-panel'

const KIND_ICON = { Code: Code2, Quiz: ListChecks, Recall: Sparkles, Review: RotateCcw }

interface PracticeTask {
  id: string
  title: string
  detail: string
  kind: keyof typeof KIND_ICON
  minutes: number
  href: string
  courseTitle: string
}

function reviewTask(review: DueReview): PracticeTask {
  const lapse = review.lapses > 0 ? `missed ${review.lapses === 1 ? 'once' : `${review.lapses} times`}` : 'fading from memory'
  return {
    id: `review-${review.courseId}-${review.conceptId}`,
    title: `Review “${review.label}”`,
    detail: `${lapse} · ${Math.round(review.mastery * 100)}% mastery`,
    kind: 'Review',
    minutes: 1,
    // The reader offers "Quick 1-minute review" on open when reviews are due.
    href: `/book/${encodeURIComponent(review.courseId)}${review.chapter ? `?chapter=${review.chapter}` : ''}`,
    courseTitle: review.courseTitle,
  }
}

/**
 * Today's practice: concepts the spaced-review scheduler (FSRS) says are due — real data from
 * the learner model when a backend is configured, this browser's learner model otherwise
 * (followed by the demo's seeded tasks).
 */
export function TodaysPractice() {
  const [done, setDone] = useState<Set<string>>(() => new Set())
  const live = isLiveLearning()
  const reviews = useDueReviews()
  const reviewTasks = (reviews.data ?? []).slice(0, 5).map(reviewTask)
  const demoTasks: PracticeTask[] = live
    ? []
    : todaysPractice.map((task) => ({
        id: task.id,
        title: task.title,
        detail: task.detail,
        kind: task.kind,
        minutes: task.minutes,
        href: `/book/${task.courseId}`,
        courseTitle: getLearningCourse(task.courseId)?.title ?? '',
      }))
  const tasks = [...reviewTasks, ...demoTasks].slice(0, 5)
  const totalMinutes = tasks.reduce((sum, task) => sum + task.minutes, 0)

  function toggle(id: string) {
    setDone((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const description =
    tasks.length === 0
      ? reviews.status === 'loading'
        ? 'Checking what is due…'
        : 'Nothing due for review — quizzes on new pages will add reviews here.'
      : `${reviewTasks.length ? `${reviewTasks.length} due for review · ` : ''}about ${totalMinutes} min`

  return (
    <DashboardPanel
      id="practice"
      title="Today’s practice"
      description={description}
      action={
        tasks.length > 0 ? (
          <span className="shrink-0 font-mono text-xs text-muted-foreground tabular-nums" aria-live="polite">
            {done.size}/{tasks.length} done
          </span>
        ) : undefined
      }
    >
      {tasks.length > 0 && (
        <ul className="flex flex-col gap-2.5">
          {tasks.map((task) => {
            const Icon = KIND_ICON[task.kind]
            const checked = done.has(task.id)
            return (
              <li
                key={task.id}
                className={cn(
                  'flex items-start gap-3 rounded-2xl border border-border p-3 transition-colors',
                  checked ? 'bg-muted/60' : 'bg-background/40',
                )}
              >
                <button
                  type="button"
                  role="checkbox"
                  aria-checked={checked}
                  aria-label={`Mark “${task.title}” as done`}
                  onClick={() => toggle(task.id)}
                  className={cn(
                    'mt-0.5 grid size-6 shrink-0 place-items-center rounded-lg border-2 transition-colors outline-none focus-visible:ring-4 focus-visible:ring-ring/30',
                    checked ? 'border-primary bg-primary text-primary-foreground' : 'border-input hover:border-primary',
                  )}
                >
                  {checked && <Check aria-hidden="true" className="size-3.5" strokeWidth={3} />}
                </button>
                <div className="min-w-0 flex-1">
                  <p className={cn('text-sm font-medium', checked && 'text-muted-foreground line-through')}>{task.title}</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {task.courseTitle} · {task.detail}
                  </p>
                  <div className="mt-2 flex items-center gap-3 text-xs">
                    <span className="inline-flex items-center gap-1 text-muted-foreground">
                      <Icon aria-hidden="true" className="size-3.5" />
                      {task.kind} · {task.minutes} min
                    </span>
                    <Link href={task.href} className="font-medium text-primary underline-offset-4 hover:underline">
                      {task.kind === 'Review' ? 'Review now' : 'Open'}
                      <span className="sr-only"> {task.title}</span>
                    </Link>
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </DashboardPanel>
  )
}
