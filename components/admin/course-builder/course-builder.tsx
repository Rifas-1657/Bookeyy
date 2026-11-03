'use client'

import { ArrowLeft, ArrowRight, CircleCheck, FlaskConical, Loader2, PartyPopper, RefreshCw, Rocket, Save, Wand2 } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { BookeyBadge } from '@/components/ui-kit/bookey-badge'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import { notify } from '@/components/ui-kit/toast'
import {
  demoCourseBuilder,
  resolveCourseBuilder,
  type BuildJob,
  type CourseBuilderClient,
  type CreateJobInput,
  type Outline,
  type SourceInput,
} from '@/lib/api/course-builder'
import { cn } from '@/lib/utils'
import { AdminPageHeader, AdminPanel, DemoNotice, Field, describedBy, inputClass, textareaClass } from '../admin-ui'
import { OutlineEditor } from './outline-editor'
import { EMPTY_DETAILS, STEPS, StepTimeline, Stepper, uid, type BuilderDetails, type DraftLinkSource, type DraftUpload } from './shared'
import { StepSources } from './step-sources'

const DRAFT_KEY = 'bookey:course-builder:draft'

interface Draft {
  details: BuilderDetails
  links: DraftLinkSource[]
  uploads: DraftUpload[]
  syllabus: string
  jobId: string | null
  step: number
  draftId: string
}

function loadDraft(): Draft | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY)
    return raw ? (JSON.parse(raw) as Draft) : null
  } catch {
    return null
  }
}

function ProgressBar({ value, label }: { value: number; label: string }) {
  return (
    <div>
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">{label}</span>
        <span className="font-mono text-xs text-muted-foreground">{Math.round(value)}%</span>
      </div>
      <div role="progressbar" aria-label={label} aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100} className="mt-2 h-2 overflow-hidden rounded-full bg-muted">
        <div className="h-full rounded-full bg-primary transition-[width] duration-500" style={{ width: `${value}%` }} />
      </div>
    </div>
  )
}

export function CourseBuilder() {
  const [client, setClient] = useState<CourseBuilderClient | null>(null)
  const [details, setDetails] = useState<BuilderDetails>(EMPTY_DETAILS)
  const [links, setLinks] = useState<DraftLinkSource[]>([])
  const [uploads, setUploads] = useState<DraftUpload[]>([])
  const [syllabus, setSyllabus] = useState('')
  const [step, setStep] = useState(0)
  const [draftId, setDraftId] = useState(() => `builder-${uid()}`)
  const [jobId, setJobId] = useState<string | null>(null)
  const [job, setJob] = useState<BuildJob | null>(null)
  const [outline, setOutline] = useState<Outline | null>(null)
  const [outlineDirty, setOutlineDirty] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [errors, setErrors] = useState<Partial<Record<keyof BuilderDetails, string>>>({})
  const [recent, setRecent] = useState<BuildJob[]>([])
  const [restored, setRestored] = useState(false)
  const ids = { title: useId(), desc: useId(), aud: useId(), level: useId() }
  const outlineSeeded = useRef<string | null>(null)

  const [connectError, setConnectError] = useState<string | null>(null)
  const connect = useCallback((force = false) => {
    setConnectError(null)
    resolveCourseBuilder(force)
      .then(setClient)
      .catch((e: Error) => setConnectError(e.message || 'The course builder did not respond'))
  }, [])

  // Resolve live vs demo, restore the draft.
  useEffect(() => {
    connect()
    const d = loadDraft()
    if (d) {
      setDetails({ ...EMPTY_DETAILS, ...d.details })
      setLinks((d.links ?? []).map((l) => ({ ...l, loading: false })))
      setUploads((d.uploads ?? []).filter((u) => u.status === 'ready'))
      setSyllabus(d.syllabus ?? '')
      setDraftId(d.draftId ?? `builder-${uid()}`)
      setJobId(d.jobId)
      setStep(d.jobId ? d.step : Math.min(d.step, 1))
    }
    setRestored(true)
  }, [])

  useEffect(() => {
    if (!restored) return
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ details, links, uploads, syllabus, jobId, step, draftId } satisfies Draft))
    } catch {
      // storage blocked — the draft simply won't survive a reload
    }
  }, [restored, details, links, uploads, syllabus, jobId, step, draftId])

  useEffect(() => {
    if (!client) return
    client.listJobs().then(setRecent).catch(() => setRecent([]))
  }, [client, job?.status])

  // Live job updates (SSE in live mode, simulation in demo mode).
  useEffect(() => {
    if (!client || !jobId) return
    let alive = true
    client.getJob(jobId).catch(() => {
      if (!alive) return
      // Demo jobs don't survive a reload; drop stale ids.
      setJobId(null)
      setJob(null)
      setStep((s) => Math.min(s, 1))
    })
    const off = client.subscribe(
      jobId,
      (j) => alive && setJob(j),
      (e) => alive && setError(e.message),
    )
    return () => {
      alive = false
      off()
    }
  }, [client, jobId])

  // Seed the editable outline once per job when it first arrives.
  useEffect(() => {
    if (job?.outline && outlineSeeded.current !== job.id) {
      outlineSeeded.current = job.id
      setOutline(job.outline)
      setOutlineDirty(false)
    }
  }, [job])

  const outlineReady = !!job && ['outline_ready', 'generating', 'ready', 'published'].includes(job.status)
  const reachable = jobId ? (outlineReady ? 4 : 2) : details.title.trim() ? 1 : 0

  const validateDetails = () => {
    const next: typeof errors = {}
    if (details.title.trim().length < 3) next.title = 'Give the course a title (3+ characters).'
    if (details.description.trim().length < 10) next.description = 'A sentence or two helps the outline (10+ characters).'
    setErrors(next)
    return Object.keys(next).length === 0
  }

  const sources: SourceInput[] = [
    ...links.map((l) => ({ type: l.type, url: l.url })),
    ...uploads.filter((u) => u.status === 'ready' && u.documentId).map((u) => ({ type: 'upload' as const, documentId: u.documentId! })),
  ]

  const run = useCallback(
    async (label: string, fn: () => Promise<void>) => {
      setBusy(label)
      setError(null)
      try {
        await fn()
      } catch (e) {
        setError((e as Error).message || 'Something went wrong')
      } finally {
        setBusy(null)
      }
    },
    [],
  )

  const startBuild = () =>
    run('build', async () => {
      if (!client) return
      const input: CreateJobInput = { ...details, title: details.title.trim(), sources, syllabusText: syllabus.trim() || undefined }
      const { jobId: id } = await client.createJob(input)
      outlineSeeded.current = null
      setJob(null)
      setOutline(null)
      setJobId(id)
      setStep(2)
    })

  const saveOutline = () =>
    run('save', async () => {
      if (!client || !jobId || !outline) return
      setJob(await client.patchOutline(jobId, outline))
      setOutlineDirty(false)
      notify.success('Outline saved')
    })

  const generate = () =>
    run('generate', async () => {
      if (!client || !jobId || !outline) return
      if (outlineDirty) {
        await client.patchOutline(jobId, outline)
        setOutlineDirty(false)
      }
      setJob(await client.generate(jobId))
      setStep(4)
    })

  const publish = () =>
    run('publish', async () => {
      if (!client || !jobId) return
      setJob(await client.publish(jobId))
      notify.success('Course published', client.mode === 'demo' ? 'Demo only — nothing was sent to a server.' : undefined)
    })

  const startOver = () => {
    setDetails(EMPTY_DETAILS)
    setLinks([])
    setUploads([])
    setSyllabus('')
    setJobId(null)
    setJob(null)
    setOutline(null)
    setDraftId(`builder-${uid()}`)
    setStep(0)
  }

  const switchToDemo = () => {
    setClient(demoCourseBuilder('Switched to demo after a live API error'))
    setError(null)
    setJobId(null)
    setJob(null)
    setStep((s) => Math.min(s, 1))
  }

  return (
    <div className="space-y-6">
      <AdminPageHeader
        title="Course builder"
        description="Turn research papers, GitHub projects, Reddit threads, lectures and documents into a full Bookey course with lessons, practicals, tasks and projects."
        actions={
          <>
            {client?.mode === 'live' && (
              <BookeyBadge tone="success" icon={<CircleCheck aria-hidden="true" />}>
                Live API
              </BookeyBadge>
            )}
            {client?.mode === 'demo' && (
              <BookeyBadge tone="warning" icon={<FlaskConical aria-hidden="true" />}>
                Demo simulation
              </BookeyBadge>
            )}
            <BookeyButton type="button" variant="outline" size="sm" onClick={startOver}>
              <RefreshCw aria-hidden="true" /> Start over
            </BookeyButton>
          </>
        }
      />

      {client?.mode === 'demo' && (
        <DemoNotice>
          <strong className="font-semibold">Demo simulation.</strong> {client.reason}. Builds, previews and uploads run in this browser tab only — nothing is
          sent to a server and published courses are not real.
        </DemoNotice>
      )}

      {!client && connectError ? (
        <div role="alert" className="flex flex-col gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
          <p className="text-destructive">{connectError}. Check that the backend is running.</p>
          <div className="flex gap-2">
            <BookeyButton type="button" size="sm" variant="outline" onClick={() => connect(true)}>
              <RefreshCw aria-hidden="true" /> Retry
            </BookeyButton>
            <BookeyButton type="button" size="sm" variant="outline" onClick={switchToDemo}>
              Continue in demo mode
            </BookeyButton>
          </div>
        </div>
      ) : !client ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden="true" className="size-4 motion-safe:animate-spin" /> Connecting to the course builder…
        </p>
      ) : (
        <>
          <Stepper current={step} reachable={reachable} onSelect={setStep} />

          {error && (
            <div role="alert" className="flex flex-col gap-3 rounded-2xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm sm:flex-row sm:items-center sm:justify-between">
              <p className="text-destructive">{error}</p>
              {client.mode === 'live' && (
                <BookeyButton type="button" size="sm" variant="outline" onClick={switchToDemo}>
                  Continue in demo mode
                </BookeyButton>
              )}
            </div>
          )}

          {step === 0 && (
            <div className="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
              <AdminPanel title="Course details">
                <form
                  className="space-y-5"
                  onSubmit={(e) => {
                    e.preventDefault()
                    if (validateDetails()) setStep(1)
                  }}
                  noValidate
                >
                  <Field id={ids.title} label="Title" error={errors.title}>
                    <input
                      id={ids.title}
                      value={details.title}
                      onChange={(e) => setDetails({ ...details, title: e.target.value })}
                      placeholder="Transformers, explained"
                      aria-invalid={errors.title ? true : undefined}
                      aria-describedby={describedBy(ids.title, errors.title)}
                      className={inputClass}
                    />
                  </Field>
                  <Field id={ids.desc} label="Description" error={errors.description} hint="What will learners be able to do at the end?">
                    <textarea
                      id={ids.desc}
                      rows={4}
                      value={details.description}
                      onChange={(e) => setDetails({ ...details, description: e.target.value })}
                      aria-invalid={errors.description ? true : undefined}
                      aria-describedby={describedBy(ids.desc, errors.description, 'hint')}
                      className={textareaClass}
                    />
                  </Field>
                  <div className="grid gap-5 sm:grid-cols-2">
                    <Field id={ids.level} label="Level">
                      <select id={ids.level} value={details.level} onChange={(e) => setDetails({ ...details, level: e.target.value as BuilderDetails['level'] })} className={inputClass}>
                        <option value="beginner">Beginner</option>
                        <option value="intermediate">Intermediate</option>
                        <option value="advanced">Advanced</option>
                      </select>
                    </Field>
                    <fieldset className="space-y-1.5">
                      <legend className="text-sm font-medium">Tutor language</legend>
                      <div className="flex rounded-xl border border-input p-1">
                        {(['tanglish', 'english'] as const).map((l) => (
                          <label
                            key={l}
                            className={cn(
                              'flex-1 cursor-pointer rounded-lg px-3 py-1.5 text-center text-sm capitalize transition-colors has-focus-visible:ring-4 has-focus-visible:ring-ring/30',
                              details.language === l ? 'bg-secondary font-medium text-secondary-foreground' : 'text-muted-foreground',
                            )}
                          >
                            <input type="radio" name="builder-language" value={l} checked={details.language === l} onChange={() => setDetails({ ...details, language: l })} className="sr-only" />
                            {l}
                          </label>
                        ))}
                      </div>
                    </fieldset>
                  </div>
                  <Field id={ids.aud} label="Target audience" hint="e.g. second-year engineering students new to ML">
                    <input id={ids.aud} value={details.targetAudience} onChange={(e) => setDetails({ ...details, targetAudience: e.target.value })} aria-describedby={`${ids.aud}-hint`} className={inputClass} />
                  </Field>
                  <div className="flex justify-end">
                    <BookeyButton type="submit">
                      Next: add sources <ArrowRight aria-hidden="true" />
                    </BookeyButton>
                  </div>
                </form>
              </AdminPanel>
              <AdminPanel title="Recent builds" description={client.mode === 'demo' ? 'Builds from this tab' : undefined}>
                {recent.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No builds yet.</p>
                ) : (
                  <ul className="space-y-2">
                    {recent.slice(0, 6).map((j) => (
                      <li key={j.id}>
                        <button
                          type="button"
                          onClick={() => {
                            outlineSeeded.current = null
                            setJobId(j.id)
                            setStep(j.outline ? 3 : 2)
                          }}
                          className="flex w-full items-center justify-between gap-3 rounded-xl border border-border px-3 py-2 text-left text-sm outline-none hover:bg-muted focus-visible:ring-4 focus-visible:ring-ring/30"
                        >
                          <span className="truncate">{j.title || j.id}</span>
                          <span className="shrink-0 text-xs text-muted-foreground">{j.status.replace('_', ' ')}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </AdminPanel>
            </div>
          )}

          {step === 1 && (
            <>
              <StepSources client={client} draftId={draftId} links={links} setLinks={setLinks} uploads={uploads} setUploads={setUploads} syllabus={syllabus} setSyllabus={setSyllabus} />
              <div className="flex flex-col-reverse gap-3 sm:flex-row sm:items-center sm:justify-between">
                <BookeyButton type="button" variant="ghost" onClick={() => setStep(0)}>
                  <ArrowLeft aria-hidden="true" /> Back
                </BookeyButton>
                <div className="flex flex-col items-stretch gap-2 sm:flex-row sm:items-center">
                  <p className="text-xs text-muted-foreground">
                    {sources.length} source{sources.length === 1 ? '' : 's'}
                    {syllabus.trim() ? ' + syllabus' : ''}
                    {uploads.some((u) => u.status === 'uploading') ? ' · waiting for uploads…' : ''}
                  </p>
                  <BookeyButton
                    type="button"
                    onClick={startBuild}
                    disabled={busy === 'build' || (sources.length === 0 && !syllabus.trim()) || uploads.some((u) => u.status === 'uploading')}
                  >
                    {busy === 'build' ? <Loader2 aria-hidden="true" className="motion-safe:animate-spin" /> : <Wand2 aria-hidden="true" />}
                    Build course outline
                  </BookeyButton>
                </div>
              </div>
            </>
          )}

          {step === 2 && (
            <div className="grid gap-6 lg:grid-cols-[1fr_1.2fr]">
              <AdminPanel title="Building your outline" description={details.title}>
                <div className="space-y-6" aria-live="polite">
                  <ProgressBar value={job?.progress ?? 0} label={job?.status === 'failed' ? 'Build failed' : outlineReady ? 'Outline ready' : 'Working'} />
                  <StepTimeline steps={job?.steps ?? []} />
                  {job?.error && <p className="text-sm text-destructive">{job.error}</p>}
                </div>
              </AdminPanel>
              <AdminPanel title="What happens next">
                <p className="text-sm leading-relaxed text-muted-foreground">
                  Bookey reads each source, connects ideas across them and drafts chapters, lessons, practicals, tasks and a project per chapter. You review and edit
                  everything before lessons are written.
                </p>
                <div className="mt-6 flex flex-wrap gap-2">
                  <BookeyButton type="button" disabled={!outlineReady} onClick={() => setStep(3)}>
                    Review outline <ArrowRight aria-hidden="true" />
                  </BookeyButton>
                  {job?.status === 'failed' && (
                    <BookeyButton type="button" variant="outline" onClick={startBuild}>
                      <RefreshCw aria-hidden="true" /> Retry build
                    </BookeyButton>
                  )}
                </div>
              </AdminPanel>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              <div className="sticky top-16 z-20 -mx-1 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-background/90 px-4 py-3 backdrop-blur">
                <p className="text-sm text-muted-foreground">
                  {outline?.chapters.length ?? 0} chapters · {outline?.chapters.reduce((n, c) => n + c.lessons.length, 0) ?? 0} lessons
                  {outlineDirty && <span className="ml-2 text-foreground">· unsaved changes</span>}
                </p>
                <div className="flex gap-2">
                  <BookeyButton type="button" variant="outline" size="sm" disabled={!outlineDirty || busy !== null} onClick={saveOutline}>
                    <Save aria-hidden="true" /> Save
                  </BookeyButton>
                  <BookeyButton type="button" size="sm" disabled={!outline || busy !== null || job?.status === 'generating'} onClick={generate}>
                    {busy === 'generate' ? <Loader2 aria-hidden="true" className="motion-safe:animate-spin" /> : <Wand2 aria-hidden="true" />}
                    Generate lessons
                  </BookeyButton>
                </div>
              </div>
              {outline ? (
                <OutlineEditor
                  outline={outline}
                  readOnly={job?.status === 'published'}
                  onChange={(o) => {
                    setOutline(o)
                    setOutlineDirty(true)
                  }}
                />
              ) : (
                <p className="text-sm text-muted-foreground">The outline isn’t ready yet.</p>
              )}
            </div>
          )}

          {step === 4 && (
            <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
              <AdminPanel title={job?.status === 'published' ? 'Published' : 'Generating lessons'} description={details.title}>
                <div className="space-y-6" aria-live="polite">
                  <ProgressBar value={job?.progress ?? 0} label={job?.status === 'ready' || job?.status === 'published' ? 'Course ready' : 'Writing lessons'} />
                  <StepTimeline steps={(job?.steps ?? []).slice(-5)} />
                </div>
              </AdminPanel>
              <AdminPanel title="Publish">
                {job?.status === 'published' ? (
                  <div className="space-y-4 text-center">
                    <PartyPopper aria-hidden="true" className="mx-auto size-10 text-primary" />
                    <p className="font-display text-xl font-semibold">Your course is live{client.mode === 'demo' ? ' (demo)' : ''}.</p>
                    <p className="text-sm text-muted-foreground">Learners will be guided through every lesson, task and project — and cheered on the whole way.</p>
                    <div className="flex flex-wrap justify-center gap-2">
                      <BookeyButton nativeButton={false} variant="outline" render={<Link href="/admin/courses" />}>
                        Go to courses
                      </BookeyButton>
                      <BookeyButton type="button" onClick={startOver}>
                        Build another
                      </BookeyButton>
                    </div>
                  </div>
                ) : (
                  <div className="space-y-4">
                    <ul className="space-y-2 text-sm">
                      <li>
                        <span className="text-muted-foreground">Language:</span> <span className="capitalize">{details.language}</span>
                      </li>
                      <li>
                        <span className="text-muted-foreground">Level:</span> <span className="capitalize">{details.level}</span>
                      </li>
                      <li>
                        <span className="text-muted-foreground">Chapters:</span> {outline?.chapters.length ?? 0}
                      </li>
                      <li>
                        <span className="text-muted-foreground">Sources:</span> {job?.sources.length ?? sources.length}
                      </li>
                    </ul>
                    <BookeyButton type="button" className="w-full" disabled={job?.status !== 'ready' || busy !== null} onClick={publish}>
                      {busy === 'publish' ? <Loader2 aria-hidden="true" className="motion-safe:animate-spin" /> : <Rocket aria-hidden="true" />}
                      Publish course
                    </BookeyButton>
                    {job?.status !== 'ready' && <p className="text-center text-xs text-muted-foreground">Available once lessons finish generating.</p>}
                    <BookeyButton type="button" variant="ghost" size="sm" onClick={() => setStep(3)}>
                      <ArrowLeft aria-hidden="true" /> Back to outline
                    </BookeyButton>
                  </div>
                )}
              </AdminPanel>
            </div>
          )}
        </>
      )}
      <p className="sr-only" aria-live="polite">
        Step {step + 1} of {STEPS.length}: {STEPS[step].label}
      </p>
    </div>
  )
}
