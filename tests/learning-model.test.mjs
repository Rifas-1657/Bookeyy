// R3 learner model (pure maths shared with the in-browser model). Run: node --test tests/
import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  armOf,
  armParts,
  bktUpdate,
  bucketOf,
  chooseStyle,
  conceptIdFor,
  contextPrior,
  feedbackText,
  gradeLocal,
  isDue,
  keywordScore,
  mentionedConcepts,
  NEW_CARD,
  pageConcepts,
  pageQuizItems,
  pickQuiz,
  preferredStyle,
  quizSummaryLine,
  retrievability,
  reviewCard,
  reviewRating,
  rewardFor,
  rewardPosteriors,
  sampleBeta,
  seededRandom,
  statusFor,
} from '../lib/learning/model.ts'

const page = {
  id: 'lesson_python_1_1',
  title: 'What is a variable?',
  chapterNumber: 1,
  keyTerms: ['variable', 'labeled box', 'value'],
  paragraphs: [
    'A variable is a name that refers to a value stored in memory.',
    'Think of it as a labeled box. The label is the variable name.',
  ],
  narrationText: '',
  codeExample: { source: 'x = 10\nprint(x)', expectedOutput: '10' },
}

test('BKT: right answers raise mastery, wrong ones lower it, soft evidence moves less', () => {
  const p = 0.3
  assert.ok(bktUpdate(p, true) > p)
  assert.ok(bktUpdate(p, false) < p)
  assert.ok(bktUpdate(p, false, { weight: 0.25 }) > bktUpdate(p, false))
  assert.ok(bktUpdate(p, true, { guess: 0.5 }) < bktUpdate(p, true, { guess: 0.1 }))
  // matches the backend (app/personalization/learner_model.py) to 4 places
  assert.equal(bktUpdate(0.3, true, { guess: 0.25 }).toFixed(4), '0.6657')
  assert.equal(statusFor(0.85, true), 'mastered')
  assert.equal(statusFor(0.2, false), 'new')
  assert.equal(statusFor(0.2, true), 'weak')
})

test('concepts come from key terms (title fallback) with stable ids', () => {
  assert.equal(conceptIdFor('print()'), 'print')
  assert.deepEqual(pageConcepts(page).map(([id]) => id), ['variable', 'labeled-box', 'value'])
  assert.deepEqual(pageConcepts({ title: 'Loops in Python', keyTerms: [] }), [['loops-in-python', 'Loops in Python']])
  assert.deepEqual(mentionedConcepts('labeled box na enna?', pageConcepts(page)), ['labeled-box'])
})

test('page-derived quiz: fill-in-the-blank with the term among options, code output question', () => {
  const items = pageQuizItems(page, 'english')
  const mcq = items.filter((q) => q.kind === 'mcq')
  assert.ok(mcq.length >= 1)
  for (const q of mcq) {
    assert.ok(q.options.includes(q.answer))
    assert.ok(q.prompt.includes('_____'))
    assert.ok(!q.prompt.toLowerCase().includes(`${q.answer.toLowerCase()} is a name`))
  }
  const out = items.find((q) => q.check === 'output')
  assert.equal(out?.answer, '10')
  const tl = pageQuizItems(page, 'tanglish')
  assert.ok(tl.some((q) => q.prompt.startsWith('Blank-a fill pannunga')))
  assert.ok(!/[஀-௿]/.test(tl.map((q) => q.prompt).join(' ')), 'Tanglish stays in Latin letters')
  const picked = pickQuiz(items, (id) => (id === 'value' ? 0.1 : 0.6), 3)
  assert.ok(picked.length >= 2 && picked.length <= 3)
  assert.equal(picked[0].conceptId, 'value', 'weakest concept first')
})

test('local grading + teacher feedback', () => {
  const [mcq] = pageQuizItems(page, 'english').filter((q) => q.kind === 'mcq')
  assert.equal(gradeLocal(mcq, mcq.answer).correct, true)
  assert.equal(gradeLocal(mcq, mcq.options.find((o) => o !== mcq.answer)).correct, false)
  const out = pageQuizItems(page, 'english').find((q) => q.check === 'output')
  assert.equal(gradeLocal(out, ' 10 ').correct, true)
  assert.equal(gradeLocal(out, '11').correct, false)
  assert.ok(keywordScore('it is a name for a value in memory', 'A variable is a name that refers to a value stored in memory.') >= 0.5)
  const ok = feedbackText({ id: 'q', answer: 'box', explanation: 'Because.' }, { correct: true, score: 1 }, 'tanglish')
  assert.match(ok, /^(Correct-a sonneenga!|Super, sariyaana answer!|Semma! Correct\.)/)
  const no = feedbackText({ id: 'q', answer: 'box', explanation: 'Because.' }, { correct: false, score: 0 }, 'english')
  assert.match(no, /Not quite — the answer is: box\. Because\./)
  assert.match(quizSummaryLine(3, 3, 'tanglish'), /semma/)
})

test('teaching-style bandit: context prior, forced depth, avoided style, reward learning', () => {
  const confused = { level: 'new', mastery: 0.2, language: 'tanglish', confused: true }
  assert.equal(bucketOf(confused), 'ts:new:low:tanglish:1')
  const [alpha] = contextPrior(confused)
  assert.ok(alpha[armOf('example-first', 'simpler')] > alpha[armOf('example-first', 'deeper')])
  assert.deepEqual(armParts(armOf('visual-first', 'standard')), ['visual-first', 'standard'])
  const rand = seededRandom(42)
  for (let i = 0; i < 20; i++) {
    const c = chooseStyle(confused, {}, { forceDepth: 'simpler', avoidStyle: 'analogy-first', rand })
    assert.equal(c.depth, 'simpler')
    assert.notEqual(c.style, 'analogy-first')
  }
  let post = {}
  const winner = armOf('step-by-step', 'simpler')
  for (let i = 0; i < 15; i++) post = rewardPosteriors(post, bucketOf(confused), winner, 1)
  for (let i = 0; i < 5; i++) post = rewardPosteriors(post, bucketOf(confused), armOf('analogy-first', 'simpler'), 0)
  assert.equal(preferredStyle(confused, post).arm, winner)
  assert.deepEqual(rewardFor('simpler'), [0, 1])
  assert.deepEqual(rewardFor('quiz_answer', true), [1, 1])
  assert.equal(rewardFor('relisten'), null)
  const r = seededRandom(1)
  const mean = Array.from({ length: 2000 }, () => sampleBeta(8, 2, r)).reduce((a, b) => a + b, 0) / 2000
  assert.ok(Math.abs(mean - 0.8) < 0.03, `Beta(8,2) mean ≈ 0.8, got ${mean}`)
})

test('FSRS: a mistake is due tomorrow, a correct review pushes it out', () => {
  const now = new Date('2026-11-03T10:00:00').getTime()
  const day = 86_400_000
  const missed = reviewCard(NEW_CARD, 'again', now)
  assert.equal(missed.lapses, 1)
  assert.equal(isDue(missed, now), false)
  assert.equal(isDue(missed, now + day), true)
  // Reviewing it correctly day after day spaces it further out each time.
  let card = missed
  const gaps = []
  for (let i = 1; i <= 4; i++) {
    const at = now + i * day
    const next = reviewCard(card, 'good', at)
    assert.ok(next.stability > card.stability, 'a correct review never shrinks stability')
    card = next
    let gap = 1
    while (!isDue(card, at + gap * day)) gap++
    gaps.push(gap)
  }
  assert.ok(gaps.at(-1) > gaps[0], `intervals grow: ${gaps}`)
  assert.ok(retrievability(card, now + 365 * day) < 0.9)
  assert.equal(reviewRating(false, { hasCard: false, review: false, slow: false, mastery: 0.3 }), 'again')
  assert.equal(reviewRating(true, { hasCard: false, review: false, slow: false, mastery: 0.7 }), null)
  assert.equal(reviewRating(true, { hasCard: true, review: false, slow: true, mastery: 0.7 }), 'hard')
})
