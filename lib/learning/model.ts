/**
 * R3 adaptive learning — the pure learner-model maths, shared by the in-browser
 * (mock-mode) learner model and the UI. Mirrors the backend:
 *   backend/app/personalization/learner_model.py  (Bayesian Knowledge Tracing)
 *   backend/app/personalization/teaching_style.py (contextual Thompson-sampling bandit)
 *   backend/app/personalization/quiz.py           (page-derived questions, grading, feedback)
 *   backend/app/personalization/fsrs.py           (FSRS-5 spaced review)
 * No runtime imports, so `node --test` loads it directly.
 */

/* --------------------------------- BKT ---------------------------------- */

export type LearnerLevel = 'new' | 'basics' | 'projects'
export type Lang = 'tanglish' | 'english'

export const LEVEL_PRIOR: Record<LearnerLevel, number> = { new: 0.15, basics: 0.3, projects: 0.45 }
export const P_SLIP = 0.1
export const P_TRANSIT = 0.15
export const GUESS_SHORT = 0.1
export const SLOW_MS = 45_000
export const MASTERED = 0.8
export const LEARNING = 0.5

export type EvidenceKind =
  | 'quiz_answer'
  | 'review_answer'
  | 'code_run'
  | 'simpler'
  | 'question'
  | 'repeat'
  | 'relisten'
  | 'page_complete'

/** kind → [observed correct, weight, counts as confusion] */
export const SOFT_EVIDENCE: Partial<Record<EvidenceKind, [boolean, number, boolean]>> = {
  simpler: [false, 0.25, true],
  question: [false, 0.15, true],
  repeat: [false, 0.15, true],
  relisten: [false, 0.1, false],
}

export function clamp(p: number, lo = 0.01, hi = 0.99) {
  return Math.max(lo, Math.min(hi, p))
}

/** One (optionally down-weighted) BKT observation followed by the learning step. */
export function bktUpdate(p0: number, correct: boolean, opts: { guess?: number; slip?: number; transit?: number; weight?: number } = {}) {
  const { guess = 0.25, slip = P_SLIP, transit = P_TRANSIT } = opts
  const w = Math.max(0, Math.min(1, opts.weight ?? 1))
  const p = clamp(p0)
  const post = correct ? (p * (1 - slip)) / (p * (1 - slip) + (1 - p) * guess) : (p * slip) / (p * slip + (1 - p) * (1 - guess))
  const blended = p + w * (post - p)
  return clamp(blended + (1 - blended) * transit * w)
}

export function learnStep(p: number, weight = 0.5) {
  const q = clamp(p)
  return clamp(q + (1 - q) * P_TRANSIT * weight)
}

export type ConceptStatus = 'mastered' | 'learning' | 'weak' | 'new'

export function statusFor(mastery: number, hasEvidence: boolean): ConceptStatus {
  if (mastery >= MASTERED) return 'mastered'
  if (mastery >= LEARNING) return 'learning'
  return hasEvidence ? 'weak' : 'new'
}

/* ------------------------------- concepts -------------------------------- */

export interface QuizPage {
  id: string
  title: string
  chapterNumber: number
  keyTerms?: string[]
  paragraphs: string[]
  narrationText?: string
  codeExample?: { source?: string; expectedOutput?: string }
}

export function conceptIdFor(term: string) {
  return (term ?? '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 100)
}

export function pageConcepts(page: Pick<QuizPage, 'title' | 'keyTerms'>): Array<[id: string, label: string]> {
  const out: Array<[string, string]> = []
  const seen = new Set<string>()
  for (const term of page.keyTerms ?? []) {
    const label = String(term).trim()
    const id = conceptIdFor(label)
    if (id && !seen.has(id)) {
      seen.add(id)
      out.push([id, label.slice(0, 200)])
    }
    if (out.length >= 6) break
  }
  if (out.length === 0 && conceptIdFor(page.title)) out.push([conceptIdFor(page.title), page.title.slice(0, 200)])
  return out
}

const words = (text: string) => (text ?? '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()

export function mentionedConcepts(text: string, concepts: Array<[string, string]>): string[] {
  const t = ` ${words(text)} `
  const hits: string[] = []
  for (const [id, label] of concepts) {
    const ws = words(label).split(' ').filter(Boolean)
    if (ws.length === 0) continue
    if (t.includes(` ${ws.join(' ')} `) || (ws.length > 1 && ws.filter((w) => w.length > 2).every((w) => t.includes(` ${w} `)))) {
      hits.push(id)
    }
  }
  return hits
}

/* ------------------------ teaching-style bandit -------------------------- */

export const STYLES = ['analogy-first', 'step-by-step', 'example-first', 'visual-first'] as const
export const DEPTHS = ['simpler', 'standard', 'deeper'] as const
export type TeachingStyle = (typeof STYLES)[number]
export type TeachingDepth = (typeof DEPTHS)[number]
export const NUM_ARMS = STYLES.length * DEPTHS.length
export const GLOBAL_BUCKET = 'ts:all'

export const STYLE_LABEL: Record<TeachingStyle, string> = {
  'analogy-first': 'Analogy first',
  'step-by-step': 'Step by step',
  'example-first': 'Example first',
  'visual-first': 'Picture first',
}

export function armOf(style: TeachingStyle, depth: TeachingDepth) {
  return STYLES.indexOf(style) * DEPTHS.length + DEPTHS.indexOf(depth)
}

export function armParts(arm: number): [TeachingStyle, TeachingDepth] {
  return [STYLES[Math.floor(arm / DEPTHS.length)], DEPTHS[arm % DEPTHS.length]]
}

export interface StyleContext {
  level: LearnerLevel
  mastery: number
  language: Lang
  confused: boolean
}

export function band(mastery: number) {
  return mastery < 0.4 ? 'low' : mastery < 0.7 ? 'mid' : 'high'
}

export function bucketOf(ctx: StyleContext) {
  return `ts:${ctx.level}:${band(ctx.mastery)}:${ctx.language}:${ctx.confused ? 1 : 0}`
}

export function contextPrior(ctx: StyleContext): [number[], number[]] {
  const alpha = Array.from({ length: NUM_ARMS }, () => 1)
  const beta = Array.from({ length: NUM_ARMS }, () => 1)
  const b = band(ctx.mastery)
  for (let arm = 0; arm < NUM_ARMS; arm++) {
    const depth = armParts(arm)[1]
    if (depth === 'simpler' && (b === 'low' || ctx.confused)) alpha[arm] += 1
    else if (depth === 'deeper' && b === 'high' && !ctx.confused) alpha[arm] += 1
    else if (depth === 'standard' && b !== 'low' && !ctx.confused) alpha[arm] += 0.5
  }
  return [alpha, beta]
}

/** Beta posteriors: bucket → arm → [alpha, beta]. */
export type Posteriors = Record<string, Record<number, [number, number]>>

export function combine(ctx: StyleContext, post: Posteriors): [number[], number[]] {
  const [alpha, beta] = contextPrior(ctx)
  const bucket = post[bucketOf(ctx)] ?? {}
  const pooled = post[GLOBAL_BUCKET] ?? {}
  for (let arm = 0; arm < NUM_ARMS; arm++) {
    const [ba, bb] = bucket[arm] ?? [1, 1]
    const [ga, gb] = pooled[arm] ?? [1, 1]
    alpha[arm] += ba - 1 + 0.5 * (ga - 1)
    beta[arm] += bb - 1 + 0.5 * (gb - 1)
  }
  return [alpha, beta]
}

/** Gamma(k, 1) sampler (Marsaglia–Tsang), for Beta sampling. */
function sampleGamma(k: number, rand: () => number): number {
  if (k < 1) return sampleGamma(k + 1, rand) * Math.pow(rand(), 1 / k)
  const d = k - 1 / 3
  const c = 1 / Math.sqrt(9 * d)
  for (;;) {
    let x: number
    let v: number
    do {
      // Box–Muller standard normal
      const u1 = Math.max(rand(), 1e-12)
      x = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * rand())
      v = 1 + c * x
    } while (v <= 0)
    v = v * v * v
    const u = rand()
    if (u < 1 - 0.0331 * x ** 4 || Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v
  }
}

export function sampleBeta(a: number, b: number, rand: () => number = Math.random) {
  const x = sampleGamma(Math.max(a, 0.01), rand)
  const y = sampleGamma(Math.max(b, 0.01), rand)
  return x / (x + y)
}

/** Seeded PRNG (mulberry32) for reproducible tests. */
export function seededRandom(seed: number) {
  let t = seed >>> 0
  return () => {
    t = (t + 0x6d2b79f5) >>> 0
    let r = Math.imul(t ^ (t >>> 15), 1 | t)
    r = (r + Math.imul(r ^ (r >>> 7), 61 | r)) ^ r
    return ((r ^ (r >>> 14)) >>> 0) / 4294967296
  }
}

export interface StyleChoice {
  arm: number
  style: TeachingStyle
  depth: TeachingDepth
  bucket: string
}

export function chooseStyle(
  ctx: StyleContext,
  post: Posteriors,
  opts: { forceDepth?: TeachingDepth; avoidStyle?: TeachingStyle; rand?: () => number } = {},
): StyleChoice {
  const [alpha, beta] = combine(ctx, post)
  let best = 0
  let bestTheta = -1
  for (let arm = 0; arm < NUM_ARMS; arm++) {
    const [style, depth] = armParts(arm)
    if (opts.forceDepth && depth !== opts.forceDepth) continue
    if (opts.avoidStyle && style === opts.avoidStyle) continue
    const theta = sampleBeta(alpha[arm], beta[arm], opts.rand)
    if (theta > bestTheta) {
      best = arm
      bestTheta = theta
    }
  }
  const [style, depth] = armParts(best)
  return { arm: best, style, depth, bucket: bucketOf(ctx) }
}

export function preferredStyle(ctx: StyleContext, post: Posteriors): StyleChoice & { expected: number } {
  const [alpha, beta] = combine(ctx, post)
  let best = 0
  for (let arm = 1; arm < NUM_ARMS; arm++) {
    if (alpha[arm] / (alpha[arm] + beta[arm]) > alpha[best] / (alpha[best] + beta[best])) best = arm
  }
  const [style, depth] = armParts(best)
  return { arm: best, style, depth, bucket: bucketOf(ctx), expected: alpha[best] / (alpha[best] + beta[best]) }
}

/** Immutable reward update in `bucket` and the pooled posterior. */
export function rewardPosteriors(post: Posteriors, bucket: string, arm: number, reward: number, weight = 1): Posteriors {
  const r = Math.max(0, Math.min(1, reward))
  const next: Posteriors = { ...post }
  for (const key of bucket === GLOBAL_BUCKET ? [bucket] : [bucket, GLOBAL_BUCKET]) {
    const arms = { ...(next[key] ?? {}) }
    const [a, b] = arms[arm] ?? [1, 1]
    arms[arm] = [a + weight * r, b + weight * (1 - r)]
    next[key] = arms
  }
  return next
}

/** Reward for the last explanation style from one piece of evidence; null = no reward. */
export function rewardFor(kind: EvidenceKind, correct?: boolean | null): [reward: number, weight: number] | null {
  switch (kind) {
    case 'quiz_answer':
    case 'review_answer':
      return [correct ? 1 : 0, 1]
    case 'simpler':
      return [0, 1]
    case 'repeat':
      return [0, 0.5]
    case 'page_complete':
      return [1, 0.5]
    case 'question':
      return [0.3, 0.3]
    default:
      return null
  }
}

/* ------------------------------- FSRS-5 ---------------------------------- */

const W = [0.4072, 1.1829, 3.1262, 15.4722, 7.2102, 0.5316, 1.0651, 0.0589, 1.506, 0.1544, 1.0071, 1.9297, 0.1514, 0.1104, 0.2099, 0.29, 2.27]
export type Rating = 'again' | 'hard' | 'good' | 'easy'
const RATING: Record<Rating, number> = { again: 1, hard: 2, good: 3, easy: 4 }
const DAY = 86_400_000

export interface ReviewCard {
  stability: number
  difficulty: number
  /** Epoch ms of the last review (start of that day, like the backend's date). */
  lastReview: number | null
  reviews: number
  lapses: number
}

export const NEW_CARD: ReviewCard = { stability: 1, difficulty: 5, lastReview: null, reviews: 0, lapses: 0 }

export function startOfDay(ms: number) {
  const d = new Date(ms)
  d.setHours(0, 0, 0, 0)
  return d.getTime()
}

export function retrievability(card: ReviewCard, now = Date.now()) {
  if (card.lastReview === null) return 0
  const days = Math.round((startOfDay(now) - startOfDay(card.lastReview)) / DAY)
  return Math.pow(0.9, days / Math.max(card.stability, 0.001))
}

export function isDue(card: ReviewCard, now = Date.now()) {
  return retrievability(card, now) < 0.9
}

export function reviewCard(card: ReviewCard, rating: Rating, now = Date.now()): ReviewCard {
  const r = RATING[rating]
  const d = card.difficulty
  const s = card.stability
  const rr = retrievability(card, now)
  const d0 = (g: number) => Math.max(1, Math.min(10, W[4] - (g - 3) * W[5]))
  let stability: number
  if (card.reviews === 0) stability = W[r - 1]
  else if (rating === 'again') stability = W[11] * Math.pow(d, -W[12]) * (Math.pow(s + 1, W[13]) - 1) * Math.exp((1 - rr) * W[14])
  else
    stability =
      s *
      (1 +
        Math.exp(W[8]) * (11 - d) * Math.pow(s, -W[9]) * (Math.exp((1 - rr) * W[10]) - 1) * (r === 2 ? W[15] : 1) * (r === 4 ? W[16] : 1))
  // First review: D0(G); later: mean reversion toward D0(good).
  const difficulty = card.reviews === 0 ? d0(r) : W[7] * d0(3) + (1 - W[7]) * (d - W[6] * (r - 3))
  return {
    stability: Math.max(0.1, stability),
    difficulty: Math.max(1, Math.min(10, difficulty)),
    lastReview: startOfDay(now),
    reviews: card.reviews + 1,
    lapses: card.lapses + (rating === 'again' ? 1 : 0),
  }
}

/** Which FSRS rating (if any) a graded answer produces — same rules as the backend. */
export function reviewRating(correct: boolean, opts: { hasCard: boolean; review: boolean; slow: boolean; mastery: number }): Rating | null {
  if (!correct) return 'again'
  if (opts.hasCard || opts.review) return opts.slow ? 'hard' : 'good'
  if (opts.mastery < LEARNING) return 'hard'
  return null
}

/* ------------------------------- quizzes --------------------------------- */

export interface QuizQuestion {
  id: string
  kind: 'mcq' | 'short'
  prompt: string
  options: string[]
  conceptId: string
  conceptLabel: string
  source: 'lesson' | 'llm' | 'page' | 'review'
  pageId: string
}

/** A question with its answer (kept locally in mock mode; server-side in live mode). */
export interface QuizItem extends QuizQuestion {
  answer: string
  explanation: string
  check: 'option' | 'keywords' | 'output' | 'rubric'
}

const STOP = new Set(
  'a an the and or of to in on for with from into by about as at is are be been was were this that these those it its how what why when which who you your we our they their there here do does did can could will would should not no yes so if then than also just only very more most some any each all one two use used using like such vs per idhu adhu enna epdi illa irukku pannu pannunga oru ellam'.split(
    ' ',
  ),
)

export function contentWords(text: string) {
  const out: string[] = []
  for (const w of (text ?? '').toLowerCase().match(/[a-z0-9]+/g) ?? []) {
    if (w.length >= 3 && !STOP.has(w) && !out.includes(w)) out.push(w)
  }
  return out
}

const norm = (s: string) => ((s ?? '').toLowerCase().match(/[a-z0-9]+/g) ?? []).join(' ')

function hash(text: string) {
  let h = 2166136261
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619)
  return h >>> 0
}

function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function sentencesOf(page: QuizPage) {
  const out: string[] = []
  for (const chunk of [...page.paragraphs, page.narrationText ?? '']) {
    for (const s of chunk.split(/(?<=[.!?])\s+/)) {
      const t = s.trim()
      if (t.length >= 20 && t.length <= 260) out.push(t)
    }
  }
  return out
}

/**
 * Questions written from the page itself (no model): fill-in-the-blank on a page sentence
 * that contains a key term (distractors = the page's other key terms), "in your own words"
 * when there are too few distractors, and "what will this code print?" from the expected output.
 */
export function pageQuizItems(page: QuizPage, lang: Lang, idPrefix = 'local', only?: Set<string>): QuizItem[] {
  const en = lang === 'english'
  const concepts = pageConcepts(page)
  const labels = concepts.map(([, l]) => l)
  const sentences = sentencesOf(page)
  const out: QuizItem[] = []
  let n = 0
  const id = () => `${idPrefix}-${page.id}-${n++}`
  for (const [cid, label] of concepts) {
    if (only && !only.has(cid)) continue
    const pat = new RegExp(`(?<![\\w])${escapeRe(label)}(?![\\w])`, 'i')
    const sentence = sentences.find((s) => pat.test(s))
    const distractors = labels.filter((l) => l.toLowerCase() !== label.toLowerCase() && !pat.test(l)).slice(0, 3)
    if (sentence && distractors.length >= 2) {
      const blank = sentence.replace(pat, '_____')
      const options = [label, ...distractors].sort((a, b) => hash(page.id + a) - hash(page.id + b))
      out.push({
        id: id(),
        kind: 'mcq',
        prompt: (en ? `Fill in the blank: “${blank}”` : `Blank-a fill pannunga: “${blank}”`).slice(0, 300),
        options,
        answer: label,
        explanation: (en ? `The page says: “${sentence}”` : `Page-la irukku: “${sentence}”`).slice(0, 400),
        conceptId: cid,
        conceptLabel: label,
        source: 'page',
        check: 'option',
        pageId: page.id,
      })
    } else if (sentence) {
      out.push({
        id: id(),
        kind: 'short',
        prompt: en ? `In your own words, what is “${label}”?` : `“${label}” na enna? Ungal words-la sollunga.`,
        options: [],
        answer: sentence,
        explanation: sentence,
        conceptId: cid,
        conceptLabel: label,
        source: 'page',
        check: 'keywords',
        pageId: page.id,
      })
    }
  }
  const expected = page.codeExample?.expectedOutput?.trim() ?? ''
  if (page.codeExample?.source && expected && expected.length <= 120 && (!only || out.length === 0)) {
    const hit = mentionedConcepts(page.codeExample.source, concepts)[0]
    const [cid, label] = concepts.find(([c]) => c === hit) ?? concepts[0] ?? [conceptIdFor(page.title), page.title]
    if (!only || only.has(cid)) {
      out.push({
        id: id(),
        kind: 'short',
        prompt: en ? 'Before you run it: what will this page’s code print?' : 'Run pannradhukku munnaadi: indha page code enna print pannum?',
        options: [],
        answer: expected,
        explanation: en ? `It prints: ${expected}` : `Idhu print aagum: ${expected}`,
        conceptId: cid,
        conceptLabel: label,
        source: 'page',
        check: 'output',
        pageId: page.id,
      })
    }
  }
  return out
}

/** Picks up to `n` questions, weakest concepts first, one per concept before repeats. */
export function pickQuiz(items: QuizItem[], mastery: (conceptId: string) => number, n = 3): QuizItem[] {
  const sorted = [...items].sort((a, b) => mastery(a.conceptId) - mastery(b.conceptId))
  const first: QuizItem[] = []
  const rest: QuizItem[] = []
  const used = new Set<string>()
  for (const q of sorted) {
    if (used.has(q.conceptId)) rest.push(q)
    else {
      used.add(q.conceptId)
      first.push(q)
    }
  }
  return [...first, ...rest].slice(0, Math.max(2, Math.min(3, n)))
}

export function keywordScore(answer: string, expected: string) {
  const want = contentWords(expected)
  if (want.length === 0) return norm(answer) === norm(expected) ? 1 : 0
  const got = contentWords(answer)
  let hit = 0
  for (const w of want) {
    if (got.some((g) => g === w || (w.length >= 5 && g.length >= 5 && g.slice(0, 5) === w.slice(0, 5)))) hit++
  }
  return hit / want.length
}

export interface GradeResult {
  correct: boolean
  score: number
  gradedBy: 'option' | 'keywords' | 'output' | 'rubric'
}

/** Local grading (no model): exact option, output match, or keyword overlap ≥ 0.5. */
export function gradeLocal(item: QuizItem, given: string): GradeResult {
  const g = (given ?? '').trim()
  if (item.kind === 'mcq') {
    const chosen = item.options.find((o) => norm(o) === norm(g))
    return { correct: Boolean(chosen && norm(chosen) === norm(item.answer)), score: chosen && norm(chosen) === norm(item.answer) ? 1 : 0, gradedBy: 'option' }
  }
  if (item.check === 'output') {
    const exact = norm(g) === norm(item.answer)
    const score = exact ? 1 : keywordScore(g, item.answer) * 0.8
    return { correct: score >= 0.99, score, gradedBy: 'output' }
  }
  const score = g ? keywordScore(g, item.answer) : 0
  return { correct: score >= 0.5, score, gradedBy: 'keywords' }
}

const PRAISE: Record<Lang, string[]> = {
  tanglish: ['Correct-a sonneenga! ', 'Super, sariyaana answer! ', 'Semma! Correct. '],
  english: ['That’s right! ', 'Exactly — well done. ', 'Correct, nice work! '],
}

/** Encouraging feedback in the teacher's voice (same wording as the backend). */
export function feedbackText(item: Pick<QuizItem, 'id' | 'answer' | 'explanation'>, result: { correct: boolean; score: number }, lang: Lang, reason = '') {
  const pick = hash(item.id)
  const why = reason || item.explanation
  if (result.correct) return (PRAISE[lang][pick % 3] + why).trim()
  if (result.score >= 0.3) {
    return ((lang === 'tanglish' ? `Kitta vandhuteenga! Full answer: ${item.answer}. ` : `Close! The full answer is: ${item.answer}. `) + why).trim()
  }
  return (
    (lang === 'tanglish' ? `Paravaala, idhu konjam tricky. Correct answer: ${item.answer}. ` : `Not quite — the answer is: ${item.answer}. `) +
    why +
    (lang === 'tanglish' ? ' Innoru thadava indha part-a paakalaam.' : ' Let’s look at this part again soon.')
  ).trim()
}

/** Short spoken/visible line when a quiz ends. */
export function quizSummaryLine(correct: number, total: number, lang: Lang) {
  if (lang === 'english') {
    if (correct === total) return `All ${total} right — brilliant! You’ve got this page.`
    if (correct === 0) return `That one was tough. We’ll review these ideas again soon — no worries.`
    return `${correct} of ${total} right. Good effort — I’ll bring the tricky ones back for a quick review.`
  }
  if (correct === total) return `${total}-um correct — semma! Indha page ungalukku nalla puriyudhu.`
  if (correct === 0) return `Idhu konjam kashtama irundhuchu. Paravaala, seekiram innoru thadava review pannalaam.`
  return `${total}-la ${correct} correct. Nalla try! Tricky questions-a apram quick review-la thirumba kekkuren.`
}

export function styleInstruction(style: TeachingStyle, depth: TeachingDepth) {
  return `${STYLE_LABEL[style]} · ${depth}`
}
