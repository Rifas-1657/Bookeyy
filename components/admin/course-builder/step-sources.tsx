'use client'

/* eslint-disable @next/next/no-img-element -- remote thumbnails of arbitrary hosts */
import { AlertCircle, FileUp, Link2, Loader2, Plus, Trash2 } from 'lucide-react'
import { useId, useRef, useState } from 'react'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { SOURCE_LABELS, SourceGlyph } from '@/components/ui-kit/source-glyphs'
import { detectSourceType, normalizeUrl, type CourseBuilderClient } from '@/lib/api/course-builder'
import { cn } from '@/lib/utils'
import { AdminPanel, inputClass, textareaClass } from '../admin-ui'
import { formatBytes, formatDuration, uid, type DraftLinkSource, type DraftUpload } from './shared'

const ACCEPT = '.pdf,.doc,.docx,.md,.txt,.html,application/pdf,text/plain,text/markdown'
const MAX_FILES = 20
/** Mirrors the backend's max_document_size_bytes (25 MB). */
const MAX_FILE_BYTES = 25 * 1024 * 1024

interface Props {
  client: CourseBuilderClient
  draftId: string
  links: DraftLinkSource[]
  setLinks: React.Dispatch<React.SetStateAction<DraftLinkSource[]>>
  uploads: DraftUpload[]
  setUploads: React.Dispatch<React.SetStateAction<DraftUpload[]>>
  syllabus: string
  setSyllabus: (v: string) => void
}

export function StepSources({ client, draftId, links, setLinks, uploads, setUploads, syllabus, setSyllabus }: Props) {
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const linkId = useId()
  const syllabusId = useId()
  const detected = value.trim() ? detectSourceType(value) : null

  const addLinks = (raw: string) => {
    const candidates = raw.split(/[\s,]+/).filter(Boolean)
    const added: DraftLinkSource[] = []
    for (const c of candidates) {
      const url = normalizeUrl(c)
      const type = detectSourceType(c)
      if (!url || !type) continue
      if (links.some((l) => l.url === url.href) || added.some((l) => l.url === url.href)) continue
      added.push({ id: uid(), type, url: url.href, loading: true })
    }
    if (!added.length) {
      setError(candidates.length ? 'That doesn’t look like a link. Paste a full URL, e.g. https://youtube.com/watch?v=…' : 'Paste a link first.')
      return
    }
    setError('')
    setValue('')
    setLinks((prev) => [...prev, ...added])
    for (const item of added) {
      client
        .previewSource(item.type, item.url)
        .then((preview) => setLinks((prev) => prev.map((l) => (l.id === item.id ? { ...l, preview, loading: false } : l))))
        .catch((e: Error) => setLinks((prev) => prev.map((l) => (l.id === item.id ? { ...l, loading: false, previewError: e.message } : l))))
    }
  }

  const addFiles = (files: File[]) => {
    const room = MAX_FILES - uploads.length
    const picked = files.slice(0, Math.max(0, room))
    if (!picked.length) return
    // Oversized files fail locally instead of making the whole batch upload fail.
    const tooBig = picked.filter((f) => f.size > MAX_FILE_BYTES)
    const batch = picked.filter((f) => f.size <= MAX_FILE_BYTES)
    const rejectedDrafts: DraftUpload[] = tooBig.map((f) => ({
      id: uid(),
      filename: f.name,
      sizeBytes: f.size,
      status: 'failed',
      error: `Larger than ${formatBytes(MAX_FILE_BYTES)}`,
    }))
    const drafts: DraftUpload[] = batch.map((f) => ({ id: uid(), filename: f.name, sizeBytes: f.size, status: 'uploading' }))
    setUploads((prev) => [...prev, ...drafts, ...rejectedDrafts])
    if (!batch.length) return
    client
      .uploadDocuments(batch, draftId)
      .then((res) => {
        // Match results to drafts in upload order, consuming each result once,
        // so two files with the same name each get their own outcome.
        const accepted = [...res.accepted]
        const rejected = [...res.rejected]
        const outcome = new Map<string, Partial<DraftUpload>>()
        for (const draft of drafts) {
          const okAt = accepted.findIndex((a) => a.filename === draft.filename)
          if (okAt !== -1) {
            outcome.set(draft.id, { status: 'ready', documentId: accepted[okAt].documentId })
            accepted.splice(okAt, 1)
            continue
          }
          const badAt = rejected.findIndex((r) => r.filename === draft.filename)
          outcome.set(draft.id, { status: 'failed', error: badAt !== -1 ? rejected[badAt].reason : 'Rejected' })
          if (badAt !== -1) rejected.splice(badAt, 1)
        }
        setUploads((prev) => prev.map((u) => (outcome.has(u.id) ? ({ ...u, ...outcome.get(u.id) } as DraftUpload) : u)))
      })
      .catch((e: Error) => setUploads((prev) => prev.map((u) => (drafts.some((d) => d.id === u.id) ? { ...u, status: 'failed', error: e.message } : u))))
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
      <div className="space-y-6">
        <AdminPanel title="Links" description="YouTube, GitHub, Reddit, arXiv, PDF links or any article. We detect the type for you.">
          <form
            onSubmit={(e) => {
              e.preventDefault()
              addLinks(value)
            }}
            className="space-y-2"
          >
            <label htmlFor={linkId} className="sr-only">
              Source link
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative flex-1">
                <span className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted-foreground">
                  {detected ? <SourceGlyph kind={detected} className="size-4 text-primary" /> : <Link2 aria-hidden="true" className="size-4" />}
                </span>
                <input
                  id={linkId}
                  value={value}
                  onChange={(e) => setValue(e.target.value)}
                  onPaste={(e) => {
                    const text = e.clipboardData.getData('text')
                    if (/\s/.test(text.trim())) {
                      e.preventDefault()
                      addLinks(text)
                    }
                  }}
                  placeholder="Paste any link — https://…"
                  inputMode="url"
                  aria-invalid={error ? true : undefined}
                  aria-describedby={`${linkId}-hint`}
                  className={cn(inputClass, 'pl-9')}
                />
              </div>
              <BookeyButton type="submit">
                <Plus aria-hidden="true" />
                Add source
              </BookeyButton>
            </div>
            <p id={`${linkId}-hint`} className={cn('text-xs', error ? 'font-medium text-destructive' : 'text-muted-foreground')} aria-live="polite">
              {error || (detected ? `Detected: ${SOURCE_LABELS[detected]}` : 'Tip: paste several links at once, separated by spaces or new lines.')}
            </p>
          </form>

          {links.length > 0 && (
            <ul className="mt-5 grid gap-3 sm:grid-cols-2">
              {links.map((l) => (
                <li key={l.id} className="group relative overflow-hidden rounded-2xl border border-border bg-background">
                  <div className="relative aspect-[16/7] bg-muted">
                    {l.preview?.thumbnailUrl ? (
                      <img src={l.preview.thumbnailUrl} alt="" loading="lazy" referrerPolicy="no-referrer" className="size-full object-cover" />
                    ) : (
                      <div className="grid size-full place-items-center text-muted-foreground">
                        <SourceGlyph kind={l.type} className="size-8" />
                      </div>
                    )}
                    <span className="absolute top-2 left-2 inline-flex items-center gap-1 rounded-full bg-card/90 px-2 py-0.5 text-[11px] font-medium backdrop-blur">
                      <SourceGlyph kind={l.type} className="size-3.5" />
                      {SOURCE_LABELS[l.type]}
                    </span>
                    {formatDuration(l.preview?.durationS) && (
                      <span className="absolute right-2 bottom-2 rounded-md bg-black/70 px-1.5 py-0.5 font-mono text-[11px] text-white">{formatDuration(l.preview?.durationS)}</span>
                    )}
                  </div>
                  <div className="space-y-0.5 p-3 pr-11">
                    {l.loading ? (
                      <p className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 aria-hidden="true" className="size-3.5 motion-safe:animate-spin" /> Fetching preview…
                      </p>
                    ) : (
                      <p className="line-clamp-2 text-sm font-medium">{l.preview?.title ?? l.url}</p>
                    )}
                    {l.previewError ? (
                      <p className="flex items-center gap-1 text-xs text-destructive">
                        <AlertCircle aria-hidden="true" className="size-3" /> Preview failed — it will still be used.
                      </p>
                    ) : (
                      <p className="truncate text-xs text-muted-foreground">{l.preview?.author ?? new URL(l.url).hostname}</p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => setLinks((prev) => prev.filter((x) => x.id !== l.id))}
                    className="absolute right-2 bottom-2 grid size-8 place-items-center rounded-full text-muted-foreground outline-none hover:bg-muted hover:text-destructive focus-visible:ring-4 focus-visible:ring-ring/30"
                  >
                    <Trash2 aria-hidden="true" className="size-4" />
                    <span className="sr-only">Remove {l.preview?.title ?? l.url}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </AdminPanel>

        <AdminPanel title="Documents" description="PDFs, Word docs, Markdown or text — up to 20 files.">
          <div
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              addFiles(Array.from(e.dataTransfer.files))
            }}
            className={cn(
              'flex flex-col items-center justify-center gap-3 rounded-2xl border-2 border-dashed p-8 text-center transition-colors',
              dragging ? 'border-primary bg-secondary' : 'border-border',
            )}
          >
            <FileUp aria-hidden="true" className="size-8 text-muted-foreground" />
            <p className="text-sm">
              <span className="font-medium">Drop files here</span> or
            </p>
            <BookeyButton type="button" variant="outline" size="sm" onClick={() => fileInput.current?.click()}>
              Choose files
            </BookeyButton>
            <input
              ref={fileInput}
              type="file"
              multiple
              accept={ACCEPT}
              className="sr-only"
              tabIndex={-1}
              onChange={(e) => {
                addFiles(Array.from(e.target.files ?? []))
                e.target.value = ''
              }}
            />
          </div>
          {uploads.length > 0 && (
            <ul className="mt-4 divide-y divide-border rounded-xl border border-border">
              {uploads.map((u) => (
                <li key={u.id} className="flex items-center gap-3 px-3 py-2.5 text-sm">
                  <SourceGlyph kind="upload" className="size-4 text-muted-foreground" />
                  <span className="min-w-0 flex-1 truncate">{u.filename}</span>
                  <span className="text-xs text-muted-foreground">{formatBytes(u.sizeBytes)}</span>
                  <span
                    className={cn(
                      'text-xs',
                      u.status === 'failed' ? 'text-destructive' : u.status === 'ready' ? 'text-emerald-700' : 'text-muted-foreground',
                    )}
                    title={u.error}
                  >
                    {u.status === 'uploading' ? 'Uploading…' : u.status === 'ready' ? 'Ready' : `Failed${u.error ? `: ${u.error}` : ''}`}
                  </span>
                  <button
                    type="button"
                    onClick={() => setUploads((prev) => prev.filter((x) => x.id !== u.id))}
                    className="grid size-8 place-items-center rounded-full text-muted-foreground outline-none hover:bg-muted hover:text-destructive focus-visible:ring-4 focus-visible:ring-ring/30"
                  >
                    <Trash2 aria-hidden="true" className="size-4" />
                    <span className="sr-only">Remove {u.filename}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </AdminPanel>
      </div>

      <AdminPanel title="Syllabus (optional)" description="Paste a syllabus or topic list. Each line can become a chapter.">
        <label htmlFor={syllabusId} className="sr-only">
          Syllabus
        </label>
        <textarea
          id={syllabusId}
          value={syllabus}
          onChange={(e) => setSyllabus(e.target.value)}
          rows={14}
          placeholder={'1. Variables and types\n2. Loops\n3. Functions\n4. Files and errors\n5. Final project'}
          className={cn(textareaClass, 'font-mono text-[13px]')}
        />
        <p className="mt-2 text-xs text-muted-foreground">{syllabus.trim() ? `${syllabus.trim().split(/\n/).filter(Boolean).length} lines` : 'Leave empty to let Bookey propose the structure.'}</p>
      </AdminPanel>
    </div>
  )
}
