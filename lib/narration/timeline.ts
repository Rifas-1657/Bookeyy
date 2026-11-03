import type { WordTiming } from '@/lib/contracts/book-events'
import { wordDelayMs, type NarrationWord } from './tokenize'

/** A word anchored to a time range inside one audio segment (ms, relative to the segment start). */
export interface TimedWord {
  wordIndex: number
  startMs: number
  endMs: number
}

const LEAD_IN_MS = 120
const TAIL_MS = 250

/**
 * Spreads `words` over `durationMs` using the same per-word weights the timed
 * simulation uses (longer words and sentence ends take longer). Used when the
 * audio has no word timings (e.g. the prebuilt demo mp3s).
 */
export function estimateTimings(words: NarrationWord[], durationMs: number): TimedWord[] {
  if (words.length === 0 || !(durationMs > 0)) return []
  const weights = words.map((word) => wordDelayMs(word.text, 1))
  const total = weights.reduce((sum, value) => sum + value, 0)
  const lead = Math.min(LEAD_IN_MS, durationMs * 0.05)
  const usable = Math.max(1, durationMs - lead - Math.min(TAIL_MS, durationMs * 0.05))
  let cursor = lead
  return words.map((word, i) => {
    const span = (weights[i] / total) * usable
    const timed = { wordIndex: word.index, startMs: cursor, endMs: cursor + span }
    cursor += span
    return timed
  })
}

/**
 * Maps backend timings onto the display words of one segment.
 *
 * - Timings carrying `wordIndex` are trusted as-is.
 * - Otherwise they are matched in order; when the TTS produced a different
 *   number of tokens (e.g. it spoke Tamil script for a Tanglish line) they are
 *   spread proportionally so every display word still gets a time.
 * - Timings expressed on a later clock (segment- or page-relative) are shifted
 *   so the first word starts near zero.
 */
export function alignTimings(raw: WordTiming[] | undefined, displayWords: NarrationWord[]): TimedWord[] | null {
  if (!raw || raw.length === 0 || displayWords.length === 0) return null
  const sorted = [...raw].sort((a, b) => a.startMs - b.startMs)
  const offset = sorted[0].startMs > 1500 ? sorted[0].startMs - LEAD_IN_MS : 0
  const firstIndex = displayWords[0].index
  const lastIndex = displayWords[displayWords.length - 1].index

  if (sorted.every((t) => typeof t.wordIndex === 'number')) {
    const inRange = sorted.filter((t) => t.wordIndex! >= firstIndex && t.wordIndex! <= lastIndex)
    if (inRange.length > 0) {
      return fillGaps(
        inRange.map((t) => ({ wordIndex: t.wordIndex!, startMs: t.startMs - offset, endMs: t.endMs - offset })),
        displayWords,
      )
    }
  }

  const n = sorted.length
  const m = displayWords.length
  if (n === m) {
    return displayWords.map((word, i) => ({
      wordIndex: word.index,
      startMs: sorted[i].startMs - offset,
      endMs: sorted[i].endMs - offset,
    }))
  }
  // Proportional mapping: display word i covers the raw range it projects onto.
  const start = sorted[0].startMs - offset
  const end = sorted[n - 1].endMs - offset
  return displayWords.map((word, i) => {
    const from = sorted[Math.min(n - 1, Math.floor((i * n) / m))]
    const to = sorted[Math.min(n - 1, Math.max(0, Math.ceil(((i + 1) * n) / m) - 1))]
    return {
      wordIndex: word.index,
      startMs: Math.max(start, from.startMs - offset),
      endMs: Math.min(end, Math.max(from.endMs, to.endMs) - offset),
    }
  })
}

function fillGaps(timed: TimedWord[], words: NarrationWord[]): TimedWord[] {
  const byIndex = new Map(timed.map((t) => [t.wordIndex, t]))
  const out: TimedWord[] = []
  let last: TimedWord | null = null
  for (const word of words) {
    const hit = byIndex.get(word.index)
    if (hit) {
      out.push(hit)
      last = hit
    } else {
      const at: number = last ? last.endMs : 0
      const filled: TimedWord = { wordIndex: word.index, startMs: at, endMs: at + 1 }
      out.push(filled)
      last = filled
    }
  }
  return out
}

/** Index into `timed` of the word being spoken at `ms` (binary search); -1 before the first word. */
export function timedWordAt(timed: TimedWord[], ms: number): number {
  let lo = 0
  let hi = timed.length - 1
  let found = -1
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    if (timed[mid].startMs <= ms) {
      found = mid
      lo = mid + 1
    } else hi = mid - 1
  }
  return found
}
