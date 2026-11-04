import type {
  AccessibilityPreferences,
  DailyMinutes,
  ExplanationStyle,
  Interest,
  LearnerLevel,
  LearningGoal,
  MovieGenre,
  Personalization,
  PreferredLanguage,
  SessionPace,
} from '@/lib/stores/personalization'

export type StepId = 'level' | 'goal' | 'interests' | 'genre' | 'project' | 'style' | 'language' | 'pace' | 'time' | 'accessibility'

/** Draft answers: required single choices start empty so the learner picks deliberately. */
export interface SurveyDraft {
  level: LearnerLevel | null
  interests: Interest[]
  movieGenre: MovieGenre | null
  style: ExplanationStyle | null
  language: PreferredLanguage
  pace: SessionPace | null
  goal: LearningGoal | null
  dailyMinutes: DailyMinutes | null
  project: string | null
  accessibility: AccessibilityPreferences
}

export interface Choice<T extends string> {
  value: T
  label: string
  description?: string
}

export const levelChoices: Choice<LearnerLevel>[] = [
  { value: 'new', label: 'New to this', description: 'Start from the very first idea, no jargon assumed.' },
  { value: 'basics', label: 'Know the basics', description: 'Skip the warm-up and go a little deeper.' },
  { value: 'projects', label: 'Built small projects', description: 'Focus on patterns, trade-offs, and real code.' },
]

export const goalChoices: Choice<LearningGoal>[] = [
  { value: 'job', label: 'Get a job', description: 'Build the skills and projects interviews ask for.' },
  { value: 'college', label: 'Clear college exams', description: 'Solid fundamentals, explained clearly.' },
  { value: 'automation', label: 'Automate boring work', description: 'Adds a chapter on scripts that save you hours.' },
  { value: 'data', label: 'Work with data', description: 'Adds a chapter on cleaning and analysing data.' },
  { value: 'web', label: 'Build for the web', description: 'Adds a chapter on HTTP, APIs and JSON.' },
  { value: 'ai', label: 'Get into AI', description: 'Adds a chapter on how machines learn.' },
  { value: 'hobby', label: 'Just for fun', description: 'Enjoy the journey, no pressure.' },
]

export const timeChoices: Choice<DailyMinutes>[] = [
  { value: '10', label: '10 minutes', description: 'A page or two a day.' },
  { value: '20', label: '20 minutes', description: 'A steady daily habit.' },
  { value: '30', label: '30 minutes', description: 'A lesson and its practice.' },
  { value: '60', label: 'An hour or more', description: 'Serious daily progress.' },
]

export const interestChoices: Choice<Interest>[] = [
  { value: 'movies', label: 'Movies' },
  { value: 'cricket', label: 'Cricket' },
  { value: 'games', label: 'Games' },
  { value: 'music', label: 'Music' },
  { value: 'cooking', label: 'Cooking' },
  { value: 'cars', label: 'Cars' },
  { value: 'anime', label: 'Anime' },
  { value: 'business', label: 'Business' },
  { value: 'science', label: 'Science' },
]

export const genreChoices: Choice<MovieGenre>[] = [
  { value: 'action', label: 'Action' },
  { value: 'comedy', label: 'Comedy' },
  { value: 'thriller', label: 'Thriller' },
  { value: 'romance', label: 'Romance' },
  { value: 'sci-fi', label: 'Sci-fi' },
  { value: 'animation', label: 'Animation' },
]

export const styleChoices: Choice<ExplanationStyle>[] = [
  { value: 'example', label: 'Example first', description: 'A relatable story, then the concept behind it.' },
  { value: 'diagram', label: 'Diagram first', description: 'See the shape of an idea before the details.' },
  { value: 'code', label: 'Code first', description: 'Show the code, then explain line by line.' },
]

export const languageChoices: Choice<PreferredLanguage>[] = [
  { value: 'tanglish', label: 'Tanglish', description: 'Tamil and English mixed, the way friends explain things.' },
  { value: 'english', label: 'English', description: 'Plain, friendly English throughout.' },
]

export const paceChoices: Choice<SessionPace>[] = [
  { value: 'calm', label: 'Calm and detailed', description: 'Smaller steps, more examples, extra recap.' },
  { value: 'balanced', label: 'Balanced', description: 'A steady mix of explanation and practice.' },
  { value: 'fast', label: 'Fast revision', description: 'Key points and quick checks only.' },
]

export const accessibilityChoices: { key: keyof AccessibilityPreferences; label: string; description: string }[] = [
  { key: 'reducedMotion', label: 'Reduced motion', description: 'Turn off page flips and animated transitions.' },
  { key: 'largerText', label: 'Larger text', description: 'Bump up reading size across the book.' },
  { key: 'captions', label: 'Captions always on', description: 'Show captions for every narrated explanation.' },
]

export interface StepMeta {
  id: StepId
  eyebrow: string
  question: string
  hint: string
  optional?: boolean
}

export const stepMeta: Record<StepId, StepMeta> = {
  level: {
    id: 'level',
    eyebrow: 'Your starting point',
    question: 'Where are you with Python right now?',
    hint: 'Pick the one that sounds most like you.',
  },
  goal: {
    id: 'goal',
    eyebrow: 'Your why',
    question: 'What do you want Python to do for you?',
    hint: 'Your book adds the chapters that get you there.',
  },
  project: {
    id: 'project',
    eyebrow: 'Your dream project',
    question: 'What would you love to build one day?',
    hint: 'Optional. A line is enough. Your capstone and examples will lean towards it.',
    optional: true,
  },
  time: {
    id: 'time',
    eyebrow: 'Your day',
    question: 'How much time can you give each day?',
    hint: 'We use this to plan your reminders and reviews.',
  },
  interests: {
    id: 'interests',
    eyebrow: 'Things you love',
    question: 'What should your examples be about?',
    hint: 'Choose as many as you like — at least one.',
  },
  genre: {
    id: 'genre',
    eyebrow: 'Movie night',
    question: 'Any favourite kind of film?',
    hint: 'Optional. Skip it if you enjoy everything.',
    optional: true,
  },
  style: {
    id: 'style',
    eyebrow: 'How it clicks',
    question: 'How do you like new ideas explained?',
    hint: 'You can still see all three formats in every chapter.',
  },
  language: {
    id: 'language',
    eyebrow: 'Reading language',
    question: 'Which language should the book speak?',
    hint: 'Tanglish is selected by default — change it anytime.',
  },
  pace: {
    id: 'pace',
    eyebrow: 'Session rhythm',
    question: 'How fast should each session move?',
    hint: 'This sets how much detail sits on each page.',
  },
  accessibility: {
    id: 'accessibility',
    eyebrow: 'Comfort settings',
    question: 'Anything that makes reading easier?',
    hint: 'All optional. Turn on whatever helps.',
    optional: true,
  },
}

export function getVisibleSteps(draft: SurveyDraft): StepId[] {
  const steps: StepId[] = ['level', 'goal', 'interests']
  if (draft.interests.includes('movies')) steps.push('genre')
  steps.push('project', 'style', 'language', 'pace', 'time', 'accessibility')
  return steps
}

export function validateStep(step: StepId, draft: SurveyDraft): string | null {
  switch (step) {
    case 'level':
      return draft.level ? null : 'Choose your current level to continue.'
    case 'goal':
      return draft.goal ? null : 'Choose what you want from Python to continue.'
    case 'time':
      return draft.dailyMinutes ? null : 'Choose your daily time to continue.'
    case 'interests':
      return draft.interests.length > 0 ? null : 'Pick at least one interest so examples feel familiar.'
    case 'style':
      return draft.style ? null : 'Choose an explanation style to continue.'
    case 'language':
      return draft.language ? null : 'Choose a language to continue.'
    case 'pace':
      return draft.pace ? null : 'Choose a session pace to continue.'
    default:
      return null
  }
}

export function draftFromPersonalization(saved: Personalization, completed: boolean): SurveyDraft {
  if (completed) return { ...saved }
  return {
    level: null,
    interests: [],
    movieGenre: null,
    style: null,
    language: saved.language,
    pace: null,
    goal: null,
    dailyMinutes: null,
    project: null,
    accessibility: { ...saved.accessibility },
  }
}

export function finalizeDraft(draft: SurveyDraft): Personalization | null {
  if (!draft.level || !draft.style || !draft.pace || !draft.goal || !draft.dailyMinutes || draft.interests.length === 0) return null
  return {
    level: draft.level,
    interests: draft.interests,
    movieGenre: draft.interests.includes('movies') ? draft.movieGenre : null,
    style: draft.style,
    language: draft.language,
    pace: draft.pace,
    goal: draft.goal,
    dailyMinutes: draft.dailyMinutes,
    project: draft.project?.trim() || null,
    accessibility: draft.accessibility,
  }
}

export function labelFor<T extends string>(choices: Choice<T>[], value: T | null) {
  return choices.find((choice) => choice.value === value)?.label ?? null
}
