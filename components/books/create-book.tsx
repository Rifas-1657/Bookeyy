'use client'

import Link from 'next/link'
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import {
  AlertCircle,
  BookOpen,
  Check,
  CircleDashed,
  FileText,
  FileUp,
  Film,
  ImageIcon,
  Link2,
  ListChecks,
  Loader2,
  Music,
  RotateCcw,
  Sparkles,
  X,
} from 'lucide-react'
import { BookeyBadge } from '@/components/ui-kit/bookey-badge'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { SourceGlyph } from '@/components/ui-kit/source-glyphs'
import {
  ALL_ACCEPTED,
  DEFAULT_LIMITS,
  LINK_LABELS,
  detectLink,
  isFinished,
  resolveBooksClient,
  uploadKind,
  type BookJob,
  type BookLanguage,
  type BookLevel,
  type BookLimits,
  type BookQuota,
  type BookStep,
  type MyBook,
  type UploadKind,
} from '@/lib/api/books'
import { cn } from '@/lib/utils'

type Mode = 'file' | 'link' | 'prompt' | 'syllabus'

const MODES: Array<{ id: Mode; label: string; icon: React.ReactNode }> = [
  { id: 'file', label: 'Upload files', icon: <FileUp /> },
  { id: 'link', label: 'Paste a link', icon: <Link2 /> },
  { id: 'prompt', label: 'Describe a topic', icon: <Sparkles /> },
  { id: 'syllabus', label: 'Paste a syllabus', icon: <ListChecks /> },
]

const KIND_META: Record<UploadKind, { label: string; icon: React.ReactNode }> = {
  document: { label: 'Document', icon: <FileText /> },
  video: { label: 'Video', icon: <Film /> },
  audio: { label: 'Audio', icon: <Music /> },
  image: { label: 'Image', icon: <ImageIcon /> },
}

const LEVELS: Array<{ id: BookLevel; label: string }> = [
  { id: 'beginner', label: 'Beginner' },
  { id: 'intermediate', label: 'Intermediate' },
  { id: 'advanced', label: 'Advanced' },
]

const LANGUAGES: Array<{ id: BookLanguage; label: string; hint: string }> = [
  { id: 'tanglish', label: 'Tanglish', hint: 'Tamil + English, in English letters' },
  { id: 'english', label: 'English', hint: 'Plain English narration' },
]

const PROMPT_EXAMPLES = [
  'Teach me SQL joins for beginners',
  'Photosynthesis for class 10, with simple diagrams',
  'Personal finance basics: budgeting and saving',
]

function formatBytes(n: number) {
  return n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / (1024 * 1024)).toFixed(1)} MB`
}

function extensionOf(name: string) {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i).toLowerCase() : ''
}

/* ---------------------------------------------------------------- controls */

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: T
  options: Array<{ id: T; label: string; hint?: string; icon?: React.ReactNode }>
  onChange: (v: T) => void
}) {
  return (
    <fieldset className="min-w-0">
      <legend className="mb-2 text-sm font-medium">{label}</legend>
      <div className="flex flex-wrap gap-2">
        {options.map((o) => {
          const active = o.id === value
          return (
            <label
              key={o.id}
              title={o.hint}
              className={cn(
                'inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-full border px-4 text-sm font-medium transition-colors [&_svg]:size-4',
                'has-[:focus-visible]:ring-4 has-[:focus-visible]:ring-ring/30',
                active
                  ? 'border-primary bg-primary text-primary-foreground'
                  : 'border-border bg-card text-foreground hover:border-primary/40 hover:bg-muted',
              )}
            >
              <input
                type="radio"
                className="sr-only"
                name={label}
                value={o.id}
                checked={active}
                onChange={() => onChange(o.id)}
              />
              {o.icon}
              {o.label}
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}

function DropZone({
  files,
  onAdd,
  onRemove,
  error,
  limits,
}: {
  files: File[]
  onAdd: (f: File[]) => void
  onRemove: (index: number) => void
  error: string | null
  limits: BookLimits
}) {
  const inputId = useId()
  const [over, setOver] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const images = files.length > 0 && files.every((f) => uploadKind(f.name) === 'image')
  const canAddMore = files.length === 0 || (images && files.length < limits.maxImages)

  return (
    <div className="flex flex-col gap-3">
      {files.length > 0 && (
        <ul className="flex flex-col gap-2" aria-label="Chosen files">
          {files.map((file, i) => {
            const kind = uploadKind(file.name) ?? 'document'
            return (
              <li key={`${file.name}-${i}`} className="flex items-center gap-3 rounded-2xl border border-border bg-card p-3">
                <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-secondary text-secondary-foreground [&_svg]:size-5" aria-hidden="true">
                  {KIND_META[kind].icon}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{file.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {KIND_META[kind].label} · {extensionOf(file.name).slice(1).toUpperCase()} · {formatBytes(file.size)}
                  </p>
                </div>
                <BookeyButton variant="ghost" size="icon" aria-label={`Remove ${file.name}`} onClick={() => onRemove(i)}>
                  <X />
                </BookeyButton>
              </li>
            )
          })}
        </ul>
      )}

      {canAddMore && (
        <label
          htmlFor={inputId}
          onDragOver={(e) => {
            e.preventDefault()
            setOver(true)
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault()
            setOver(false)
            const dropped = Array.from(e.dataTransfer.files ?? [])
            if (dropped.length) onAdd(dropped)
          }}
          className={cn(
            'flex cursor-pointer flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed px-4 text-center transition-colors sm:px-6',
            files.length ? 'min-h-24 py-4' : 'min-h-44 py-8',
            'has-[:focus-visible]:ring-4 has-[:focus-visible]:ring-ring/30',
            over ? 'border-primary bg-secondary/60' : 'border-input bg-card hover:border-primary/50 hover:bg-muted/60',
            error && 'border-destructive',
          )}
        >
          <span className="grid size-12 place-items-center rounded-2xl bg-secondary text-secondary-foreground" aria-hidden="true">
            {images ? <ImageIcon className="size-5" /> : <FileUp className="size-5" />}
          </span>
          <span className="text-sm font-medium">
            {images ? 'Add more photos' : 'Drop files here'}, or <span className="text-primary underline underline-offset-4">browse</span>
          </span>
          {files.length === 0 && (
            <span className="flex flex-col gap-1 text-xs text-muted-foreground">
              <span>Documents: PDF, PPTX, DOCX, TXT, MD · up to {limits.maxUploadMb} MB</span>
              <span>
                Video / audio: MP4, MOV, WEBM, MKV, M4A, MP3, WAV · one file, up to {limits.maxMediaMb} MB /{' '}
                {limits.maxMediaMinutes} min
              </span>
              <span>
                Photos of notes, pages or a syllabus: JPG, PNG, WEBP{limits.images.heic ? ', HEIC' : ''} · up to {limits.maxImages}{' '}
                images, {limits.maxImageMb} MB each
              </span>
            </span>
          )}
          <input
            ref={inputRef}
            id={inputId}
            type="file"
            multiple
            className="sr-only"
            accept={ALL_ACCEPTED.join(',')}
            onChange={(e) => {
              const picked = Array.from(e.target.files ?? [])
              if (picked.length) onAdd(picked)
              if (inputRef.current) inputRef.current.value = ''
            }}
          />
        </label>
      )}
      {error && (
        <p className="flex items-center gap-1.5 text-sm text-destructive" role="alert">
          <AlertCircle className="size-4 shrink-0" /> {error}
        </p>
      )}
    </div>
  )
}

/** Combine new picks with the current selection: one document, one video/audio, or a set of images. */
function mergeFiles(current: File[], picked: File[], limits: BookLimits): { files: File[]; error: string | null } {
  const unknown = picked.find((f) => uploadKind(f.name) === null)
  if (unknown) {
    return { files: current, error: `“${unknown.name}” isn’t supported. Use a document, a video/audio file, or photos.` }
  }
  const heic = picked.find((f) => /\.(heic|heif)$/i.test(f.name))
  if (heic && !limits.images.heic) {
    return { files: current, error: 'HEIC photos aren’t supported yet — export them as JPG (iPhone: Camera → Formats → Most Compatible).' }
  }
  const all = [...current.filter((f) => uploadKind(f.name) === 'image'), ...picked]
  const kinds = new Set(all.map((f) => uploadKind(f.name)))
  if (kinds.size === 1 && kinds.has('image')) {
    if (all.length > limits.maxImages) return { files: current, error: `Up to ${limits.maxImages} images per book.` }
    const big = all.find((f) => f.size > limits.maxImageMb * 1024 * 1024)
    if (big) return { files: current, error: `${big.name} is ${formatBytes(big.size)} — each image can be up to ${limits.maxImageMb} MB.` }
    if (!limits.images.vision && !limits.images.tesseract) {
      return { files: current, error: 'This server can’t read images yet (no vision model or OCR configured).' }
    }
    return { files: all, error: null }
  }
  if (picked.length > 1) {
    return { files: current, error: 'Choose one document or one video/audio file — or several photos (not a mix).' }
  }
  const f = picked[0]
  const kind = uploadKind(f.name)
  if (kind === 'document' && f.size > limits.maxUploadMb * 1024 * 1024) {
    return { files: current, error: `That file is ${formatBytes(f.size)} — the limit is ${limits.maxUploadMb} MB.` }
  }
  if ((kind === 'video' || kind === 'audio') && f.size > limits.maxMediaMb * 1024 * 1024) {
    return { files: current, error: `That recording is ${formatBytes(f.size)} — the limit is ${limits.maxMediaMb} MB.` }
  }
  if ((kind === 'video' || kind === 'audio') && !limits.transcription) {
    return { files: current, error: 'This server can’t transcribe video or audio yet (ffmpeg / faster-whisper missing).' }
  }
  return { files: [f], error: null }
}

function SyllabusToggle({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint: string }) {
  const id = useId()
  return (
    <div className="flex items-start gap-3 rounded-2xl border border-border bg-secondary/40 p-3">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 size-5 shrink-0 accent-[var(--primary)]"
        aria-describedby={`${id}-hint`}
      />
      <label htmlFor={id} className="min-w-0 cursor-pointer">
        <span className="block text-sm font-medium">{label}</span>
        <span id={`${id}-hint`} className="block text-xs text-muted-foreground">
          {hint}
        </span>
      </label>
    </div>
  )
}

/* ---------------------------------------------------------------- progress */

function StepIcon({ status }: { status: BookStep['status'] }) {
  if (status === 'done') return <Check className="size-4" aria-hidden="true" />
  if (status === 'running') return <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden="true" />
  if (status === 'failed') return <AlertCircle className="size-4" aria-hidden="true" />
  return <CircleDashed className="size-4" aria-hidden="true" />
}

const STEP_STATUS_TEXT: Record<BookStep['status'], string> = {
  done: 'done',
  running: 'in progress',
  failed: 'failed',
  pending: 'waiting',
  skipped: 'skipped',
}

function Timeline({ steps }: { steps: BookStep[] }) {
  return (
    <ol className="relative flex flex-col gap-0">
      {steps.map((s, i) => (
        <li key={s.key} className="relative flex gap-3 pb-5 last:pb-0">
          {i < steps.length - 1 && (
            <span
              aria-hidden="true"
              className={cn('absolute top-8 left-[15px] h-[calc(100%-2rem)] w-px', s.status === 'done' ? 'bg-primary' : 'bg-border')}
            />
          )}
          <span
            className={cn(
              'relative z-10 grid size-8 shrink-0 place-items-center rounded-full border',
              s.status === 'done' && 'border-primary bg-primary text-primary-foreground',
              s.status === 'running' && 'border-primary bg-secondary text-secondary-foreground',
              s.status === 'failed' && 'border-destructive bg-card text-destructive',
              (s.status === 'pending' || s.status === 'skipped') && 'border-border bg-card text-muted-foreground',
            )}
          >
            <StepIcon status={s.status} />
          </span>
          <div className="min-w-0 pt-1">
            <p className={cn('text-sm font-medium', s.status === 'pending' && 'text-muted-foreground')}>
              {s.label} <span className="sr-only">— {STEP_STATUS_TEXT[s.status]}</span>
            </p>
            {s.detail && <p className="mt-0.5 text-xs break-words text-muted-foreground">{s.detail}</p>}
          </div>
        </li>
      ))}
    </ol>
  )
}

function ProgressPanel({
  job,
  demo,
  onReset,
  onRetry,
  retrying,
  retryError,
  connectionError,
}: {
  job: BookJob | null
  demo: boolean
  onReset: () => void
  onRetry: () => void
  retrying: boolean
  retryError: string | null
  connectionError: string | null
}) {
  const failed = job?.status === 'failed'
  const canRetry = failed && !job?.busy && !demo
  const finished = job ? isFinished(job) : false
  const canOpen = Boolean(job && (job.bookReady || (job.status === 'ready' && demo)) && !failed)
  const progress = job?.progress ?? 0
  const writing = job?.lessons.filter((l) => l.status === 'done').length ?? 0

  return (
    <section aria-labelledby="book-progress-title" className="rounded-3xl border border-border bg-card p-5 sm:p-7">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold tracking-[0.18em] text-primary uppercase">
            {failed ? 'Something went wrong' : finished ? 'Your book is ready' : 'Writing your book'}
          </p>
          <h2 id="book-progress-title" className="mt-1 font-display text-2xl font-bold break-words">
            {job?.title ?? 'Starting…'}
          </h2>
          {job?.aiKnowledge && (
            <BookeyBadge tone="warning" className="mt-2" icon={<Sparkles />}>
              Generated from AI knowledge
            </BookeyBadge>
          )}
        </div>
        {demo && (
          <BookeyBadge tone="outline" className="self-start">
            Demo simulation
          </BookeyBadge>
        )}
      </div>

      <div className="mt-5">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>{failed ? 'Stopped' : finished ? 'Complete' : 'Progress'}</span>
          <span aria-hidden="true">{progress}%</span>
        </div>
        <div
          role="progressbar"
          aria-label="Book generation progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress}
          className="mt-1.5 h-2 overflow-hidden rounded-full bg-muted"
        >
          <div
            className={cn('h-full rounded-full motion-safe:transition-[width] motion-safe:duration-500', failed ? 'bg-destructive' : 'bg-primary')}
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <div className="mt-6" aria-live="polite">
        {job ? <Timeline steps={job.steps} /> : <p className="text-sm text-muted-foreground">Connecting…</p>}
      </div>

      {job?.error && (
        <p className="mt-5 flex gap-2 rounded-2xl border border-destructive/40 p-3 text-sm text-destructive" role="alert">
          <AlertCircle className="mt-0.5 size-4 shrink-0" /> {job.error}
        </p>
      )}
      {connectionError && !finished && (
        <p className="mt-4 text-xs text-muted-foreground">Live updates paused ({connectionError}); retrying…</p>
      )}
      {job && job.warnings.length > 0 && (
        <ul className="mt-5 space-y-1.5 text-xs text-muted-foreground">
          {job.warnings.slice(0, 4).map((w) => (
            <li key={w} className="flex gap-1.5">
              <AlertCircle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /> {w}
            </li>
          ))}
        </ul>
      )}

      <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:flex-wrap">
        {canOpen && job && (
          <BookeyButton size="lg" nativeButton={false} render={<Link href={`/book/${encodeURIComponent(job.courseId)}`} />}>
            <BookOpen /> {finished ? 'Open your book' : 'Start reading chapter 1'}
          </BookeyButton>
        )}
        {canRetry && (
          <BookeyButton size="lg" onClick={onRetry} disabled={retrying}>
            {retrying ? <Loader2 className="motion-safe:animate-spin" /> : <RotateCcw />} Retry
          </BookeyButton>
        )}
        {(finished || failed) && (
          <BookeyButton size="lg" variant="outline" onClick={onReset}>
            <RotateCcw /> Create another book
          </BookeyButton>
        )}
      </div>
      {retryError && (
        <p className="mt-3 text-xs text-destructive" role="alert">
          {retryError}
        </p>
      )}
      {canOpen && !finished && (
        <p className="mt-3 text-xs text-muted-foreground">
          Chapter 1 is ready{writing > 1 ? ` (${writing} lessons written)` : ''}. The rest keeps writing in the background — new
          pages appear when you reopen the book.
        </p>
      )}
    </section>
  )
}

/* ---------------------------------------------------------------- my books */

function MyBooks({ books, onRetry }: { books: MyBook[]; onRetry: (jobId: string) => void }) {
  if (books.length === 0) return null
  return (
    <section aria-labelledby="my-books-title" className="space-y-3">
      <h2 id="my-books-title" className="font-display text-xl font-semibold">
        Your books
      </h2>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {books.map((b) => (
          <li key={b.jobId} className="flex flex-col gap-2 rounded-2xl border border-border bg-card p-4">
            <p className="line-clamp-2 font-medium break-words">{b.title}</p>
            <div className="flex flex-wrap gap-1.5">
              <BookeyBadge tone={b.status === 'failed' ? 'neutral' : 'soft'}>
                {b.status === 'failed' ? 'Failed' : b.busy || b.status !== 'ready' ? `Writing · ${b.progress}%` : 'Ready'}
              </BookeyBadge>
              {b.aiKnowledge && <BookeyBadge tone="outline">AI knowledge</BookeyBadge>}
            </div>
            {b.status === 'failed' && !b.busy && (
              <BookeyButton variant="outline" size="sm" className="self-start" onClick={() => onRetry(b.jobId)}>
                <RotateCcw /> Retry
              </BookeyButton>
            )}
            {b.published && (
              <BookeyButton
                variant="link"
                className="mt-auto self-start"
                nativeButton={false}
                render={<Link href={`/book/${encodeURIComponent(b.courseId)}`} />}
              >
                Open book
              </BookeyButton>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

/* ---------------------------------------------------------------- page */

export function CreateBook() {
  const client = useMemo(() => resolveBooksClient(), [])
  const demo = client.mode === 'demo'

  const [mode, setMode] = useState<Mode>('file')
  const [files, setFiles] = useState<File[]>([])
  const [fileError, setFileError] = useState<string | null>(null)
  const [isSyllabus, setIsSyllabus] = useState(false)
  const [syllabusText, setSyllabusText] = useState('')
  const [limits, setLimits] = useState<BookLimits>(DEFAULT_LIMITS)
  const [link, setLink] = useState('')
  const [prompt, setPrompt] = useState('')
  const [title, setTitle] = useState('')
  const [level, setLevel] = useState<BookLevel>('beginner')
  const [language, setLanguage] = useState<BookLanguage>('tanglish')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<BookJob | null>(null)
  const [connectionError, setConnectionError] = useState<string | null>(null)
  const [retrying, setRetrying] = useState(false)
  const [retryError, setRetryError] = useState<string | null>(null)
  // bumped on retry so the subscription restarts even for the same job id
  const [subscription, setSubscription] = useState(0)
  const [books, setBooks] = useState<MyBook[]>([])
  const [quota, setQuota] = useState<BookQuota | null>(null)

  const linkType = link.trim() ? detectLink(link) : null
  const ids = { link: useId(), prompt: useId(), title: useId(), syllabus: useId(), extra: useId() }

  const refreshBooks = useCallback(() => {
    if (demo) return
    client.listMine().then(setBooks).catch(() => setBooks([]))
    client.quota().then(setQuota)
  }, [client, demo])

  useEffect(() => {
    client.limits().then((l) => l && setLimits(l))
  }, [client])

  // Reattach to a running job after a reload (?job=…).
  useEffect(() => {
    const fromUrl = new URLSearchParams(window.location.search).get('job')
    if (fromUrl && !demo) setJobId(fromUrl)
    refreshBooks()
  }, [demo, refreshBooks])

  useEffect(() => {
    if (!jobId) return
    setConnectionError(null)
    const stop = client.subscribe(
      jobId,
      (next) => {
        setJob(next)
        setConnectionError(null)
        if (isFinished(next)) refreshBooks()
      },
      (e) => setConnectionError(e.message),
    )
    return stop
  }, [client, jobId, refreshBooks, subscription])

  const retry = async (id: string) => {
    if (retrying) return
    setRetrying(true)
    setRetryError(null)
    try {
      await client.retry(id)
      setJob(null)
      setJobId(id)
      setSubscription((n) => n + 1)
      if (!demo) window.history.replaceState(null, '', `?job=${encodeURIComponent(id)}`)
      refreshBooks()
    } catch (err) {
      setRetryError((err as Error).message || 'Could not retry this book.')
    } finally {
      setRetrying(false)
    }
  }

  const addFiles = (picked: File[]) => {
    const res = mergeFiles(files, picked, limits)
    setFileError(res.error)
    setFiles(res.files)
  }
  const removeFile = (i: number) => {
    setFileError(null)
    setFiles((cur) => cur.filter((_, j) => j !== i))
  }
  const firstKind = files.length ? uploadKind(files[0].name) : null
  const isMedia = firstKind === 'video' || firstKind === 'audio' || firstKind === 'image'
  const extraLinkType = mode === 'syllabus' && link.trim() ? detectLink(link) : null

  const ready =
    (mode === 'file' && files.length > 0) ||
    (mode === 'link' && linkType !== null) ||
    (mode === 'prompt' && prompt.trim().length >= 4) ||
    (mode === 'syllabus' && syllabusText.trim().length >= 10 && (!link.trim() || extraLinkType !== null))

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!ready || submitting) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const res = await client.create({
        file: mode === 'file' && !isMedia ? files[0] : null,
        files: mode === 'file' && isMedia ? files : undefined,
        syllabus: mode === 'file' && isSyllabus,
        syllabusText: mode === 'syllabus' ? syllabusText : undefined,
        url: mode === 'link' || mode === 'syllabus' ? link : undefined,
        prompt: mode === 'prompt' ? prompt : undefined,
        title: title || undefined,
        level,
        language,
      })
      setJob(null)
      setJobId(res.jobId)
      if (!demo) window.history.replaceState(null, '', `?job=${encodeURIComponent(res.jobId)}`)
    } catch (err) {
      setSubmitError((err as Error).message || 'Could not start the book.')
    } finally {
      setSubmitting(false)
    }
  }

  const reset = () => {
    setJobId(null)
    setJob(null)
    setRetryError(null)
    setFiles([])
    setIsSyllabus(false)
    setSyllabusText('')
    setLink('')
    setPrompt('')
    setTitle('')
    if (!demo) window.history.replaceState(null, '', window.location.pathname)
  }

  const quotaFull = quota && !quota.exempt && (quota.running >= quota.maxConcurrent || quota.today >= quota.maxPerDay)

  return (
    <div className="flex flex-col gap-8">
      {demo && (
        <p className="flex gap-2 rounded-2xl border border-border bg-secondary/50 p-4 text-sm text-secondary-foreground" role="note">
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>
            <strong>Demo simulation.</strong> No Bookey API is configured, so nothing is uploaded — the progress below is
            simulated and ends by opening the seeded Python book.
          </span>
        </p>
      )}

      {jobId ? (
        <ProgressPanel
          job={job}
          demo={demo}
          onReset={reset}
          onRetry={() => jobId && retry(jobId)}
          retrying={retrying}
          retryError={retryError}
          connectionError={connectionError}
        />
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-6 rounded-3xl border border-border bg-card p-5 sm:p-7" noValidate>
          <Segmented label="What should the book be made from?" value={mode} options={MODES} onChange={setMode} />

          {mode === 'file' && (
            <div className="flex flex-col gap-3">
              <DropZone files={files} onAdd={addFiles} onRemove={removeFile} error={fileError} limits={limits} />
              {files.length > 0 && (
                <SyllabusToggle
                  checked={isSyllabus}
                  onChange={setIsSyllabus}
                  label={firstKind === 'image' ? 'These photos are my syllabus' : 'This is my syllabus'}
                  hint="The book follows it exactly — same units, same order. Lessons are filled from AI knowledge."
                />
              )}
              {(firstKind === 'video' || firstKind === 'audio') && (
                <p className="text-xs text-muted-foreground">
                  We transcribe it on our server (speech → text) and read the slides on screen. A 45-minute lecture takes a few
                  minutes — you can watch the progress.
                </p>
              )}
              {firstKind === 'image' && (
                <p className="text-xs text-muted-foreground">
                  {limits.images.engine === 'vision'
                    ? 'Handwritten notes, textbook pages and diagrams are read in Tamil and English.'
                    : 'Text is read with offline OCR (English only, diagrams not described).'}
                </p>
              )}
            </div>
          )}

          {mode === 'syllabus' && (
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-2">
                <label htmlFor={ids.syllabus} className="text-sm font-medium">
                  Your syllabus
                </label>
                <textarea
                  id={ids.syllabus}
                  rows={7}
                  maxLength={20000}
                  value={syllabusText}
                  onChange={(e) => setSyllabusText(e.target.value)}
                  placeholder={'Unit 1: Cell structure\n  - Cell wall and membrane\n  - Nucleus\nUnit 2: Plant nutrition\n  - Photosynthesis'}
                  className="min-h-40 rounded-2xl border border-input bg-background p-3 font-mono text-base outline-none placeholder:text-muted-foreground focus-visible:ring-4 focus-visible:ring-ring/30 sm:text-sm"
                />
                <p className="text-xs text-muted-foreground">
                  The book follows it exactly: one chapter per unit (up to {limits.syllabusMaxChapters}), its topics become lessons.
                  Photo of a syllabus? Use “Upload files” and tick “These photos are my syllabus”.
                </p>
              </div>
              <div className="flex flex-col gap-2">
                <label htmlFor={ids.extra} className="text-sm font-medium">
                  Extra material <span className="font-normal text-muted-foreground">(optional link to fill the content)</span>
                </label>
                <input
                  id={ids.extra}
                  type="url"
                  inputMode="url"
                  autoComplete="off"
                  value={link}
                  onChange={(e) => setLink(e.target.value)}
                  placeholder="YouTube lecture, article, GitHub repo or arXiv paper"
                  className="h-12 rounded-2xl border border-input bg-background px-3 text-base outline-none placeholder:text-muted-foreground focus-visible:ring-4 focus-visible:ring-ring/30 sm:text-sm"
                />
                {link.trim() && (
                  <p className="text-xs text-muted-foreground" aria-live="polite">
                    {extraLinkType ? `Detected: ${LINK_LABELS[extraLinkType]}` : 'That doesn’t look like a link yet.'}
                  </p>
                )}
              </div>
            </div>
          )}

          {mode === 'link' && (
            <div className="flex flex-col gap-2">
              <label htmlFor={ids.link} className="text-sm font-medium">
                Link
              </label>
              <div className="flex min-h-12 items-center gap-2 rounded-2xl border border-input bg-background px-3 focus-within:ring-4 focus-within:ring-ring/30">
                <span className="text-muted-foreground" aria-hidden="true">
                  {linkType ? <SourceGlyph kind={linkType} /> : <Link2 className="size-5" />}
                </span>
                <input
                  id={ids.link}
                  type="url"
                  inputMode="url"
                  autoComplete="off"
                  placeholder="YouTube, article, GitHub repo or arXiv paper"
                  value={link}
                  onChange={(e) => setLink(e.target.value)}
                  className="h-12 min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground sm:text-sm"
                  aria-describedby={`${ids.link}-hint`}
                />
              </div>
              <p id={`${ids.link}-hint`} className="text-xs text-muted-foreground" aria-live="polite">
                {link.trim()
                  ? linkType
                    ? `Detected: ${LINK_LABELS[linkType]}`
                    : 'That doesn’t look like a link yet.'
                  : 'Paste one link. Playlists aren’t supported — use a single video.'}
              </p>
            </div>
          )}

          {mode === 'prompt' && (
            <div className="flex flex-col gap-2">
              <label htmlFor={ids.prompt} className="text-sm font-medium">
                What do you want to learn?
              </label>
              <textarea
                id={ids.prompt}
                rows={4}
                maxLength={2000}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                placeholder="Teach me SQL joins for beginners, with real examples"
                className="min-h-28 rounded-2xl border border-input bg-background p-3 text-base outline-none placeholder:text-muted-foreground focus-visible:ring-4 focus-visible:ring-ring/30 sm:text-sm"
              />
              <div className="flex flex-wrap gap-2">
                {PROMPT_EXAMPLES.map((ex) => (
                  <button
                    key={ex}
                    type="button"
                    onClick={() => setPrompt(ex)}
                    className="min-h-9 rounded-full border border-border px-3 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground focus-visible:ring-4 focus-visible:ring-ring/30 focus-visible:outline-none"
                  >
                    {ex}
                  </button>
                ))}
              </div>
              <p className="text-xs text-muted-foreground">
                Without a source, Bookey writes from AI knowledge — the book is labelled so you know to double-check facts.
              </p>
            </div>
          )}

          <div className="grid gap-6 md:grid-cols-2">
            <Segmented label="Level" value={level} options={LEVELS} onChange={setLevel} />
            <Segmented label="Narration language" value={language} options={LANGUAGES} onChange={setLanguage} />
          </div>

          <div className="flex flex-col gap-2">
            <label htmlFor={ids.title} className="text-sm font-medium">
              Title <span className="font-normal text-muted-foreground">(optional)</span>
            </label>
            <input
              id={ids.title}
              value={title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="We’ll name it from your source if you leave this empty"
              className="h-12 rounded-2xl border border-input bg-background px-3 text-base outline-none placeholder:text-muted-foreground focus-visible:ring-4 focus-visible:ring-ring/30 sm:text-sm"
            />
          </div>

          {submitError && (
            <p className="flex gap-2 text-sm text-destructive" role="alert">
              <AlertCircle className="mt-0.5 size-4 shrink-0" /> {submitError}
            </p>
          )}

          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-xs text-muted-foreground">
              {quota
                ? quota.exempt
                  ? 'Admin account — no book limit.'
                  : `${quota.today}/${quota.maxPerDay} books today · ${quota.running}/${quota.maxConcurrent} writing now`
                : 'Chapter 1 is usually ready in about a minute; the rest keeps writing in the background.'}
            </p>
            <BookeyButton type="submit" size="lg" disabled={!ready || submitting || Boolean(quotaFull)} className="w-full sm:w-auto">
              {submitting ? <Loader2 className="motion-safe:animate-spin" /> : <Sparkles />}
              {submitting ? 'Starting…' : 'Create my book'}
            </BookeyButton>
          </div>
        </form>
      )}

      <MyBooks books={books} onRetry={retry} />
      {!jobId && retryError && (
        <p className="text-xs text-destructive" role="alert">
          {retryError}
        </p>
      )}
    </div>
  )
}
