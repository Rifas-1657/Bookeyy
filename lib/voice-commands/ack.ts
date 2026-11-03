/**
 * Short acknowledgements the tutor says/shows when she acts on a command,
 * in the learner's language. Tanglish lines are written in Latin letters for
 * the screen; the backend TTS transliterates them to Tamil script for the voice.
 * Pure (no imports at runtime) so node --test can load it.
 */
import type { IntentArgs, VoiceIntent } from './matcher'

export type AckLanguage = 'tanglish' | 'english'

/** Why a command could not be carried out (said instead of the normal ack). */
export type AckProblem =
  | 'no_next_page'
  | 'no_previous_page'
  | 'no_chapter'
  | 'no_page'
  | 'fastest'
  | 'slowest'
  | 'busy'
  | 'no_code'

const LINES: Record<Exclude<VoiceIntent, 'question' | 'goto_chapter' | 'goto_page'>, [string, string]> = {
  stop: ['Sari, niruthuren.', 'Okay, pausing.'],
  resume: ['Sari, continue panren.', 'Okay, continuing.'],
  next_page: ['Sari, aduththa page.', 'Okay, next page.'],
  previous_page: ['Sari, munnaadi page.', 'Okay, previous page.'],
  repeat: ['Sari, thirumba solren.', 'Sure, once more.'],
  slower: ['Sari, konjam slow-a solren.', 'Okay, a little slower.'],
  faster: ['Sari, konjam fast-a solren.', 'Okay, a little faster.'],
  simpler: ['Sari, innum simple-a solren.', 'Sure, let me explain it more simply.'],
  run_code: ['Sari, code run panren.', 'Running the code.'],
  show_diagram: ['Idho, diagram paarunga.', 'Here is the diagram.'],
  quiz: ['Sari, konjam questions kekkuren.', 'Okay, a quick quiz.'],
}

const PROBLEMS: Record<AckProblem, [string, string]> = {
  no_next_page: ['Idhu dhaan last page.', 'This is the last page.'],
  no_previous_page: ['Idhu dhaan first page.', 'This is the first page.'],
  no_chapter: ['Andha chapter indha book-la illa.', 'That chapter is not in this book.'],
  no_page: ['Andha page indha book-la illa.', 'That page is not in this book.'],
  fastest: ['Idhu dhaan max speed.', 'This is already the fastest speed.'],
  slowest: ['Idhu dhaan romba slow speed.', 'This is already the slowest speed.'],
  busy: ['Oru nimisham, munnaadi kelvikku answer panren.', 'One moment, I am still answering your last question.'],
  no_code: ['Indha page-la run panna code illa.', 'There is no code to run on this page.'],
}

export function ackFor(intent: VoiceIntent, args: IntentArgs, lang: AckLanguage): string {
  const en = lang === 'english'
  if (intent === 'question') return ''
  if (intent === 'goto_chapter') {
    if (args.chapterDelta === 1) return en ? 'Okay, next chapter.' : 'Sari, aduththa chapter.'
    if (args.chapterDelta === -1) return en ? 'Okay, previous chapter.' : 'Sari, munnaadi chapter.'
    const where = args.page ? `chapter ${args.chapter}, page ${args.page}` : `chapter ${args.chapter}`
    return en ? `Okay, ${where}.` : `Sari, ${where}.`
  }
  if (intent === 'goto_page') return en ? `Okay, page ${args.page}.` : `Sari, page ${args.page}.`
  const pair = LINES[intent]
  return en ? pair[1] : pair[0]
}

export function ackProblem(problem: AckProblem, lang: AckLanguage): string {
  const pair = PROBLEMS[problem]
  return lang === 'english' ? pair[1] : pair[0]
}

/** What the learner can say, for the visible command list / help text. */
export const COMMAND_EXAMPLES: Array<{ intent: VoiceIntent; label: string; say: string }> = [
  { intent: 'stop', label: 'Pause', say: '“stop”, “niruthu”, “konjam wait”' },
  { intent: 'resume', label: 'Continue', say: '“continue”, “sollunga”' },
  { intent: 'repeat', label: 'Repeat', say: '“thirumba sollu”, “once more”' },
  { intent: 'slower', label: 'Slower', say: '“slow-a sollu”' },
  { intent: 'faster', label: 'Faster', say: '“fast-a sollu”' },
  { intent: 'simpler', label: 'Simpler', say: '“innum simple-a sollu”' },
  { intent: 'quiz', label: 'Quiz me', say: '“quiz me”, “test pannu”' },
  { intent: 'run_code', label: 'Run code', say: '“code run pannu”' },
  { intent: 'show_diagram', label: 'Diagram', say: '“show diagram”' },
  { intent: 'previous_page', label: 'Previous page', say: '“munnaadi page”, “go back”' },
  { intent: 'next_page', label: 'Next page', say: '“aduththa page”, “next page”' },
]
