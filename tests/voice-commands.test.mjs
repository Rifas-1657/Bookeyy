// Voice/text lesson command matcher (R1). Run: node --test tests/
// Node >= 23.6 strips TypeScript types natively, so the pure modules import directly.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  ACT_THRESHOLD,
  isConfident,
  isLikelyEcho,
  isNoiseTranscript,
  matchIntent,
  sanitizeIntent,
  skeleton,
  tamilToLatin,
} from '../lib/voice-commands/matcher.ts'
import { ackFor, ackProblem } from '../lib/voice-commands/ack.ts'
import { buildQuizPrompt, buildSimplerPrompt, parseQuizQuestions } from '../lib/voice-commands/quiz.ts'

function expectIntent(text, intent, args) {
  const m = matchIntent(text)
  assert.equal(m.intent, intent, `"${text}" → ${m.intent} (${m.confidence}), expected ${intent}`)
  if (intent !== 'question') assert.ok(m.confidence >= ACT_THRESHOLD, `"${text}" confidence ${m.confidence}`)
  if (args) assert.deepEqual(m.args, args, `"${text}" args`)
}

const CASES = {
  stop: ['stop', 'Stop!', 'pause', 'niruthu', 'nirutthu', 'konjam wait', 'wait pannu', 'hold on', 'one minute', 'Can you stop for a second?', 'நிறுத்து', 'ஸ்டாப்', 'போதும்'],
  resume: ['continue', 'sollunga', 'start', 'play', 'resume', 'go on', 'carry on', 'தொடருங்க', 'thodarunga'],
  next_page: ['next page', 'next', 'aduththa page', 'adutha pakkam', 'skip', 'um so can you go to the next page please', 'அடுத்த பக்கம்', 'நெக்ஸ்ட் பேஜ்'],
  previous_page: ['previous page', 'munnaadi page', 'go back', 'back', 'முந்தைய பக்கம்'],
  repeat: ['thirumba sollu', 'once more', 'repeat', 'one more time', 'innoru thadava', 'திரும்ப சொல்லு', 'ரிப்பீட்'],
  slower: ['slow-a sollu', 'slowly', 'slow down', 'too fast', 'speed kammi pannunga', 'மெதுவா சொல்லு'],
  faster: ['fast-a', 'fast-a sollu', 'speed up', 'too slow', 'vegama sollunga', 'வேகமா'],
  simpler: ['innum simple-a sollu', 'explain again simpler', 'simpler please', 'puriyala', 'i didnt understand', 'புரியல'],
  run_code: ['code run pannu', 'run the code', 'run it', 'execute', 'kod run pannunga', 'கோட் ரன் பண்ணு'],
  show_diagram: ['show diagram', 'diagram', 'diagram kaattu', 'show me the picture', 'படம் காட்டு'],
  quiz: ['quiz me', 'test pannu', 'ask me questions', 'kelvi kelunga', 'குவிஸ்'],
}

for (const [intent, phrases] of Object.entries(CASES)) {
  test(`intent ${intent}`, () => {
    for (const phrase of phrases) expectIntent(phrase, intent)
  })
}

test('go to chapter / page with numbers in digits, English, Tanglish and Tamil', () => {
  expectIntent('go to chapter 3', 'goto_chapter', { chapter: 3 })
  expectIntent('chapter moonu', 'goto_chapter', { chapter: 3 })
  expectIntent('third chapter', 'goto_chapter', { chapter: 3 })
  expectIntent('அத்தியாயம் 3', 'goto_chapter', { chapter: 3 })
  expectIntent('chapter 2 page 3', 'goto_chapter', { chapter: 2, page: 3 })
  expectIntent('page 5', 'goto_page', { page: 5 })
  expectIntent('go to page twelve', 'goto_page', { page: 12 })
  expectIntent('மூணு பக்கம்', 'goto_page', { page: 3 })
  expectIntent('page ௫', 'goto_page', { page: 5 })
  expectIntent('next chapter', 'goto_chapter', { chapterDelta: 1 })
  expectIntent('previous chapter', 'goto_chapter', { chapterDelta: -1 })
  expectIntent('start from the beginning', 'repeat', { scope: 'page' })
})

test('questions stay questions', () => {
  for (const q of [
    'what is a variable?',
    'variable-um value-um enna difference?',
    'how do I print in python',
    'print hello world',
    'I have a question about variables',
    'can I ask a question',
    'what',
    'okay',
    '',
  ]) {
    assert.equal(matchIntent(q).intent, 'question', q)
  }
})

test('negations and question words are not acted on', () => {
  for (const q of ['dont stop', 'stop pannaadhe', 'why does the loop stop', 'what is this diagram']) {
    const m = matchIntent(q)
    assert.ok(!isConfident(m) || m.intent === 'question', `${q} → ${m.intent} ${m.confidence}`)
  }
})

test('ambiguous phrases are flagged for the backend, not acted on', () => {
  const m = matchIntent('explain loops simpler')
  assert.equal(m.intent, 'simpler')
  assert.ok(!isConfident(m))
})

test('Tamil script and spelling noise normalise to the same skeleton', () => {
  assert.equal(tamilToLatin('நிறுத்து'), 'niruththu')
  assert.equal(skeleton('niruthu'), skeleton(tamilToLatin('நிறுத்து')))
  assert.equal(skeleton('aduththa'), skeleton('adutha'))
  assert.equal(skeleton('munnaadi'), skeleton(tamilToLatin('முன்னாடி')))
})

test('sanitizeIntent validates untrusted backend output', () => {
  assert.deepEqual(sanitizeIntent('goto_page', { page: '4', x: 1 }), { intent: 'goto_page', args: { page: 4 } })
  assert.equal(sanitizeIntent('goto_page', {}), null)
  assert.equal(sanitizeIntent('rm -rf', {}), null)
  assert.equal(sanitizeIntent('goto_chapter', { chapter: -2 }), null)
  assert.deepEqual(sanitizeIntent('stop', null), { intent: 'stop', args: {} })
})

test('echo guard and noise filter', () => {
  const narration = 'Variable oru labeled box maari, adhukulla value vechukalaam.'
  assert.ok(isLikelyEcho('labeled box maari value', [narration]))
  assert.ok(!isLikelyEcho('next page', [narration]))
  assert.ok(!isLikelyEcho('stop', [narration]))
  assert.ok(isNoiseTranscript('Thank you.'))
  assert.ok(isNoiseTranscript('  '))
  assert.ok(!isNoiseTranscript('stop'))
})

test('acknowledgements in the learner language', () => {
  assert.equal(ackFor('stop', {}, 'tanglish'), 'Sari, niruthuren.')
  assert.equal(ackFor('next_page', {}, 'english'), 'Okay, next page.')
  assert.equal(ackFor('goto_chapter', { chapter: 2, page: 3 }, 'english'), 'Okay, chapter 2, page 3.')
  assert.equal(ackFor('question', {}, 'english'), '')
  assert.equal(ackProblem('no_next_page', 'tanglish'), 'Idhu dhaan last page.')
})

test('quiz and simpler prompts fit the tutor limit and parse back', () => {
  const long = 'x'.repeat(500)
  for (const lang of ['tanglish', 'english']) {
    assert.ok(buildQuizPrompt(long, lang).length <= 280)
    assert.ok(buildSimplerPrompt(long, lang, long).length <= 280)
    assert.ok(buildQuizPrompt('Variables', lang).startsWith('Quiz me'))
  }
  const quiz = parseQuizQuestions('Quick quiz:\n1. What is a variable?\n2) Why use names?\nQ3: What prints?\nTake your time.')
  assert.deepEqual(quiz.questions, ['What is a variable?', 'Why use names?', 'What prints?'])
  assert.equal(quiz.intro, 'Quick quiz:')
  assert.equal(quiz.outro, 'Take your time.')
  assert.equal(parseQuizQuestions('What is x? And what is y?').questions.length, 2)
  assert.equal(parseQuizQuestions('No list here.').questions.length, 0)
})
