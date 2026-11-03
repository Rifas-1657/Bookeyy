/**
 * Mock mode (no backend): the R3 learner model kept in this browser (localStorage).
 * Same algorithms as the server (lib/learning/model.ts mirrors app/personalization/*):
 * BKT mastery per concept, the contextual teaching-style bandit, FSRS review cards and
 * page-derived graded quizzes. Nothing here leaves the device.
 */
import { readStorage, storageKeys, writeStorage } from '@/lib/storage'
import {
  bktUpdate,
  chooseStyle,
  feedbackText,
  GUESS_SHORT,
  isDue,
  LEARNING,
  learnStep,
  LEVEL_PRIOR,
  mentionedConcepts,
  NEW_CARD,
  pageConcepts,
  pageQuizItems,
  pickQuiz,
  preferredStyle,
  retrievability,
  reviewCard,
  reviewRating,
  rewardFor,
  rewardPosteriors,
  SLOW_MS,
  SOFT_EVIDENCE,
  statusFor,
  gradeLocal,
  type EvidenceKind,
  type Lang,
  type LearnerLevel,
  type Posteriors,
  type QuizItem,
  type QuizPage,
  type ReviewCard,
  type StyleChoice,
  type TeachingDepth,
  type TeachingStyle,
} from './model'
import type { ConceptView, DueReview, GradeResponse, LearnerSummary, QuizResponse } from './types'

interface LocalConcept {
  label: string
  pageId: string
  chapter: number
  mastery: number
  attempts: number
  correct: number
  confusion: number
  lastSeen: number | null
}

interface LocalCourse {
  concepts: Record<string, LocalConcept>
  cards: Record<string, ReviewCard>
  posteriors: Posteriors
  lastShown: (StyleChoice & { at: number; rewards: number }) | null
  /** Recent confusion signals (simpler / repeat / question) as [kind, at]. */
  signals: Array<[string, number]>
  issued: Record<string, QuizItem & { review?: boolean; answered?: boolean; lang?: Lang }>
  /** The question each concept was last missed on (re-asked in reviews). */
  missed: Record<string, QuizItem>
}

type LocalStore = Record<string, LocalCourse>

export const LEARNING_EVENT = 'bookey-learning'
const ATTRIBUTION_MS = 60 * 60_000
const CONFUSION_MS = 30 * 60_000

function emptyCourse(): LocalCourse {
  return { concepts: {}, cards: {}, posteriors: {}, lastShown: null, signals: [], issued: {}, missed: {} }
}

function load(): LocalStore {
  return readStorage<LocalStore>(storageKeys.learnerModel, {})
}

function save(store: LocalStore) {
  writeStorage(storageKeys.learnerModel, store)
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(LEARNING_EVENT))
}

function update<T>(courseId: string, fn: (course: LocalCourse) => T): T {
  const store = load()
  const course = { ...emptyCourse(), ...(store[courseId] ?? {}) }
  const out = fn(course)
  store[courseId] = course
  save(store)
  return out
}

function ensureConcepts(course: LocalCourse, page: QuizPage, level: LearnerLevel) {
  for (const [id, label] of pageConcepts(page)) {
    course.concepts[id] ??= {
      label,
      pageId: page.id,
      chapter: page.chapterNumber,
      mastery: LEVEL_PRIOR[level],
      attempts: 0,
      correct: 0,
      confusion: 0,
      lastSeen: null,
    }
  }
}

function confusion(course: LocalCourse, now = Date.now()) {
  return course.signals
    .filter(([, at]) => now - at < CONFUSION_MS)
    .reduce((sum, [kind]) => sum + (kind === 'question' ? 0.5 : 1), 0)
}

function rewardLastShown(course: LocalCourse, kind: EvidenceKind, correct?: boolean | null) {
  const reward = rewardFor(kind, correct)
  const shown = course.lastShown
  if (!reward || !shown || Date.now() - shown.at > ATTRIBUTION_MS || shown.rewards >= 4) return
  course.posteriors = rewardPosteriors(course.posteriors, shown.bucket, shown.arm, reward[0], reward[1])
  shown.rewards += 1
}

export interface LocalEvidence {
  kind: EvidenceKind
  page: QuizPage
  conceptIds?: string[]
  correct?: boolean | null
  responseMs?: number
  text?: string
  guess?: number
}

function applyOne(course: LocalCourse, ev: LocalEvidence, level: LearnerLevel) {
  ensureConcepts(course, ev.page, level)
  const concepts = pageConcepts(ev.page)
  const own = concepts.map(([id]) => id)
  let targets = (ev.conceptIds ?? []).filter((id) => id in course.concepts)
  let mult = 1
  if (targets.length === 0) targets = ev.text ? mentionedConcepts(ev.text, concepts) : []
  if (targets.length === 0) {
    targets = own
    mult = 0.5
  }
  const now = Date.now()
  for (const id of targets) {
    const c = course.concepts[id]
    if (!c) continue
    if (ev.kind === 'quiz_answer' || ev.kind === 'review_answer') {
      const slow = Boolean(ev.correct) && (ev.responseMs ?? 0) > SLOW_MS
      c.mastery = bktUpdate(c.mastery, Boolean(ev.correct), { guess: ev.guess ?? GUESS_SHORT, weight: (slow ? 0.6 : 1) * mult })
      c.attempts += 1
      if (ev.correct) c.correct += 1
      const rating = reviewRating(Boolean(ev.correct), {
        hasCard: id in course.cards,
        review: ev.kind === 'review_answer',
        slow,
        mastery: c.mastery,
      })
      if (rating) course.cards[id] = reviewCard(course.cards[id] ?? NEW_CARD, rating, now)
    } else if (ev.kind === 'code_run') {
      c.mastery = bktUpdate(c.mastery, Boolean(ev.correct), { guess: 0.3, weight: (ev.correct ? 0.5 : 0.3) * mult })
    } else if (ev.kind === 'page_complete') {
      c.mastery = learnStep(c.mastery, 0.5 * mult)
    } else {
      const soft = SOFT_EVIDENCE[ev.kind]
      if (!soft) continue
      c.mastery = bktUpdate(c.mastery, soft[0], { guess: 0.25, weight: soft[1] * mult })
      if (soft[2]) c.confusion += 1
    }
    c.lastSeen = now
  }
  if (ev.kind === 'simpler' || ev.kind === 'repeat' || ev.kind === 'question') {
    course.signals = [...course.signals.filter(([, at]) => now - at < CONFUSION_MS), [ev.kind, now] as [string, number]].slice(-30)
  }
  rewardLastShown(course, ev.kind, ev.correct)
  return targets
}

export function recordLocal(courseId: string, ev: LocalEvidence, level: LearnerLevel = 'new') {
  update(courseId, (course) => applyOne(course, ev, level))
}

/** Mock tutor hook: records the question as evidence and picks the explanation style. */
export function chooseLocalStyle(
  courseId: string,
  page: QuizPage,
  question: string,
  opts: { simpler: boolean; language: Lang; level?: LearnerLevel },
): StyleChoice {
  const level = opts.level ?? 'new'
  return update(courseId, (course) => {
    applyOne(course, { kind: opts.simpler ? 'simpler' : 'question', page, text: question }, level)
    const concepts = pageConcepts(page)
    const ids = mentionedConcepts(question, concepts)
    const pool = (ids.length ? ids : concepts.map(([id]) => id)).map((id) => course.concepts[id]?.mastery ?? LEVEL_PRIOR[level])
    const ctx = { level, mastery: Math.min(...pool), language: opts.language, confused: confusion(course) >= 2 }
    const avoid = opts.simpler ? course.lastShown?.style : undefined
    const choice = chooseStyle(ctx, course.posteriors, {
      forceDepth: opts.simpler ? 'simpler' : undefined,
      avoidStyle: avoid as TeachingStyle | undefined,
    })
    course.lastShown = { ...choice, at: Date.now(), rewards: 0 }
    return choice
  })
}

function conceptView(id: string, c: LocalConcept): ConceptView {
  const evidence = Boolean(c.attempts || c.confusion || c.lastSeen)
  return {
    id,
    label: c.label,
    pageId: c.pageId,
    chapter: c.chapter,
    mastery: Math.round(c.mastery * 1000) / 1000,
    attempts: c.attempts,
    correct: c.correct,
    confusion: c.confusion,
    status: statusFor(c.mastery, evidence),
    lastSeenAt: c.lastSeen ? new Date(c.lastSeen).toISOString() : null,
  }
}

export function localDueReviews(courseTitles: (courseId: string) => string, courseId?: string): DueReview[] {
  const store = load()
  const out: DueReview[] = []
  for (const [cid, course] of Object.entries(store)) {
    if (courseId && cid !== courseId) continue
    for (const [conceptId, card] of Object.entries(course.cards ?? {})) {
      const c = course.concepts?.[conceptId]
      if (!c || !isDue(card)) continue
      out.push({
        courseId: cid,
        courseTitle: courseTitles(cid),
        conceptId,
        label: c.label,
        pageId: c.pageId,
        chapter: c.chapter,
        mastery: c.mastery,
        lapses: card.lapses,
        retrievability: Math.round(retrievability(card) * 1000) / 1000,
        lastReviewed: card.lastReview ? new Date(card.lastReview).toISOString().slice(0, 10) : null,
      })
    }
  }
  return out.sort((a, b) => a.retrievability - b.retrievability || a.mastery - b.mastery)
}

export function localSummary(
  courseId: string,
  opts: { level: LearnerLevel; language: Lang; chapterTitle: (n: number) => string; courseTitle: (id: string) => string },
): LearnerSummary {
  const course = { ...emptyCourse(), ...(load()[courseId] ?? {}) }
  const concepts = Object.entries(course.concepts)
    .map(([id, c]) => conceptView(id, c))
    .sort((a, b) => (a.chapter ?? 0) - (b.chapter ?? 0) || a.label.localeCompare(b.label))
  const seen = concepts.filter((c) => c.status !== 'new')
  const mean = seen.length ? seen.reduce((s, c) => s + c.mastery, 0) / seen.length : null
  const byChapter = new Map<number, ConceptView[]>()
  for (const c of concepts) byChapter.set(c.chapter ?? 0, [...(byChapter.get(c.chapter ?? 0) ?? []), c])
  const conf = confusion(course)
  const style = preferredStyle(
    { level: opts.level, mastery: mean ?? LEVEL_PRIOR[opts.level], language: opts.language, confused: conf >= 2 },
    course.posteriors,
  )
  const pooled = course.posteriors['ts:all'] ?? {}
  return {
    courseId,
    level: opts.level,
    language: opts.language,
    overallMastery: mean,
    concepts,
    chapters: [...byChapter.entries()]
      .sort(([a], [b]) => a - b)
      .map(([number, cs]) => ({
        number,
        title: opts.chapterTitle(number),
        mastery: cs.reduce((s, c) => s + c.mastery, 0) / cs.length,
        seen: cs.filter((c) => c.status !== 'new').length,
        total: cs.length,
        mastered: cs.filter((c) => c.status === 'mastered').length,
      })),
    weak: seen
      .filter((c) => c.status === 'weak' || (c.confusion >= 2 && c.mastery < 0.8))
      .sort((a, b) => a.mastery - b.mastery)
      .slice(0, 5),
    strong: concepts.filter((c) => c.status === 'mastered').slice(0, 5),
    recentConfusion: conf,
    style: {
      arm: style.arm,
      style: style.style,
      depth: style.depth as TeachingDepth,
      expected: style.expected,
      bucket: style.bucket,
      evidence: Object.values(pooled).reduce((s, [a, b]) => s + a + b - 2, 0),
    },
    dueReviews: localDueReviews(opts.courseTitle, courseId),
    model: 'bkt',
  }
}

export function localQuiz(courseId: string, page: QuizPage | null, lang: Lang, mode: 'page' | 'review', pages: QuizPage[] = []): QuizResponse {
  return update(courseId, (course) => {
    let items: QuizItem[] = []
    if (mode === 'review') {
      const due = localDueReviews(() => '', courseId).slice(0, 3)
      for (const d of due) {
        const missed = course.missed[d.conceptId]
        const src = pages.find((p) => p.id === d.pageId)
        const item = missed ?? (src ? pageQuizItems(src, lang, 'review', new Set([d.conceptId]))[0] : undefined)
        if (item) items.push({ ...item, id: `review-${d.conceptId}-${Date.now()}`, source: 'review' })
      }
    } else if (page) {
      ensureConcepts(course, page, 'new')
      items = pickQuiz(pageQuizItems(page, lang, `q${Date.now()}`), (id) => course.concepts[id]?.mastery ?? 0.2)
    }
    const kept = Object.entries(course.issued).slice(-30)
    course.issued = Object.fromEntries(kept)
    for (const item of items) course.issued[item.id] = { ...item, review: mode === 'review', lang }
    return {
      quizId: items.length ? `local-${Date.now()}` : null,
      mode,
      questions: items.map(({ answer: _a, explanation: _e, check: _c, ...q }) => q),
    }
  })
}

export function localGrade(courseId: string, questionId: string, answer: string, responseMs: number | undefined, lang: Lang, page: QuizPage | undefined, level: LearnerLevel = 'new'): GradeResponse | null {
  return update(courseId, (course) => {
    const item = course.issued[questionId]
    if (!item) return null
    const result = gradeLocal(item, answer)
    const counted = !item.answered
    let mastery: number | null = null
    if (counted && page) {
      item.answered = true
      applyOne(
        course,
        {
          kind: item.review ? 'review_answer' : 'quiz_answer',
          page,
          conceptIds: [item.conceptId],
          correct: result.correct,
          responseMs,
          guess: item.kind === 'mcq' && item.options.length ? 1 / item.options.length : GUESS_SHORT,
        },
        level,
      )
      if (!result.correct) {
        const { review: _r, answered: _a, lang: _l, ...plain } = item
        course.missed[item.conceptId] = plain
      }
      mastery = course.concepts[item.conceptId]?.mastery ?? null
    }
    return {
      questionId,
      correct: result.correct,
      score: result.score,
      gradedBy: result.gradedBy,
      feedback: feedbackText(item, result, item.lang ?? lang),
      answer: item.answer,
      explanation: item.explanation,
      conceptId: item.conceptId,
      conceptLabel: item.conceptLabel,
      counted,
      mastery,
    }
  })
}

/** Has any concept below "learning"? (used to decide whether to nudge a review) */
export function hasLocalWeakness(courseId: string) {
  const course = load()[courseId]
  return Boolean(course && Object.values(course.concepts).some((c) => c.attempts > 0 && c.mastery < LEARNING))
}
