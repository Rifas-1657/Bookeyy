export type FaqCategory = 'books' | 'narration' | 'content' | 'privacy' | 'accessibility' | 'future'

export const FAQ_CATEGORIES: [FaqCategory, string][] = [
  ['books', 'Videos to books'],
  ['narration', 'Narration'],
  ['content', 'Learning content'],
  ['privacy', 'Privacy'],
  ['accessibility', 'Accessibility'],
  ['future', 'Future capabilities'],
]

export const FAQ_ITEMS: { category: FaqCategory; q: string; a: string }[] = [
  {
    category: 'books',
    q: 'What does “turning a video into a book” actually mean?',
    a: 'The source is transcribed and reorganized into chapters and short pages, each focused on one idea. Diagrams, narration, and practice are added where they help. You get the substance of the original in a form you can skim, search, and revisit.',
  },
  {
    category: 'books',
    q: 'Does Bookey summarize away important detail?',
    a: 'The goal is restructuring, not shortening. Key reasoning and examples are kept, and every page links back to the moment in the source it came from, so you can always check the original.',
  },
  {
    category: 'books',
    q: 'How long should a source be?',
    a: 'Bookey is designed for long-form material, roughly 20 minutes and up: lectures, workshops, podcasts, and recorded conversations. Short clips work but gain less from being structured.',
  },
  {
    category: 'narration',
    q: 'Can I listen instead of reading?',
    a: 'Yes. Every page can be narrated, with each word highlighted as it is spoken. You can pause, change speed, and pick up where you left off.',
  },
  {
    category: 'narration',
    q: 'Is the narration the original speaker’s voice?',
    a: 'No. Narration uses a synthesized reading voice. Using an original speaker’s voice would require their explicit permission.',
  },
  {
    category: 'content',
    q: 'What kinds of learning content work best?',
    a: 'Explanatory material with a clear thread: technical tutorials, university lectures, professional training, and interviews with experts. Highly visual content such as live demonstrations translates less directly.',
  },
  {
    category: 'content',
    q: 'Which languages are supported for code practice?',
    a: 'Yes. Python examples run right on the page, and you can edit them. Support for more languages is on the roadmap.',
  },
  {
    category: 'privacy',
    q: 'What happens to the files I upload?',
    a: 'Files you upload to create a book are stored privately on our servers so Bookey can build your book. Only you can open that book.',
  },
  {
    category: 'privacy',
    q: 'Where are my notes and progress stored?',
    a: 'Notes, bookmarks and reading progress are saved to your account, so they follow you across devices.',
  },
  {
    category: 'privacy',
    q: 'Do you track what I read?',
    a: 'Reading activity is used only to adapt your book and show your progress. We do not sell it or share it for advertising.',
  },
  {
    category: 'accessibility',
    q: 'Can I use Bookey with a keyboard or screen reader?',
    a: 'Yes. Navigation, the reader, and interactive elements are keyboard accessible and labelled for screen readers. Narration includes a synced transcript.',
  },
  {
    category: 'accessibility',
    q: 'What if animations make me uncomfortable?',
    a: 'Bookey follows your system’s reduced-motion setting. Diagrams appear complete instead of drawing, and decorative motion is turned off.',
  },
  {
    category: 'future',
    q: 'When can I generate books from my own videos?',
    a: 'Yes. Open Create a book and add a video, PDF, slides, images or a link, and Bookey writes it into a narrated book.',
  },
  {
    category: 'future',
    q: 'Will there be accounts and synced progress?',
    a: 'Yes. Your account keeps your books, notes and progress on every device.',
  },
  {
    category: 'future',
    q: 'Can educators publish books for their learners?',
    a: 'A Creator plan for publishing and sharing books is planned. It is shown on the pricing page as an example tier and is not available yet.',
  },
]
