/**
 * Tutor prompts for lesson commands. "Quiz me" is now the R3 graded quiz
 * (components/book/tutor/graded-quiz.tsx + lib/learning); `buildQuizPrompt` /
 * `parseQuizQuestions` remain only for asking the tutor itself for free-form
 * questions. `buildSimplerPrompt` is sent with context.intent = "simpler", so the
 * backend's teaching-style bandit picks a new angle.
 * Pure (no runtime imports) so node --test can load it.
 */

export type QuizLanguage = 'tanglish' | 'english'

/** Max question length accepted by the tutor (see lib/tutor/demo-tutor.ts MAX_QUESTION_LENGTH). */
const MAX = 280

function clip(title: string, room: number) {
  const t = title.replace(/\s+/g, ' ').trim()
  return t.length > room ? `${t.slice(0, room - 1).trimEnd()}…` : t
}

/** The instruction sent through the normal tutor ask path (page context travels with it). */
export function buildQuizPrompt(pageTitle: string, lang: QuizLanguage): string {
  const title = clip(pageTitle, 80)
  const text =
    lang === 'english'
      ? `Quiz me: ask me 3 short questions about this page ("${title}"), numbered 1 to 3, one line each. Only the questions, no answers yet.`
      : `Quiz me: indha page ("${title}") pathi 3 chinna questions kelunga, 1 to 3 number pottu, ovvonnum oru line. Questions mattum, answers ippo venam.`
  return text.slice(0, MAX)
}

/** "Explain again simpler" — re-explain the current page more simply, through the tutor. */
export function buildSimplerPrompt(pageTitle: string, lang: QuizLanguage, focus?: string): string {
  const title = clip(pageTitle, 70)
  const part = focus ? clip(focus, 80) : ''
  const text =
    lang === 'english'
      ? `Explain this page again more simply ("${title}")${part ? `, especially: "${part}"` : ''}. Short sentences, one everyday example, no new jargon.`
      : `Indha page-a ("${title}") innum simple-a explain pannunga${part ? `, mukkiyama: "${part}"` : ''}. Chinna sentences, oru daily-life example, pudhu jargon venam.`
  return text.slice(0, MAX)
}

export interface ParsedQuiz {
  /** Text before the first question (e.g. "Sari, indha 3 questions-ku answer pannunga:"). */
  intro: string
  questions: string[]
  /** Text after the last question. */
  outro: string
}

const ITEM = /^\s*(?:(?:q(?:uestion)?\s*)?(\d{1,2})\s*[.):\-–]|[-*•]|\(\d{1,2}\))\s*(.+)$/i

/** Splits a tutor reply into numbered questions; returns no questions when it isn't a list. */
export function parseQuizQuestions(text: string): ParsedQuiz {
  const lines = text.split(/\r?\n/)
  const questions: string[] = []
  const intro: string[] = []
  const outro: string[] = []
  for (const line of lines) {
    const m = ITEM.exec(line)
    if (m && m[2].trim()) {
      questions.push(m[2].replace(/\*\*/g, '').trim())
      continue
    }
    if (!line.trim()) continue
    if (questions.length === 0) intro.push(line.trim())
    else outro.push(line.trim())
  }
  if (questions.length < 2) {
    // Not a list: fall back to sentences ending in "?" (some models answer in one paragraph).
    const asked = text.match(/[^.?!\n]+\?/g)?.map((q) => q.trim()) ?? []
    if (asked.length >= 2) return { intro: '', questions: asked.slice(0, 5), outro: '' }
    return { intro: text.trim(), questions: [], outro: '' }
  }
  return { intro: intro.join(' '), questions: questions.slice(0, 5), outro: outro.join(' ') }
}
