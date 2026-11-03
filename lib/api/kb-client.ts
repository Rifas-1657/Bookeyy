/**
 * lib/api/kb-client.ts
 *
 * Typed client for the Phase-1 knowledge-base ingestion endpoints.
 *
 *   POST /api/admin/documents/upload-batch   — multi-file upload
 *   POST /api/admin/documents/ingest-urls    — URL batch
 *   GET  /api/admin/documents                — list documents
 *   GET  /api/admin/documents/jobs           — list SourceJobs
 *   GET  /api/admin/documents/jobs/{id}      — single job with children
 *   DELETE /api/admin/documents/{id}         — delete document
 *   POST   /api/admin/documents/{id}/retry   — retry failed document
 *
 * Auth: session token from lib/api/auth.ts (refreshed on 401).
 * All endpoints require admin role.
 *
 * When NEXT_PUBLIC_BOOKEY_API_URL is unset the component falls back to showing
 * an "API not configured" notice — there is no mock fallback for admin writes.
 */

import { authorizedFetch } from './auth'
import { API_BASE_URL } from './base-url'

/** `null` without a backend; `''` (relative) in same-origin mode. Compare with `=== null`. */
export const KB_BASE_URL = API_BASE_URL

// ── Types (mirror backend Pydantic models exactly) ──────────────────────────

export interface KbDocument {
  id: string
  course_id: string
  original_filename: string
  mime_type: string
  size_bytes: number
  status: string            // uploaded | processing | indexed | failed | duplicate
  error_message: string | null
  page_count: number | null
  chunk_count: number | null
  embedding_model: string | null
  uploaded_by: string
  source_type: string       // file | url
  source_url: string | null
  source_job_id: string | null
  content_hash: string | null
  created_at: string
  updated_at: string
}

export interface SourceJob {
  id: string
  course_id: string
  kind: string              // file | url | mixed
  status: string            // queued | processing | done | partial | failed
  total_items: number
  done_items: number
  failed_items: number
  progress_pct: number
  error_summary: string | null
  submitted_by: string
  created_at: string
  finished_at: string | null
}

export interface SourceJobDetail extends SourceJob {
  documents: KbDocument[]
}

export interface BatchUploadResult {
  job_id: string
  accepted: KbDocument[]
  rejected: Array<{ filename: string; reason: string }>
}

export interface UrlIngestResult {
  job_id: string
  accepted: KbDocument[]
  rejected: Array<{ url: string; reason: string }>
}

// ── Internal fetch helper ───────────────────────────────────────────────────

async function kbFetch<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  if (KB_BASE_URL === null) throw new Error('NEXT_PUBLIC_BOOKEY_API_URL is not set.')
  // authorizedFetch leaves Content-Type unset for FormData so the browser adds the boundary.
  const res = await authorizedFetch(path, options)
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    const message =
      (detail && typeof detail === 'object' && 'detail' in detail)
        ? String(detail.detail)
        : res.statusText
    throw new Error(`${res.status}: ${message}`)
  }
  // 204 No Content
  if (res.status === 204) return undefined as unknown as T
  return res.json() as Promise<T>
}

// ── Public API ───────────────────────────────────────────────────────────────

/**
 * Upload 1–20 files for a course.
 * Returns immediately with accepted/rejected breakdown and a job_id.
 */
export async function uploadBatch(
  courseId: string,
  files: File[],
): Promise<BatchUploadResult> {
  const form = new FormData()
  form.append('course_id', courseId)
  for (const file of files) {
    form.append('files', file, file.name)
  }
  return kbFetch<BatchUploadResult>('/api/admin/documents/upload-batch', {
    method: 'POST',
    body: form,
  })
}

/**
 * Submit 1–20 URLs for ingestion.
 * Syntactic + SSRF validation happens on the server before any DB write.
 */
export async function ingestUrls(
  courseId: string,
  urls: string[],
): Promise<UrlIngestResult> {
  return kbFetch<UrlIngestResult>('/api/admin/documents/ingest-urls', {
    method: 'POST',
    body: JSON.stringify({ course_id: courseId, urls }),
  })
}

/** List documents, optionally filtered by course. */
export async function listDocuments(courseId?: string): Promise<KbDocument[]> {
  const params = courseId ? `?course_id=${encodeURIComponent(courseId)}` : ''
  return kbFetch<KbDocument[]>(`/api/admin/documents${params}`)
}

/** List SourceJobs, optionally filtered by course. */
export async function listJobs(courseId?: string): Promise<SourceJob[]> {
  const params = courseId ? `?course_id=${encodeURIComponent(courseId)}` : ''
  return kbFetch<SourceJob[]>(`/api/admin/documents/jobs${params}`)
}

/** Get a single SourceJob with its child documents. */
export async function getJob(jobId: string): Promise<SourceJobDetail> {
  return kbFetch<SourceJobDetail>(`/api/admin/documents/jobs/${jobId}`)
}

/** Delete a document (and its chunks + vectors). */
export async function deleteDocument(docId: string): Promise<void> {
  return kbFetch<void>(`/api/admin/documents/${docId}`, { method: 'DELETE' })
}

/** Retry a failed or duplicate document. */
export async function retryDocument(docId: string): Promise<KbDocument> {
  return kbFetch<KbDocument>(`/api/admin/documents/${docId}/retry`, {
    method: 'POST',
  })
}

// ── Derived helpers ──────────────────────────────────────────────────────────

export type DocStatus = KbDocument['status']
export type JobStatus = SourceJob['status']

export function isTerminal(status: DocStatus | JobStatus): boolean {
  return ['indexed', 'failed', 'duplicate', 'done', 'partial'].includes(status)
}

export function docStatusLabel(status: DocStatus): string {
  switch (status) {
    case 'uploaded':    return 'Queued'
    case 'processing':  return 'Processing'
    case 'indexed':     return 'Indexed'
    case 'failed':      return 'Failed'
    case 'duplicate':   return 'Duplicate'
    default:            return status
  }
}

export function mimeToType(mime: string): string {
  if (mime === 'application/pdf') return 'PDF'
  if (mime.includes('wordprocessingml')) return 'DOCX'
  if (mime === 'text/markdown' || mime === 'text/x-markdown') return 'MD'
  if (mime === 'text/plain') return 'TXT'
  if (mime === 'text/html') return 'HTML'
  return mime.split('/')[1]?.toUpperCase() ?? '?'
}
