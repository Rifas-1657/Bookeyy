'use client'

/**
 * Admin knowledge-base page — Phase-1 real implementation.
 *
 * Replaces the previous demo-only component.  All actions hit the real backend:
 *   • File upload  → POST /api/admin/documents/upload-batch
 *   • URL ingestion → POST /api/admin/documents/ingest-urls
 *   • Delete        → DELETE /api/admin/documents/{id}
 *   • Retry         → POST /api/admin/documents/{id}/retry
 *   • Live status   → GET /api/admin/documents/status-stream (SSE)
 *
 * When NEXT_PUBLIC_BOOKEY_API_URL is not set an "API not configured" notice is
 * shown.  No fake success messages or in-memory-only state.
 */

import {
  AlertCircle,
  CircleCheck,
  FileStack,
  FileUp,
  Link2,
  LoaderCircle,
  RefreshCw,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { BookeyBadge } from '@/components/ui-kit/bookey-badge'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { EmptyState } from '@/components/ui-kit/empty-state'
import { notify } from '@/components/ui-kit/toast'
import {
  deleteDocument,
  docStatusLabel,
  ingestUrls,
  isTerminal,
  KB_BASE_URL,
  listDocuments,
  mimeToType,
  retryDocument,
  uploadBatch,
  type KbDocument,
} from '@/lib/api/kb-client'
import { useAdminDemo } from '@/lib/stores/admin-demo'
import { formatBytes, formatDate } from '@/lib/admin/utils'
import { cn } from '@/lib/utils'
import {
  AdminPageHeader,
  ConfirmDialog,
  inputClass,
  TableScroller,
  tdClass,
  textareaClass,
  thClass,
} from '../admin-ui'

// ─────────────────────────────────────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────────────────────────────────────

const MAX_FILES = 20
const MAX_BYTES = 25 * 1024 * 1024   // 25 MB — matches backend limit
const ACCEPTED_EXTENSIONS = new Set(['.pdf', '.docx', '.md', '.txt'])
const POLL_INTERVAL_MS = 3000        // fallback poll when SSE is unavailable

// ─────────────────────────────────────────────────────────────────────────────
// Root component
// ─────────────────────────────────────────────────────────────────────────────

export function KnowledgeBase() {
  const courses = useAdminDemo((s) => s.courses)

  const [courseId, setCourseId] = useState<string>(() => courses[0]?.id ?? '')
  const [documents, setDocuments] = useState<KbDocument[]>([])
  const [loading, setLoading] = useState(false)
  const [apiError, setApiError] = useState<string | null>(null)

  // ── Initial load ──────────────────────────────────────────────────────────

  const refresh = useCallback(async () => {
    if (KB_BASE_URL === null) return
    try {
      const docs = await listDocuments(courseId || undefined)
      setDocuments(docs)
      setApiError(null)
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Failed to load documents.')
    }
  }, [courseId])

  useEffect(() => {
    setLoading(true)
    refresh().finally(() => setLoading(false))
  }, [refresh])

  // ── SSE live status ───────────────────────────────────────────────────────
  // Opens a Server-Sent Events connection while any document is non-terminal.
  // Falls back to polling when SSE is not supported or the server is unreachable.

  const hasActive = documents.some((d) => !isTerminal(d.status))

  useEffect(() => {
    if (KB_BASE_URL === null || !hasActive) return

    let es: EventSource | null = null
    let fallbackTimer: ReturnType<typeof setInterval> | null = null

    const applyUpdate = (raw: string) => {
      try {
        const event = JSON.parse(raw) as {
          kind: string
          id: string
          status: string
          chunk_count?: number | null
          error?: string | null
        }
        if (event.kind !== 'document') return
        setDocuments((prev) =>
          prev.map((d) =>
            d.id === event.id
              ? { ...d, status: event.status, chunk_count: event.chunk_count ?? d.chunk_count, error_message: event.error ?? d.error_message }
              : d,
          ),
        )
      } catch {
        /* ignore malformed frames */
      }
    }

    if (typeof EventSource !== 'undefined') {
      const param = courseId ? `?course_id=${encodeURIComponent(courseId)}` : ''
      es = new EventSource(`${KB_BASE_URL}/api/admin/documents/status-stream${param}`)
      es.onmessage = (e) => applyUpdate(e.data)
      es.onerror = () => {
        es?.close()
        // Fall back to polling
        fallbackTimer = setInterval(refresh, POLL_INTERVAL_MS)
      }
    } else {
      fallbackTimer = setInterval(refresh, POLL_INTERVAL_MS)
    }

    return () => {
      es?.close()
      if (fallbackTimer) clearInterval(fallbackTimer)
    }
  }, [hasActive, courseId, refresh])

  // ── Handlers passed down ──────────────────────────────────────────────────

  const handleUploaded = (newDocs: KbDocument[]) => {
    setDocuments((prev) => [...newDocs, ...prev])
  }

  const handleDelete = async (doc: KbDocument) => {
    try {
      await deleteDocument(doc.id)
      setDocuments((prev) => prev.filter((d) => d.id !== doc.id))
      notify.success(`Deleted "${doc.original_filename}"`)
    } catch (err) {
      notify.error('Delete failed', err instanceof Error ? err.message : undefined)
    }
  }

  const handleRetry = async (doc: KbDocument) => {
    try {
      const updated = await retryDocument(doc.id)
      setDocuments((prev) => prev.map((d) => (d.id === updated.id ? updated : d)))
      notify.info(`Re-queued "${doc.original_filename}"`)
    } catch (err) {
      notify.error('Retry failed', err instanceof Error ? err.message : undefined)
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────

  if (KB_BASE_URL === null) {
    return (
      <div className="space-y-6">
        <AdminPageHeader
          title="Knowledge base"
          description="Attach source documents and URLs to courses so the tutor can reference them."
        />
        <div
          role="alert"
          className="flex items-start gap-3 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-950"
        >
          <AlertCircle aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          <div>
            <p className="font-semibold">Backend not connected</p>
            <p className="mt-1">
              Set <code className="rounded bg-amber-100 px-1">NEXT_PUBLIC_BOOKEY_API_URL</code> to your
              running backend URL to enable real document ingestion.
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title="Knowledge base"
        description="Upload PDFs, DOCX, Markdown, or text files — or paste URLs — to give the tutor course-specific knowledge."
        actions={
          <div className="flex items-center gap-2">
            <label htmlFor="kb-course-select" className="sr-only">
              Select course
            </label>
            <select
              id="kb-course-select"
              value={courseId}
              onChange={(e) => setCourseId(e.target.value)}
              className={cn(inputClass, 'h-9 w-44')}
            >
              <option value="">All courses</option>
              {courses.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                </option>
              ))}
            </select>
            <BookeyButton
              variant="outline"
              size="sm"
              onClick={refresh}
              aria-label="Refresh document list"
            >
              <RefreshCw aria-hidden="true" className={loading ? 'animate-spin' : ''} />
            </BookeyButton>
          </div>
        }
      />

      {/* Upload zone — requires a course to be selected */}
      <UploadZone
        courseId={courseId}
        onUploaded={handleUploaded}
        disabled={!courseId}
      />

      {/* URL ingestion */}
      <UrlIngestZone
        courseId={courseId}
        onIngested={handleUploaded}
        disabled={!courseId}
      />

      {/* API error banner */}
      {apiError && (
        <div role="alert" className="flex items-center gap-2 rounded-2xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
          {apiError}
        </div>
      )}

      {/* Document table */}
      {loading && documents.length === 0 ? (
        <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
          <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
          Loading…
        </div>
      ) : documents.length === 0 ? (
        <EmptyState
          icon={<FileStack aria-hidden="true" />}
          title="No documents yet"
          description={
            courseId
              ? 'Upload files or paste URLs above to start building the knowledge base.'
              : 'Select a course above, then upload files or paste URLs.'
          }
        />
      ) : (
        <DocumentsTable
          documents={documents}
          onDelete={handleDelete}
          onRetry={handleRetry}
        />
      )}
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// UploadZone
// ─────────────────────────────────────────────────────────────────────────────

function UploadZone({
  courseId,
  onUploaded,
  disabled,
}: {
  courseId: string
  onUploaded: (docs: KbDocument[]) => void
  disabled: boolean
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [rejections, setRejections] = useState<string[]>([])

  const handleFiles = async (list: FileList | null) => {
    if (!list || list.length === 0) return
    const files = Array.from(list)
    const clientRejections: string[] = []
    const accepted: File[] = []

    for (const file of files.slice(0, MAX_FILES)) {
      const ext = ('.' + (file.name.split('.').pop() ?? '')).toLowerCase()
      if (!ACCEPTED_EXTENSIONS.has(ext)) {
        clientRejections.push(`${file.name}: unsupported type (use PDF, DOCX, MD, or TXT)`)
        continue
      }
      if (file.size === 0) {
        clientRejections.push(`${file.name}: file is empty`)
        continue
      }
      if (file.size > MAX_BYTES) {
        clientRejections.push(`${file.name}: ${formatBytes(file.size)} exceeds the 25 MB limit`)
        continue
      }
      accepted.push(file)
    }

    if (files.length > MAX_FILES) {
      clientRejections.push(`Only the first ${MAX_FILES} files were submitted.`)
    }

    setRejections(clientRejections)
    if (inputRef.current) inputRef.current.value = ''
    if (accepted.length === 0) return

    setUploading(true)
    try {
      const result = await uploadBatch(courseId, accepted)
      if (result.accepted.length > 0) {
        onUploaded(result.accepted)
        notify.success(
          `${result.accepted.length} file${result.accepted.length > 1 ? 's' : ''} queued for indexing`,
          'Processing runs in the background.',
        )
      }
      if (result.rejected.length > 0) {
        setRejections((prev) => [
          ...prev,
          ...result.rejected.map((r) => `${r.filename}: ${r.reason}`),
        ])
      }
    } catch (err) {
      notify.error('Upload failed', err instanceof Error ? err.message : undefined)
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="space-y-3">
      <div
        onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          if (!disabled) handleFiles(e.dataTransfer.files)
        }}
        aria-disabled={disabled}
        className={cn(
          'flex flex-col items-center gap-3 rounded-2xl border-2 border-dashed px-6 py-10 text-center transition-colors',
          dragging && !disabled ? 'border-primary bg-secondary' : 'border-border bg-card',
          disabled && 'cursor-not-allowed opacity-50',
        )}
      >
        <span aria-hidden="true" className="grid size-12 place-items-center rounded-2xl bg-secondary text-secondary-foreground">
          {uploading
            ? <LoaderCircle className="size-6 animate-spin" />
            : <FileUp className="size-6" />}
        </span>
        <div>
          <p className="font-medium">
            {disabled ? 'Select a course above to upload files' : 'Drag files here, or choose from your device'}
          </p>
          <p id="upload-hint" className="text-sm text-muted-foreground">
            PDF, DOCX, MD, or TXT · up to 25 MB each · up to {MAX_FILES} files at once
          </p>
        </div>
        <input
          ref={inputRef}
          id="kb-file-input"
          type="file"
          multiple
          accept=".pdf,.docx,.md,.txt"
          onChange={(e) => handleFiles(e.target.files)}
          disabled={disabled || uploading}
          aria-describedby="upload-hint"
          className="sr-only"
        />
        <BookeyButton
          type="button"
          variant="outline"
          disabled={disabled || uploading}
          onClick={() => inputRef.current?.click()}
        >
          {uploading ? 'Uploading…' : 'Choose files'}
        </BookeyButton>
      </div>

      <RejectionList rejections={rejections} onDismiss={() => setRejections([])} />
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// UrlIngestZone
// ─────────────────────────────────────────────────────────────────────────────

function UrlIngestZone({
  courseId,
  onIngested,
  disabled,
}: {
  courseId: string
  onIngested: (docs: KbDocument[]) => void
  disabled: boolean
}) {
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [rejections, setRejections] = useState<string[]>([])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const urls = text
      .split('\n')
      .map((u) => u.trim())
      .filter(Boolean)
    if (urls.length === 0) return

    setSubmitting(true)
    setRejections([])
    try {
      const result = await ingestUrls(courseId, urls)
      if (result.accepted.length > 0) {
        onIngested(result.accepted)
        notify.success(
          `${result.accepted.length} URL${result.accepted.length > 1 ? 's' : ''} queued for ingestion`,
          'Fetching and indexing runs in the background.',
        )
        setText('')
      }
      if (result.rejected.length > 0) {
        setRejections(result.rejected.map((r) => `${r.url}: ${r.reason}`))
      }
    } catch (err) {
      notify.error('URL ingestion failed', err instanceof Error ? err.message : undefined)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="rounded-2xl border border-border bg-card p-4 space-y-3">
        <div className="flex items-center gap-2">
          <Link2 aria-hidden="true" className="size-4 text-muted-foreground" />
          <span className="text-sm font-medium">Ingest from URLs</span>
          <span className="text-xs text-muted-foreground">(one per line, up to 20)</span>
        </div>
        <label htmlFor="kb-url-input" className="sr-only">
          URLs to ingest, one per line
        </label>
        <textarea
          id="kb-url-input"
          rows={3}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={disabled || submitting}
          placeholder={
            disabled
              ? 'Select a course above'
              : 'https://example.com/notes.pdf\nhttps://example.com/guide.md'
          }
          className={cn(textareaClass, 'font-mono text-xs')}
          aria-describedby="kb-url-hint"
        />
        <p id="kb-url-hint" className="text-xs text-muted-foreground">
          HTTPS only. PDFs, DOCX, Markdown, plain text, and HTML pages are accepted.
          Private / internal network addresses are blocked.
        </p>
        <div className="flex justify-end">
          <BookeyButton
            type="submit"
            variant="outline"
            size="sm"
            disabled={disabled || submitting || text.trim() === ''}
          >
            {submitting ? (
              <>
                <LoaderCircle aria-hidden="true" className="animate-spin" />
                Submitting…
              </>
            ) : (
              <>
                <Link2 aria-hidden="true" />
                Ingest URLs
              </>
            )}
          </BookeyButton>
        </div>
      </div>
      <RejectionList rejections={rejections} onDismiss={() => setRejections([])} />
    </form>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// DocumentsTable
// ─────────────────────────────────────────────────────────────────────────────

function DocumentsTable({
  documents,
  onDelete,
  onRetry,
}: {
  documents: KbDocument[]
  onDelete: (doc: KbDocument) => void
  onRetry: (doc: KbDocument) => void
}) {
  const [pendingDelete, setPendingDelete] = useState<KbDocument | null>(null)

  return (
    <>
      <TableScroller label="Knowledge base documents">
        <table className="w-full min-w-[60rem] text-sm">
          <caption className="sr-only">
            Source documents with type, size, source, and indexing status
          </caption>
          <thead className="border-b border-border bg-muted/50">
            <tr>
              <th scope="col" className={thClass}>File / URL</th>
              <th scope="col" className={thClass}>Type</th>
              <th scope="col" className={`${thClass} text-right`}>Size</th>
              <th scope="col" className={thClass}>Course</th>
              <th scope="col" className={thClass}>Status</th>
              <th scope="col" className={thClass}>Chunks</th>
              <th scope="col" className={`${thClass} text-right`}>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {documents.map((doc) => (
              <DocumentRow
                key={doc.id}
                doc={doc}
                onDeleteClick={() => setPendingDelete(doc)}
                onRetry={onRetry}
              />
            ))}
          </tbody>
        </table>
      </TableScroller>

      <ConfirmDialog
        open={pendingDelete !== null}
        onOpenChange={(open) => !open && setPendingDelete(null)}
        title="Delete document?"
        description={`"${pendingDelete?.original_filename ?? ''}" and all its indexed chunks will be permanently removed.`}
        confirmLabel="Delete document"
        onConfirm={() => {
          if (pendingDelete) onDelete(pendingDelete)
        }}
      />
    </>
  )
}

function DocumentRow({
  doc,
  onDeleteClick,
  onRetry,
}: {
  doc: KbDocument
  onDeleteClick: () => void
  onRetry: (doc: KbDocument) => void
}) {
  const isUrl = doc.source_type === 'url'
  const canRetry = doc.status === 'failed' || doc.status === 'duplicate'

  return (
    <tr className="hover:bg-muted/40">
      {/* File / URL */}
      <td className={tdClass}>
        <p
          className="max-w-64 truncate font-medium"
          title={isUrl ? (doc.source_url ?? doc.original_filename) : doc.original_filename}
        >
          {doc.original_filename}
        </p>
        {isUrl && doc.source_url && (
          <p className="mt-0.5 max-w-64 truncate text-xs text-muted-foreground" title={doc.source_url}>
            {doc.source_url}
          </p>
        )}
        <p className="text-xs text-muted-foreground">{formatDate(doc.created_at)}</p>
      </td>

      {/* Type */}
      <td className={tdClass}>
        <BookeyBadge tone="outline">{mimeToType(doc.mime_type)}</BookeyBadge>
      </td>

      {/* Size */}
      <td className={`${tdClass} text-right whitespace-nowrap tabular-nums`}>
        {doc.size_bytes > 0 ? formatBytes(doc.size_bytes) : '—'}
      </td>

      {/* Course */}
      <td className={tdClass}>
        <span className="font-mono text-xs text-muted-foreground">{doc.course_id}</span>
      </td>

      {/* Status */}
      <td className={tdClass}>
        <DocStatusBadge doc={doc} />
      </td>

      {/* Chunks */}
      <td className={tdClass}>
        <span className="tabular-nums text-muted-foreground">
          {doc.chunk_count != null ? doc.chunk_count.toLocaleString() : '—'}
        </span>
      </td>

      {/* Actions */}
      <td className={tdClass}>
        <div className="flex items-center justify-end gap-1">
          {canRetry && (
            <BookeyButton
              variant="ghost"
              size="sm"
              onClick={() => onRetry(doc)}
              aria-label={`Retry ${doc.original_filename}`}
            >
              <RefreshCw aria-hidden="true" />
              Retry
            </BookeyButton>
          )}
          <button
            type="button"
            onClick={onDeleteClick}
            className="grid size-9 place-items-center rounded-full text-muted-foreground outline-none hover:bg-destructive/10 hover:text-destructive focus-visible:ring-4 focus-visible:ring-ring/30"
            aria-label={`Delete ${doc.original_filename}`}
          >
            <Trash2 aria-hidden="true" className="size-4" />
          </button>
        </div>
      </td>
    </tr>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// DocStatusBadge
// ─────────────────────────────────────────────────────────────────────────────

function DocStatusBadge({ doc }: { doc: KbDocument }) {
  switch (doc.status) {
    case 'indexed':
      return (
        <BookeyBadge tone="success" icon={<CircleCheck aria-hidden="true" />}>
          Indexed
        </BookeyBadge>
      )
    case 'failed':
      return (
        <div className="space-y-1">
          <BookeyBadge tone="warning" icon={<TriangleAlert aria-hidden="true" />}>
            Failed
          </BookeyBadge>
          {doc.error_message && (
            <p className="max-w-48 text-xs text-destructive" title={doc.error_message}>
              {doc.error_message.slice(0, 80)}{doc.error_message.length > 80 ? '…' : ''}
            </p>
          )}
        </div>
      )
    case 'duplicate':
      return (
        <BookeyBadge tone="neutral" icon={<AlertCircle aria-hidden="true" />}>
          Duplicate
        </BookeyBadge>
      )
    case 'processing':
      return (
        <div className="flex items-center gap-1.5 text-xs font-medium">
          <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin motion-reduce:animate-none" />
          Processing
        </div>
      )
    case 'uploaded':
      return <BookeyBadge tone="neutral">Queued</BookeyBadge>
    default:
      return <BookeyBadge tone="neutral">{docStatusLabel(doc.status)}</BookeyBadge>
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// RejectionList
// ─────────────────────────────────────────────────────────────────────────────

function RejectionList({
  rejections,
  onDismiss,
}: {
  rejections: string[]
  onDismiss: () => void
}) {
  if (rejections.length === 0) return null
  return (
    <div role="alert" className="rounded-2xl border border-destructive/30 bg-destructive/5 p-4 text-sm">
      <div className="flex items-start justify-between gap-2">
        <p className="flex items-center gap-2 font-semibold text-destructive">
          <TriangleAlert aria-hidden="true" className="size-4 shrink-0" />
          {rejections.length} {rejections.length === 1 ? 'item was' : 'items were'} not accepted
        </p>
        <button
          type="button"
          onClick={onDismiss}
          className="rounded-full text-destructive opacity-70 hover:opacity-100"
          aria-label="Dismiss"
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      </div>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">
        {rejections.map((r, i) => (
          <li key={i}>{r}</li>
        ))}
      </ul>
    </div>
  )
}
