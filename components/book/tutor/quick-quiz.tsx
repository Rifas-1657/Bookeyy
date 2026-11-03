'use client'

import { ListChecks } from 'lucide-react'
import { parseQuizQuestions } from '@/lib/voice-commands/quiz'

/**
 * R1 "quiz me": the tutor's 2–3 quick questions as a numbered list.
 * R3 (adaptive learning) will replace this with graded, learner-model-driven
 * quizzes; until then it only displays what the tutor asked.
 */
export function QuickQuiz({ text, final }: { text: string; final: boolean }) {
  const quiz = parseQuizQuestions(text)
  if (!final || quiz.questions.length === 0) {
    return <p className="whitespace-pre-wrap">{text}</p>
  }
  return (
    <section aria-label="Quick quiz" className="flex flex-col gap-2">
      <p className="flex items-center gap-1.5 font-sans text-xs font-semibold tracking-wider text-book-deep uppercase">
        <ListChecks className="size-4" aria-hidden="true" /> Quick quiz
      </p>
      {quiz.intro && <p>{quiz.intro}</p>}
      <ol className="flex list-decimal flex-col gap-1.5 pl-5">
        {quiz.questions.map((question, i) => (
          <li key={i} className="pl-1">
            {question}
          </li>
        ))}
      </ol>
      {quiz.outro && <p className="font-sans text-sm text-book-muted">{quiz.outro}</p>}
    </section>
  )
}
