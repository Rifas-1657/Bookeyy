import type { BookCover } from '@/types/learning'

export const LANDING_NAV = [
  { href: '/features', label: 'Features' },
  { href: '/how-it-works', label: 'How it works' },
  { href: '/pricing', label: 'Pricing' },
  { href: '/about', label: 'About' },
  { href: '/faq', label: 'FAQ' },
] as const

export const ROUTES = {
  login: '/login',
  signup: '/signup',
  courses: '/courses',
  demoCourse: '/book/python',
  features: '/features',
  howItWorks: '/how-it-works',
  pricing: '/pricing',
  about: '/about',
  faq: '/faq',
  privacy: '/privacy',
  terms: '/terms',
} as const

export interface SpotlightCourse {
  slug: string
  title: string
  author: string
  blurb: string
  difficulty: 'Beginner' | 'Intermediate' | 'Advanced'
  chapters: number
  cover: BookCover
}

export const SPOTLIGHT_COURSES: SpotlightCourse[] = [
  {
    slug: 'python-from-zero',
    title: 'Python: The Complete Book',
    author: 'Bookey',
    blurb: 'Variables, loops, and functions — with code you run on the page.',
    difficulty: 'Beginner',
    chapters: 12,
    cover: { base: '#E11D48', spine: '#9F1239', accent: '#FFE4E6', text: '#FFFFFF' },
  },
  {
    slug: 'java-foundations',
    title: 'Java Foundations',
    author: 'Bookey',
    blurb: 'Classes and objects explained with diagrams that draw themselves.',
    difficulty: 'Intermediate',
    chapters: 14,
    cover: { base: '#3B0A14', spine: '#12040A', accent: '#FB7185', text: '#FFE4E6' },
  },
  {
    slug: 'machine-learning-intuition',
    title: 'Machine Learning Intuition',
    author: 'Bookey',
    blurb: 'Gradients and models, narrated step by step by a voice tutor.',
    difficulty: 'Advanced',
    chapters: 10,
    cover: { base: '#F59E0B', spine: '#C2410C', accent: '#FFFBEB', text: '#292013' },
  },
  {
    slug: 'web-development-essentials',
    title: 'Web Development Essentials',
    author: 'Bookey',
    blurb: 'HTML, CSS, and JavaScript with live previews for every chapter.',
    difficulty: 'Beginner',
    chapters: 16,
    cover: { base: '#FFE4E6', spine: '#FB7185', accent: '#E11D48', text: '#3B0A14' },
  },
]

export const FAQS = [
  {
    q: 'What can I turn into a Bookey book?',
    a: 'YouTube videos and lectures, PDFs and documents, research papers (arXiv), GitHub projects, Reddit threads and web articles. Bookey restructures them into chapters and short pages, then adds narration, live diagrams, practicals and tasks where they help.',
  },
  {
    q: 'Which languages does the tutor speak?',
    a: 'Tanglish (Tamil in English letters, the way friends actually talk) and English. You can switch per course, and ask questions in either.',
  },
  {
    q: 'Are answers from the voice tutor made up?',
    a: 'The tutor answers from your sources first and points back to where an idea came from. When something goes beyond the material, she says so instead of presenting it as part of the source.',
  },
  {
    q: 'Can I learn offline?',
    a: 'Yes. A finished book keeps its pages, your screenshots, notes and the end-of-chapter recap, so you can revisit it without a connection.',
  },
  {
    q: 'Do I need to install anything to run the code?',
    a: 'No. Practicals open right on the page, so you can edit an example and run it without setting up a local environment.',
  },
  {
    q: 'Can my team build its own courses?',
    a: 'Admins use the course builder: add papers, repos, threads, lectures and a syllabus, review the generated outline, edit lessons and projects, then publish.',
  },
]

// ---- Luxury landing data --------------------------------------------------

export type LuxCoverTone = 'oxblood' | 'ink' | 'gold' | 'linen'

export interface LuxCourse {
  title: string
  italic: string
  level: 'Beginner' | 'Intermediate' | 'Advanced'
  languages: ('Tanglish' | 'English')[]
  sources: ('youtube' | 'github' | 'reddit' | 'arxiv' | 'pdf_url' | 'web')[]
  chapters: number
  lessons: number
  project: string
  tone: LuxCoverTone
}

/** Illustrative course editions for the marketing page (demo content). */
export const LUX_COURSES: LuxCourse[] = [
  {
    title: 'Transformers,',
    italic: 'explained',
    level: 'Advanced',
    languages: ['English', 'Tanglish'],
    sources: ['arxiv', 'youtube', 'github'],
    chapters: 8,
    lessons: 31,
    project: 'Train a tiny poet on Thirukkural',
    tone: 'oxblood',
  },
  {
    title: 'Python',
    italic: 'from zero',
    level: 'Beginner',
    languages: ['Tanglish', 'English'],
    sources: ['youtube', 'pdf_url'],
    chapters: 12,
    lessons: 44,
    project: 'Build a tea-shop billing app',
    tone: 'ink',
  },
  {
    title: 'System design',
    italic: 'in practice',
    level: 'Intermediate',
    languages: ['English'],
    sources: ['github', 'reddit', 'web'],
    chapters: 10,
    lessons: 36,
    project: 'Design a URL shortener that scales',
    tone: 'gold',
  },
  {
    title: 'Machine learning',
    italic: 'intuition',
    level: 'Intermediate',
    languages: ['Tanglish', 'English'],
    sources: ['youtube', 'arxiv', 'reddit'],
    chapters: 9,
    lessons: 28,
    project: 'Predict Chennai rainfall',
    tone: 'linen',
  },
]

export interface LuxTestimonial {
  quote: string
  name: string
  role: string
}

/** Illustrative quotes from pilot personas — replace with real, consented quotes before launch. */
export const LUX_TESTIMONIALS: LuxTestimonial[] = [
  {
    quote: 'She explains like my favourite senior did — in Tanglish, slowly, and never makes me feel stupid for asking twice.',
    name: 'Divya R.',
    role: 'Second-year CSE, Coimbatore',
  },
  {
    quote: 'I gave it a three-hour lecture and a repo. Ten minutes later I had a book with diagrams and a project at the end.',
    name: 'Arjun M.',
    role: 'Self-taught developer',
  },
  {
    quote: 'Building a course from papers used to take me a month. Now I review an outline, fix two lessons, and publish.',
    name: 'Prof. K. Lakshmi',
    role: 'Course author',
  },
]
