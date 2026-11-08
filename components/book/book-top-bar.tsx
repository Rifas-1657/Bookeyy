'use client'

import {
  ArrowLeft,
  Award,
  Bookmark,
  BookmarkCheck,
  BookOpen,
  CloudDownload,
  Loader2,
  Maximize,
  MessagesSquare,
  Minimize,
  MoreHorizontal,
  PanelLeft,
  Pause,
  ShieldCheck,
  Sparkles,
  Square,
  StickyNote,
  Type,
  Volume2,
  WifiOff,
} from 'lucide-react'
import Link from 'next/link'
import { BookeyBadge } from '@/components/ui-kit/bookey-badge'
import { Hint } from '@/components/ui-kit/hint'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { OfflineDownload } from '@/components/book/offline-control'
import { cn } from '@/lib/utils'
import { NARRATION_RATES, type NarrationRate, type ReaderViewMode } from '@/types/book'

const glassButton =
  'inline-flex size-9 shrink-0 items-center justify-center rounded-full border border-white/10 bg-white/5 text-foreground backdrop-blur-md transition-colors hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-40 aria-pressed:border-primary/50 aria-pressed:bg-primary/20 aria-pressed:text-primary'

interface BookTopBarProps {
  courseId: string
  courseTitle: string
  chapterLabel: string
  pageNumber: number | null
  totalPages: number
  online: boolean
  railOpen: boolean
  onToggleRail: () => void
  bookmarked: boolean
  onToggleBookmark: () => void
  notesOpen: boolean
  onToggleNotes: () => void
  chatOpen: boolean
  onToggleChat: () => void
  viewMode: ReaderViewMode
  onViewModeChange: (mode: ReaderViewMode) => void
  fontScale: number
  onFontScaleChange: (scale: number) => void
  fullscreen: { active: boolean; supported: boolean; toggle: () => void }
  disabled?: boolean
  audioStatus?: 'idle' | 'speaking' | 'paused' | 'loading' | 'error'
  onToggleListen?: () => void
  onStopListen?: () => void
  playbackRate?: NarrationRate
  onRateChange?: (rate: NarrationRate) => void
  /** Lesson recap for the current chapter (shown in More). */
  recap?: { open: boolean; toggle: () => void }
  /** Offline download state (shown in More). */
  offline?: OfflineDownload
}

const viewModes: { id: ReaderViewMode; label: string; icon: typeof BookOpen }[] = [
  { id: 'full', label: 'Full book', icon: BookOpen },
  { id: 'guided', label: 'Guided', icon: Sparkles },
]

export function BookTopBar(props: BookTopBarProps) {
  const { pageNumber, totalPages, disabled } = props
  const progress = pageNumber ? (pageNumber / totalPages) * 100 : 0

  return (
    <header className="relative z-20 shrink-0 border-b border-white/10 bg-black/20 backdrop-blur-xl">
      <div className="flex h-16 items-center gap-2 px-3 sm:gap-3 sm:px-5">
        <Hint label="Back to course" side="bottom">
          <Link href={`/courses/${props.courseId}`} className={glassButton} aria-label="Back to course">
            <ArrowLeft className="size-4" aria-hidden="true" />
          </Link>
        </Hint>

        <Hint label={props.railOpen ? 'Hide chapters' : 'Show chapters'} side="bottom">
          <button
            type="button"
            className={glassButton}
            aria-pressed={props.railOpen}
            aria-label="Chapters"
            onClick={props.onToggleRail}
            disabled={disabled}
          >
            <PanelLeft className="size-4" aria-hidden="true" />
          </button>
        </Hint>

        <div className="min-w-0 flex-1 px-1">
          <p className="truncate text-sm font-semibold">{props.courseTitle}</p>
          <p className="truncate text-xs text-muted-foreground">{props.chapterLabel}</p>
        </div>

        <div className="hidden items-center gap-2 md:flex">
          {pageNumber && (
            <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 font-mono text-xs tabular-nums">
              Page {pageNumber} of {totalPages}
            </span>
          )}
          {!props.online && (
            <BookeyBadge tone="warning" icon={<WifiOff aria-hidden="true" />}>
              Offline
            </BookeyBadge>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {props.onToggleListen && (
            <div className="flex items-center gap-1" role="group" aria-label="Audio narration">
              {props.audioStatus && props.audioStatus !== 'idle' && (
                <>
                  {props.onStopListen && (
                    <Hint label="Stop narration" side="bottom">
                      <button
                        type="button"
                        className={glassButton}
                        aria-label="Stop narration"
                        onClick={props.onStopListen}
                      >
                        <Square className="size-3.5" aria-hidden="true" />
                      </button>
                    </Hint>
                  )}
                  {props.playbackRate && props.onRateChange && (
                    <Hint label={`Speed ${props.playbackRate}×`} side="bottom">
                      <button
                        type="button"
                        onClick={() => {
                          const next = NARRATION_RATES[(NARRATION_RATES.indexOf(props.playbackRate ?? 1) + 1) % NARRATION_RATES.length]
                          props.onRateChange?.(next)
                        }}
                        aria-label={`Narration speed ${props.playbackRate} times`}
                        className="rounded-full border border-white/10 bg-white/5 px-2 py-1 font-mono text-xs font-semibold tabular-nums hover:bg-white/10"
                      >
                        {props.playbackRate}×
                      </button>
                    </Hint>
                  )}
                </>
              )}
              <Hint
                label={
                  props.audioStatus === 'loading'
                    ? 'Synthesizing speech…'
                    : props.audioStatus === 'speaking'
                      ? 'Pause narration'
                      : props.audioStatus === 'paused'
                        ? 'Resume narration'
                        : props.audioStatus === 'error'
                          ? 'Audio service unavailable'
                          : 'Listen to this page'
                }
                side="bottom"
              >
                <button
                  type="button"
                  onClick={props.onToggleListen}
                  disabled={disabled || props.audioStatus === 'loading'}
                  aria-label={props.audioStatus === 'speaking' ? 'Pause narration' : 'Listen to this page'}
                  className={cn(
                    'inline-flex h-9 items-center gap-1.5 rounded-full border border-book-orange/40 bg-book-orange/15 px-3 text-xs font-semibold text-foreground backdrop-blur-md transition-colors hover:bg-book-orange/25 focus-visible:ring-2 focus-visible:ring-book-orange focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-40',
                    props.audioStatus === 'speaking' && 'border-book-orange bg-book-orange/30 text-book-marker',
                  )}
                >
                  {props.audioStatus === 'loading' ? (
                    <Loader2 className="size-3.5 animate-spin" aria-hidden="true" />
                  ) : props.audioStatus === 'speaking' ? (
                    <Pause className="size-3.5 fill-current" aria-hidden="true" />
                  ) : (
                    <Volume2 className="size-3.5" aria-hidden="true" />
                  )}
                  <span>
                    {props.audioStatus === 'loading'
                      ? 'Loading…'
                      : props.audioStatus === 'speaking'
                        ? 'Pause'
                        : props.audioStatus === 'paused'
                          ? 'Resume'
                          : props.audioStatus === 'error'
                            ? 'Unavailable'
                            : 'Listen'}
                  </span>
                </button>
              </Hint>
            </div>
          )}

          <Hint label={props.chatOpen ? 'Close tutor chat' : 'Ask the tutor about this page'} side="bottom">
            <button
              type="button"
              className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full border border-white/10 bg-white/5 px-3 text-xs font-semibold backdrop-blur-md transition-colors hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-40 aria-pressed:border-primary/50 aria-pressed:bg-primary/20 aria-pressed:text-primary"
              aria-pressed={props.chatOpen}
              aria-label="Ask"
              onClick={props.onToggleChat}
              disabled={disabled}
            >
              <MessagesSquare className="size-3.5" aria-hidden="true" />
              <span className="hidden sm:inline">Ask</span>
            </button>
          </Hint>

          <DropdownMenu>
            <DropdownMenuTrigger className={cn(glassButton, 'relative')} aria-label="More reader options">
                <MoreHorizontal className="size-4" aria-hidden="true" />
                {props.offline?.progress && (
                  <span aria-hidden="true" className="absolute -top-0.5 -right-0.5 size-2.5 rounded-full bg-primary motion-safe:animate-pulse" />
                )}
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="theme-night w-60">
              <DropdownMenuGroup>
                <DropdownMenuItem onClick={props.onToggleBookmark} disabled={disabled}>
                  {props.bookmarked ? <BookmarkCheck aria-hidden="true" /> : <Bookmark aria-hidden="true" />}
                  {props.bookmarked ? 'Remove bookmark' : 'Bookmark this page'}
                </DropdownMenuItem>
                <DropdownMenuItem onClick={props.onToggleNotes} disabled={disabled}>
                  <StickyNote aria-hidden="true" />
                  {props.notesOpen ? 'Close notes' : 'Notes and bookmarks'}
                </DropdownMenuItem>
                {props.recap && (
                  <DropdownMenuItem onClick={props.recap.toggle} disabled={disabled}>
                    <Award aria-hidden="true" />
                    {props.recap.open ? 'Close lesson recap' : 'Lesson recap'}
                  </DropdownMenuItem>
                )}
                {props.offline && (
                  <DropdownMenuItem
                    onClick={() => void props.offline?.download()}
                    disabled={Boolean(props.offline.progress) || Boolean(props.offline.saved?.complete) || !props.online}
                  >
                    {props.offline.progress ? (
                      <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                    ) : props.offline.saved?.complete ? (
                      <ShieldCheck aria-hidden="true" />
                    ) : (
                      <CloudDownload aria-hidden="true" />
                    )}
                    {props.offline.progress
                      ? `Saving ${Math.round((props.offline.progress.done / props.offline.progress.total) * 100)}%`
                      : props.offline.saved?.complete
                        ? 'Available offline'
                        : 'Download for offline'}
                  </DropdownMenuItem>
                )}
                {props.fullscreen.supported && (
                  <DropdownMenuItem onClick={props.fullscreen.toggle}>
                    {props.fullscreen.active ? <Minimize aria-hidden="true" /> : <Maximize aria-hidden="true" />}
                    {props.fullscreen.active ? 'Exit fullscreen' : 'Fullscreen'}
                  </DropdownMenuItem>
                )}
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuLabel>Reading mode</DropdownMenuLabel>
                {viewModes.map(({ id, label, icon: Icon }) => (
                  <DropdownMenuItem key={id} onClick={() => props.onViewModeChange(id)}>
                    <Icon aria-hidden="true" />
                    {label}
                    {props.viewMode === id && <span className="ml-auto text-xs text-primary">On</span>}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuGroup>
              <DropdownMenuSeparator />
              <DropdownMenuGroup>
                <DropdownMenuLabel>
                  Text size · <span className="font-mono">{Math.round(props.fontScale * 100)}%</span>
                </DropdownMenuLabel>
                <DropdownMenuItem closeOnClick={false} onClick={() => props.onFontScaleChange(props.fontScale + 0.05)}>
                  <Type aria-hidden="true" />
                  Larger text
                </DropdownMenuItem>
                <DropdownMenuItem closeOnClick={false} onClick={() => props.onFontScaleChange(props.fontScale - 0.05)}>
                  <Type aria-hidden="true" className="scale-75" />
                  Smaller text
                </DropdownMenuItem>
                <DropdownMenuItem closeOnClick={false} onClick={() => props.onFontScaleChange(1)}>
                  Reset size
                </DropdownMenuItem>
              </DropdownMenuGroup>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      <div
        role="progressbar"
        aria-label="Book progress"
        aria-valuemin={0}
        aria-valuemax={totalPages}
        aria-valuenow={pageNumber ?? 0}
        className="h-0.5 w-full bg-white/5"
      >
        <div className="h-full bg-primary transition-[width] duration-500 ease-out" style={{ width: `${progress}%` }} />
      </div>
    </header>
  )
}
