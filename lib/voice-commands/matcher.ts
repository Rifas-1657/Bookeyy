/**
 * Voice/text lesson commands: maps what the learner said (Latin Tanglish,
 * English or Tamil script — STT may return any of them) to a lesson intent.
 *
 * Pure and dependency-free (only erasable TypeScript), so `node --test` can
 * import it directly: see tests/voice-commands.test.mjs.
 *
 * How it decides:
 * 1. normalise: lowercase, Tamil script → Latin, punctuation → spaces, Tamil digits → digits;
 * 2. every token gets a phonetic "skeleton" (th→t, dh→d, voicing merged, doubled letters
 *    collapsed…) so "niruthu", "nirutthu" and "நிறுத்து" all compare equal;
 * 3. each intent has patterns (groups of alternatives); a pattern matches when every group
 *    finds a distinct token (exactly or within a small edit distance);
 * 4. confidence = share of the utterance the pattern explains (filler words count as
 *    explained), lowered for question words, negations ("stop pannaadhe", "don't") and
 *    long sentences. Low confidence → the caller asks the backend or treats it as a question.
 */

export type VoiceIntent =
  | 'stop'
  | 'resume'
  | 'next_page'
  | 'previous_page'
  | 'repeat'
  | 'slower'
  | 'faster'
  | 'simpler'
  | 'run_code'
  | 'show_diagram'
  | 'quiz'
  | 'goto_chapter'
  | 'goto_page'
  | 'question'

export const VOICE_INTENTS: readonly VoiceIntent[] = [
  'stop',
  'resume',
  'next_page',
  'previous_page',
  'repeat',
  'slower',
  'faster',
  'simpler',
  'run_code',
  'show_diagram',
  'quiz',
  'goto_chapter',
  'goto_page',
  'question',
]

export interface IntentArgs {
  /** goto_chapter: chapter number (1-based). */
  chapter?: number
  /** goto_page: page number in the book (1-based); goto_chapter: page within that chapter. */
  page?: number
  /** goto_chapter: relative move ("next chapter" = 1, "previous chapter" = -1). */
  chapterDelta?: number
  /** repeat: 'sentence' (default) or 'page' ("from the beginning"). */
  scope?: 'sentence' | 'page'
}

export interface IntentMatch {
  intent: VoiceIntent
  args: IntentArgs
  /** 0..1 */
  confidence: number
  /** The normalised Latin text that was matched. */
  normalized: string
  /** Which layer decided: the local matcher or the backend classifier. */
  source: 'local' | 'backend' | 'fallback'
}

/** At or above: act on the command without asking anyone. */
export const ACT_THRESHOLD = 0.75
/** Between this and ACT_THRESHOLD the utterance is ambiguous (ask the backend if available). */
export const AMBIGUOUS_THRESHOLD = 0.4

/* ------------------------------------------------------------------------ */
/* Tamil script → Latin                                                      */
/* ------------------------------------------------------------------------ */

const TA_VOWELS: Record<string, string> = {
  'அ': 'a', 'ஆ': 'aa', 'இ': 'i', 'ஈ': 'ii', 'உ': 'u', 'ஊ': 'uu',
  'எ': 'e', 'ஏ': 'ee', 'ஐ': 'ai', 'ஒ': 'o', 'ஓ': 'oo', 'ஔ': 'au',
}
const TA_CONSONANTS: Record<string, string> = {
  'க': 'k', 'ங': 'ng', 'ச': 'ch', 'ஞ': 'nj', 'ட': 't', 'ண': 'n', 'த': 'th', 'ந': 'n',
  'ப': 'p', 'ம': 'm', 'ய': 'y', 'ர': 'r', 'ல': 'l', 'வ': 'v', 'ழ': 'zh', 'ள': 'l',
  'ற': 'r', 'ன': 'n', 'ஜ': 'j', 'ஷ': 'sh', 'ஸ': 's', 'ஹ': 'h',
}
const TA_SIGNS: Record<string, string> = {
  'ா': 'aa', 'ி': 'i', 'ீ': 'ii', 'ு': 'u', 'ூ': 'uu', 'ெ': 'e', 'ே': 'ee', 'ை': 'ai',
  'ொ': 'o', 'ோ': 'oo', 'ௌ': 'au',
}
const TA_PULLI = '்'
const TA_DIGITS = '௦௧௨௩௪௫௬௭௮௯'

/** Best-effort Tamil → Latin (enough for matching; not a display transliteration). */
export function tamilToLatin(text: string): string {
  let out = ''
  const chars = Array.from(text)
  for (let i = 0; i < chars.length; i += 1) {
    const ch = chars[i]
    const digit = TA_DIGITS.indexOf(ch)
    if (digit >= 0) {
      out += String(digit)
      continue
    }
    if (TA_VOWELS[ch]) {
      out += TA_VOWELS[ch]
      continue
    }
    const consonant = TA_CONSONANTS[ch]
    if (consonant) {
      const next = chars[i + 1]
      if (next === TA_PULLI) {
        out += consonant
        i += 1
      } else if (next && TA_SIGNS[next]) {
        out += consonant + TA_SIGNS[next]
        i += 1
      } else {
        out += `${consonant}a`
      }
      continue
    }
    if (TA_SIGNS[ch] || ch === TA_PULLI || ch === 'ஃ') continue
    out += ch
  }
  return out
}

/* ------------------------------------------------------------------------ */
/* Normalisation and fuzzy comparison                                        */
/* ------------------------------------------------------------------------ */

export function normalizeUtterance(raw: string): string {
  return tamilToLatin(raw.normalize('NFC'))
    .toLowerCase()
    .replace(/['’`]/g, '')
    .replace(/(\d+)(st|nd|rd|th)\b/g, '$1')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

/** Phonetic skeleton: spellings STT / Tanglish writers mix up compare equal. */
export function skeleton(word: string): string {
  let s = word.toLowerCase()
  s = s
    .replace(/ph/g, 'f')
    .replace(/sh/g, 's')
    .replace(/zh/g, 'l')
    .replace(/ch/g, 's')
    .replace(/([tdkgb])h/g, '$1')
    .replace(/ck/g, 'k')
    .replace(/q/g, 'k')
    .replace(/c/g, 'k')
    .replace(/x/g, 'ks')
    .replace(/z/g, 's')
    .replace(/w/g, 'v')
    // Tamil script has no voicing distinction, so neither do we.
    .replace(/b/g, 'p')
    .replace(/d/g, 't')
    .replace(/g/g, 'k')
    .replace(/j/g, 's')
    .replace(/ee|ii/g, 'i')
    .replace(/oo|uu/g, 'u')
    .replace(/aa/g, 'a')
    .replace(/(.)\1+/g, '$1')
  if (s.length > 2) s = s.replace(/y$/, 'i')
  return s
}

export function editDistance(a: string, b: string): number {
  if (a === b) return 0
  if (!a.length) return b.length
  if (!b.length) return a.length
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i)
  for (let i = 1; i <= a.length; i += 1) {
    const cur = [i]
    for (let j = 1; j <= b.length; j += 1) {
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1))
    }
    prev = cur
  }
  return prev[b.length]
}

function allowedDistance(alias: string): number {
  if (alias.length >= 8) return 2
  if (alias.length >= 5) return 1
  return 0
}

/** Words that only ever match exactly (so "what" never fuzzes into "wait"). */
const QUESTION_WORDS = new Set([
  'what', 'whats', 'why', 'how', 'who', 'whom', 'when', 'where', 'which', 'whose',
  'enna', 'ennathu', 'ennadhu', 'yen', 'yean', 'en', 'epdi', 'eppadi', 'eppdi', 'yeppadi',
  'edhu', 'ethu', 'yaaru', 'yaru', 'enga', 'engae', 'evlo', 'evvalavu', 'eppo', 'eppothu',
])

const NEGATIONS = new Set([
  'dont', 'not', 'no', 'never', 'venam', 'vendam', 'vendaam', 'venaam', 'vena', 'illa',
  'illai', 'kudathu', 'koodathu', 'kudaathu', 'nahi',
])

/** Words that don't change what the learner wants. */
const FILLERS = new Set([
  'please', 'pls', 'plz', 'kindly', 'ok', 'okay', 'okey', 'k', 'sari', 'seri', 'sariya', 'hmm', 'hm',
  'um', 'umm', 'uh', 'ah', 'aah', 'oh', 'hey', 'hi', 'hello', 'nila', 'miss', 'mam', 'maam', 'madam',
  'sir', 'teacher', 'akka', 'anna', 'da', 'di', 'pa', 'ma', 'ya', 'yeah', 'yes', 'the', 'a', 'an',
  'to', 'can', 'could', 'would', 'will', 'you', 'u', 'me', 'my', 'us', 'i', 'we', 'let', 'lets',
  'just', 'now', 'ippo', 'ippa', 'konjam', 'little', 'bit', 'some', 'go', 'for', 'of', 'on', 'it',
  'this', 'that', 'idhu', 'ithu', 'adha', 'atha', 'andha', 'antha', 'indha', 'intha', 'inda', 'la',
  'le', 'pannu', 'pannunga', 'pannungo', 'panni', 'pannalaam', 'pannalam', 'pannuga', 'panra',
  'panunga', 'panu', 'karo', 'do', 'and', 'then', 'also', 'is', 'are', 'be', 'again', 'more', 'innum',
  'explain', 'tell', 'say', 'number', 'once', 'try', 'want', 'need', 'venum', 'vendum',
  'kodunga', 'thaan', 'than', 'tha', 'ku', 'kku', 'at', 'in', 'with', 'from', 'up', 'down', 'right',
  'one', 'oru', 'po', 'ponga', 'polaam', 'polam', 'move', 'take', 'show', 'kaattu', 'kaatunga',
])

/* ------------------------------------------------------------------------ */
/* Intent patterns                                                           */
/* ------------------------------------------------------------------------ */

const W = {
  stop: 'stop|pause|wait|waitu|halt|niruthu|niruththu|nirutthu|niruthunga|niruthungo|nirutha|niruttu|nillu|nillunga|pothum|podhum|enough|quiet|silence|amaidhi|amaithi|shush|veyit|staap|paas',
  resume: 'continue|kantinyu|kanteenyu|resume|play|start|proceed|unpause|sollunga|sollu|sollungo|solunga|thodarunga|thodaru|thodar|thodangu|thodanga|aarambi|aarambinga|aarambikalaam',
  next: 'next|aduththa|adutha|adutta|adhutha|aduthu|adutthu|forward|nekst',
  prev: 'previous|prev|munnaadi|munnadi|mundhaiya|mundhina|mundhaya|before|back|pinnadi|pinnaadi|munthaiya|preevius',
  page: 'page|pages|pakkam|pakam|pej|peej|peij|slide',
  chapter: 'chapter|chapters|adhyayam|athiyayam|adhyaayam|lesson|unit|chaptar|saptar',
  repeat: 'repeat|replay|thirumba|thirumbavum|thirumbi|marupadiyum|marubadiyum|repeattu|ripeet',
  slow: 'slow|sloo|slowly|slower|slowa|medhuva|methuva|methuvaa|medhuvaa|mella|mellama|mellisa|mellamaa|meduva',
  fast: 'fast|paast|faster|fasta|quick|quickly|quicker|vegama|veegama|vegamaa|seekiram|sikkiram|seekkiram|seekram|hurry',
  speed: 'speed|speedu|pace|vegam',
  simple: 'simple|simpil|simpler|simplea|simply|easy|easier|easya|elimaiya|elimaya|elimaiyaa|elimai|basic|basically|layman',
  confused: 'puriyala|puriyalai|puriyale|puriyalaye|puriyaledhu|puriyavillai|confusing|confused|unclear',
  run: 'run|execute|ottu|oottu|odavidu|odu|ran|runnu',
  code: 'code|program|kod|koot|kodu|script|programme',
  diagram: 'diagram|tayakiram|picture|visual|visuals|chart|flowchart|padam|figure|graph|diagramme|daiyagram|image|drawing',
  quiz: 'quiz|test|exam|kuvis|kwiz|quizz|tesst',
  questions: 'question|questions|kelvi|kelvigal|kezhvi|qs',
  ask: 'ask|kelu|kelunga|kekkanum|kekkunga|kezhunga',
  beginning: 'beginning|first|mudhal|mudhalla|modhal|aarambathula|aarambathilirundhu|starting',
} as const

interface Pattern {
  intent: VoiceIntent
  groups: string[][]
  args?: IntentArgs
}

function p(intent: VoiceIntent, spec: string, args?: IntentArgs): Pattern {
  const groups = spec.split(' ').map((group) => {
    const resolved = group.replace(/\$(\w+)/g, (_, key: keyof typeof W) => W[key])
    // A leading '=' marks an alias that must match exactly (no fuzzy edit distance).
    return resolved.split('|').map((alias) => (alias.startsWith('=') ? `=${skeleton(alias.slice(1))}` : skeleton(alias)))
  })
  return { intent, groups, args }
}

const PATTERNS: Pattern[] = [
  // stop
  p('stop', '$stop'),
  p('stop', 'hold on|up'),
  p('stop', 'shut up'),
  p('stop', 'minute|min|second|sec|nimisham|nimidam'),
  p('stop', '$stop minute|min|second|sec|nimisham|nimidam'),
  p('stop', 'be quiet'),
  // resume
  p('resume', '$resume'),
  p('resume', 'go on'),
  p('resume', 'carry on'),
  p('resume', 'keep going'),
  p('resume', '$resume $resume'),
  // pages
  p('next_page', '$next'),
  p('next_page', '$next $page'),
  p('next_page', 'turn $page'),
  p('next_page', 'skip'),
  p('next_page', 'move on'),
  p('previous_page', '$prev'),
  p('previous_page', '$prev $page'),
  p('previous_page', 'go back'),
  // chapters (relative)
  p('goto_chapter', '$next $chapter', { chapterDelta: 1 }),
  p('goto_chapter', '$prev $chapter', { chapterDelta: -1 }),
  // repeat
  p('repeat', '$repeat'),
  p('repeat', 'again'),
  p('repeat', '$repeat $resume'),
  p('repeat', 'once more'),
  p('repeat', 'one more time'),
  p('repeat', 'innoru|inoru thadava|dhadava|thadavai|murai|time'),
  p('repeat', 'say that again'),
  p('repeat', 'come again'),
  p('repeat', '$beginning $resume', { scope: 'page' }),
  p('repeat', 'from $beginning', { scope: 'page' }),
  p('repeat', 'restart', { scope: 'page' }),
  p('repeat', '$repeat $page', { scope: 'page' }),
  p('repeat', '$repeat $beginning', { scope: 'page' }),
  // speed
  p('slower', '$slow'),
  p('slower', '$slow $resume'),
  p('slower', '$speed kammi|korai|korainga|kuraingal|kurainga|kuraiya|reduce|less|lower|decrease'),
  p('slower', 'reduce|lower|decrease $speed'),
  p('slower', 'too|romba $fast'),
  p('faster', '$fast'),
  p('faster', '$fast $resume'),
  p('faster', '$speed jaasthi|jasthi|adhigam|athigam|increase|more|higher|up|ethu|yethu|ethunga'),
  p('faster', 'increase|raise $speed'),
  p('faster', 'too|romba $slow'),
  // simpler
  p('simpler', '$simple'),
  p('simpler', '$simple $resume'),
  p('simpler', '$confused'),
  p('simpler', '$repeat $simple'),
  p('simpler', 'dont understand|understood|get'),
  p('simpler', 'not clear|understand|understood'),
  p('simpler', 'didnt understand|get'),
  // code
  p('run_code', '$run'),
  p('run_code', '$run $code'),
  p('run_code', 'start|begin $code'),
  p('run_code', 'output'),
  p('run_code', '$code output'),
  // diagram
  p('show_diagram', '$diagram'),
  p('show_diagram', 'show|kaattu|kaatu|kaminga|kaanbi|display|open|see|paarkanum|paakanum $diagram'),
  // quiz
  p('quiz', '$quiz'),
  p('quiz', '$quiz $ask'),
  p('quiz', '$ask =questions|=kelvigal|=qs|=kelvi'),
  p('quiz', '=questions|=kelvigal|=kelvi $ask'),
  p('quiz', 'check knowledge|understanding'),
]

/** Priority when two patterns explain the utterance equally well (earlier wins). */
const PRIORITY: VoiceIntent[] = [
  'goto_chapter',
  'goto_page',
  'simpler',
  'quiz',
  'repeat',
  'run_code',
  'show_diagram',
  'slower',
  'faster',
  'next_page',
  'previous_page',
  'stop',
  'resume',
  'question',
]

/* ------------------------------------------------------------------------ */
/* Numbers ("chapter 3", "moonu chapter", "page anju", "3rd chapter")        */
/* ------------------------------------------------------------------------ */

const NUMBER_WORDS: Array<[number, string]> = [
  [1, 'one|first|onnu|ondru|onru|mudhal|modhal|onnavathu|mudhalavathu'],
  [2, 'two|second|rendu|irandu|irantu|rendavathu|irandavathu'],
  [3, 'three|third|moonu|munu|moondru|munru|moonavathu|munravathu'],
  [4, 'four|fourth|naalu|nalu|naangu|nangu|naalavathu|nangavathu'],
  [5, 'five|fifth|anju|ainthu|aindhu|anjavathu|ainthavathu'],
  [6, 'six|sixth|aaru|aru|aaravathu'],
  [7, 'seven|seventh|ezhu|elu|ezhavathu'],
  [8, 'eight|eighth|ettu|ettavathu'],
  [9, 'nine|ninth|onbathu|ombodhu|ombathu|onbadhu'],
  [10, 'ten|tenth|pathu|paththu|pathavathu'],
  [11, 'eleven|eleventh|pathinonnu|pathinondru'],
  [12, 'twelve|twelfth|pannendu|panirendu'],
  [13, 'thirteen|pathimoonu'],
  [14, 'fourteen|pathinaalu'],
  [15, 'fifteen|pathinanju'],
  [16, 'sixteen'],
  [17, 'seventeen'],
  [18, 'eighteen'],
  [19, 'nineteen'],
  [20, 'twenty|irupathu'],
]
const NUMBER_LOOKUP: Array<[number, string[]]> = NUMBER_WORDS.map(([n, words]) => [n, words.split('|').map(skeleton)])

export function parseNumberToken(token: string): number | null {
  if (/^\d{1,3}$/.test(token)) return Number(token)
  const sk = skeleton(token)
  for (const [n, aliases] of NUMBER_LOOKUP) {
    if (aliases.some((alias) => alias === sk || (alias.length >= 5 && editDistance(alias, sk) <= 1))) return n
  }
  return null
}

/* ------------------------------------------------------------------------ */
/* Matching                                                                  */
/* ------------------------------------------------------------------------ */

interface Token {
  raw: string
  sk: string
  filler: boolean
  question: boolean
  negation: boolean
}

function tokenize(normalized: string): Token[] {
  return normalized
    .split(' ')
    .filter(Boolean)
    .map((raw) => ({
      raw,
      sk: skeleton(raw),
      filler: FILLERS.has(raw),
      question: QUESTION_WORDS.has(raw),
      negation: NEGATIONS.has(raw) || /(aadhe|aathe|aadheenga|aatheenga|kkadhe|kaadhe)$/.test(raw),
    }))
}

/** Distance of `token` to the closest alias in `group`, or -1 if none is close enough. */
function groupDistance(token: Token, group: string[]): number {
  let best = -1
  for (const alias of group) {
    if (alias[0] === '=') {
      if (alias.slice(1) === token.sk) return 0
      continue
    }
    if (alias === token.sk) return 0
    if (token.question) continue
    const allowed = allowedDistance(alias)
    if (!allowed || Math.abs(alias.length - token.sk.length) > allowed) continue
    const d = editDistance(alias, token.sk)
    if (d <= allowed && (best < 0 || d < best)) best = d
  }
  return best
}

interface Candidate {
  intent: VoiceIntent
  args: IntentArgs
  covered: number
  consumed: Set<number>
  fuzz: number
  groups: number
}

function matchPattern(tokens: Token[], pattern: Pattern): Candidate | null {
  const consumed = new Set<number>()
  let fuzz = 0
  // Order-insensitive: each group takes the best still-free token.
  for (const group of pattern.groups) {
    let pick = -1
    let pickDistance = Infinity
    for (let i = 0; i < tokens.length; i += 1) {
      if (consumed.has(i)) continue
      const d = groupDistance(tokens[i], group)
      if (d >= 0 && d < pickDistance) {
        pick = i
        pickDistance = d
        if (d === 0) break
      }
    }
    if (pick < 0) return null
    consumed.add(pick)
    fuzz += pickDistance
  }
  return { intent: pattern.intent, args: { ...(pattern.args ?? {}) }, covered: 0, consumed, fuzz, groups: pattern.groups.length }
}

const PAGE_WORDS = W.page.split('|').map(skeleton)
const CHAPTER_WORDS = W.chapter.split('|').map(skeleton)

function isWord(token: Token, words: string[]) {
  return words.some((alias) => alias === token.sk || (alias.length >= 5 && editDistance(alias, token.sk) <= 1))
}

/** "chapter 3", "3 chapter", "chapter number three", "moonavathu chapter", "chapter 2 page 4". */
function matchGoto(tokens: Token[]): Candidate | null {
  const near = (anchor: number): { index: number; value: number } | null => {
    for (const offset of [1, -1, 2, -2]) {
      const i = anchor + offset
      if (i < 0 || i >= tokens.length) continue
      const value = parseNumberToken(tokens[i].raw)
      if (value !== null && value > 0) return { index: i, value }
    }
    return null
  }
  let chapter: { anchor: number; index: number; value: number } | null = null
  let page: { anchor: number; index: number; value: number } | null = null
  tokens.forEach((token, i) => {
    if (!chapter && isWord(token, CHAPTER_WORDS)) {
      const n = near(i)
      if (n && n.index !== page?.index) chapter = { anchor: i, ...n }
    } else if (!page && isWord(token, PAGE_WORDS)) {
      const n = near(i)
      if (n && n.index !== chapter?.index) page = { anchor: i, ...n }
    }
  })
  const c = chapter as { anchor: number; index: number; value: number } | null
  const pg = page as { anchor: number; index: number; value: number } | null
  if (c) {
    const consumed = new Set([c.anchor, c.index])
    const args: IntentArgs = { chapter: c.value }
    if (pg) {
      consumed.add(pg.anchor)
      consumed.add(pg.index)
      args.page = pg.value
    }
    return { intent: 'goto_chapter', args, covered: 0, consumed, fuzz: 0, groups: consumed.size }
  }
  if (pg) {
    return { intent: 'goto_page', args: { page: pg.value }, covered: 0, consumed: new Set([pg.anchor, pg.index]), fuzz: 0, groups: 2 }
  }
  return null
}

function questionMatch(normalized: string, confidence: number): IntentMatch {
  return { intent: 'question', args: {}, confidence, normalized, source: 'local' }
}

/**
 * Classifies one utterance. Never throws; anything unrecognised is a `question`
 * (with confidence = how sure we are that it is *not* a command).
 */
export function matchIntent(utterance: string): IntentMatch {
  const normalized = normalizeUtterance(utterance ?? '')
  const tokens = tokenize(normalized)
  if (tokens.length === 0) return questionMatch(normalized, 0)
  const hasQuestionMark = /[?？]/.test(utterance)

  const candidates: Candidate[] = []
  const gotoMatch = matchGoto(tokens)
  if (gotoMatch) candidates.push(gotoMatch)
  for (const pattern of PATTERNS) {
    const found = matchPattern(tokens, pattern)
    if (!found) continue
    candidates.push(found)
  }
  if (candidates.length === 0) return questionMatch(normalized, 1)

  let best: { candidate: Candidate; score: number } | null = null
  for (const candidate of candidates) {
    let covered = 0
    let questionLeft = false
    let negationLeft = false
    tokens.forEach((token, i) => {
      const inPattern = candidate.consumed.has(i)
      if (inPattern || token.filler) covered += 1
      if (!inPattern && token.question) questionLeft = true
      if (!inPattern && token.negation) negationLeft = true
    })
    let score = covered / tokens.length
    if (questionLeft) score *= 0.55
    if (negationLeft) score *= 0.4
    if (hasQuestionMark && candidate.intent !== 'repeat') score *= 0.85
    if (tokens.length > 10) score *= 0.6
    score *= Math.pow(0.92, candidate.fuzz)
    candidate.covered = covered
    if (
      !best ||
      score > best.score + 1e-9 ||
      (Math.abs(score - best.score) < 1e-9 &&
        (candidate.groups > best.candidate.groups ||
          (candidate.groups === best.candidate.groups &&
            PRIORITY.indexOf(candidate.intent) < PRIORITY.indexOf(best.candidate.intent))))
    ) {
      best = { candidate, score }
    }
  }
  const chosen = best!
  const confidence = Math.round(Math.min(1, chosen.score) * 100) / 100
  if (confidence < AMBIGUOUS_THRESHOLD) return questionMatch(normalized, 1 - confidence)
  return { intent: chosen.candidate.intent, args: chosen.candidate.args, confidence, normalized, source: 'local' }
}

/** True when the matcher's answer should be acted on without asking the backend. */
export function isConfident(match: IntentMatch): boolean {
  return match.intent === 'question' ? match.confidence >= 1 - AMBIGUOUS_THRESHOLD : match.confidence >= ACT_THRESHOLD
}

/** Validates an intent/args pair from an untrusted source (backend JSON). */
export function sanitizeIntent(intent: unknown, args: unknown): { intent: VoiceIntent; args: IntentArgs } | null {
  if (typeof intent !== 'string' || !(VOICE_INTENTS as readonly string[]).includes(intent)) return null
  const out: IntentArgs = {}
  const raw = (args && typeof args === 'object' ? args : {}) as Record<string, unknown>
  const int = (value: unknown) => {
    const n = typeof value === 'string' ? Number(value) : value
    return typeof n === 'number' && Number.isInteger(n) && n > 0 && n < 1000 ? n : undefined
  }
  if (int(raw.chapter)) out.chapter = int(raw.chapter)
  if (int(raw.page)) out.page = int(raw.page)
  if (raw.chapterDelta === 1 || raw.chapterDelta === -1) out.chapterDelta = raw.chapterDelta
  if (raw.scope === 'page' || raw.scope === 'sentence') out.scope = raw.scope
  if (intent === 'goto_chapter' && !out.chapter && !out.chapterDelta) return null
  if (intent === 'goto_page' && !out.page) return null
  return { intent: intent as VoiceIntent, args: out }
}

/* ------------------------------------------------------------------------ */
/* Echo guard                                                                */
/* ------------------------------------------------------------------------ */

/**
 * True when a transcript is most likely our own voice coming back through the
 * speakers (it mostly repeats words we just spoke). Hands-free mode drops these.
 */
export function isLikelyEcho(transcript: string, recentlySpoken: string[]): boolean {
  const heard = normalizeUtterance(transcript)
    .split(' ')
    .filter((w) => w.length >= 3)
    .map(skeleton)
  if (heard.length < 2) return false
  const spoken = new Set(
    recentlySpoken.flatMap((text) => normalizeUtterance(text).split(' ').filter((w) => w.length >= 3).map(skeleton)),
  )
  if (spoken.size === 0) return false
  const hits = heard.filter((w) => spoken.has(w)).length
  return hits / heard.length >= 0.6
}

/** Whisper's well-known hallucinations on silence/noise; never commands or questions. */
export function isNoiseTranscript(transcript: string): boolean {
  const n = normalizeUtterance(transcript)
  if (!n) return true
  return /^(thank you|thanks|thank you for watching|thanks for watching|you|bye|bye bye|subscribe|please subscribe|music|applause|silence|hmm|um|uh|ah|oh)$/.test(n)
}
