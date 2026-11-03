import { cn } from '@/lib/utils'

/** Source kinds Bookey can turn into a book (mirrors the course-builder API). */
export type SourceKind = 'youtube' | 'github' | 'reddit' | 'arxiv' | 'pdf_url' | 'web' | 'upload' | 'syllabus'

export const SOURCE_LABELS: Record<SourceKind, string> = {
  youtube: 'YouTube video',
  github: 'GitHub project',
  reddit: 'Reddit thread',
  arxiv: 'Research paper',
  pdf_url: 'PDF link',
  web: 'Web article',
  upload: 'Uploaded document',
  syllabus: 'Course syllabus',
}

type GlyphProps = { className?: string; title?: string }

function Svg({ className, title, children }: GlyphProps & { children: React.ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={cn('size-5 shrink-0', className)}
      role={title ? 'img' : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
    >
      {children}
    </svg>
  )
}

/**
 * Simplified, monochrome source glyphs (not official logos) that inherit
 * `currentColor`, so they theme with the surrounding text.
 */
export function SourceGlyph({ kind, className, title }: GlyphProps & { kind: SourceKind }) {
  switch (kind) {
    case 'youtube':
      return (
        <Svg className={className} title={title}>
          <rect x="2.5" y="5.5" width="19" height="13" rx="4" />
          <path d="M10 9.2v5.6l4.8-2.8z" fill="currentColor" stroke="none" />
        </Svg>
      )
    case 'github':
      return (
        <Svg className={className} title={title}>
          <circle cx="6.5" cy="5.5" r="2" />
          <circle cx="6.5" cy="18.5" r="2" />
          <circle cx="17.5" cy="8.5" r="2" />
          <path d="M6.5 7.5v9M17.5 10.5c0 4-5 3.5-9.6 6.4" />
        </Svg>
      )
    case 'reddit':
      return (
        <Svg className={className} title={title}>
          <ellipse cx="12" cy="14" rx="8" ry="5.5" />
          <path d="M12 8.5 13.2 3.5l4 1" />
          <circle cx="18.6" cy="4.8" r="1.3" />
          <circle cx="9" cy="13.4" r="0.9" fill="currentColor" />
          <circle cx="15" cy="13.4" r="0.9" fill="currentColor" />
          <path d="M9.3 16.4c1.6 1 3.8 1 5.4 0" />
        </Svg>
      )
    case 'arxiv':
      return (
        <Svg className={className} title={title}>
          <path d="M6 2.8h8l4 4v14.4H6z" />
          <path d="M14 2.8v4h4" />
          <path d="M9 12.5l5 5M14 12.5l-5 5" />
          <path d="M9 9.5h3" />
        </Svg>
      )
    case 'pdf_url':
    case 'upload':
      return (
        <Svg className={className} title={title}>
          <path d="M6 2.8h8l4 4v14.4H6z" />
          <path d="M14 2.8v4h4" />
          <path d="M8.6 17v-4h1.3a1.2 1.2 0 0 1 0 2.4H8.6M12.4 17v-4h1a2 2 0 0 1 0 4zM16.2 17v-4h1.6M16.2 15h1.3" strokeWidth={1.2} />
        </Svg>
      )
    case 'syllabus':
      return (
        <Svg className={className} title={title}>
          <path d="M4 5.5c2.5-1.4 5.5-1.4 8 0v14c-2.5-1.4-5.5-1.4-8 0z" />
          <path d="M12 5.5c2.5-1.4 5.5-1.4 8 0v14c-2.5-1.4-5.5-1.4-8 0z" />
          <path d="M6.5 9h3M6.5 12h3M14.5 9h3M14.5 12h3" />
        </Svg>
      )
    case 'web':
    default:
      return (
        <Svg className={className} title={title}>
          <circle cx="12" cy="12" r="9" />
          <path d="M3 12h18M12 3c2.6 2.8 2.6 15.2 0 18M12 3c-2.6 2.8-2.6 15.2 0 18" />
        </Svg>
      )
  }
}
