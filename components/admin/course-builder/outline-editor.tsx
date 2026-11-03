'use client'

import { ArrowDown, ArrowUp, ChevronDown, GripVertical, Hammer, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { BookeyButton } from '@/components/ui-kit/bookey-button'
import type { Chapter, Lesson, Outline } from '@/lib/api/course-builder'
import { cn } from '@/lib/utils'
import { inputClass, textareaClass } from '../admin-ui'

const PRACTICAL_KINDS = ['code', 'quiz', 'exercise', 'reflection', 'diagram']

function move<T>(list: T[], from: number, to: number): T[] {
  if (to < 0 || to >= list.length || from === to) return list
  const next = [...list]
  const [item] = next.splice(from, 1)
  next.splice(to, 0, item)
  return next
}

const lines = (v: string) => v.split('\n').map((l) => l.trim()).filter(Boolean)

function IconButton({ label, onClick, disabled, children, danger }: { label: string; onClick: () => void; disabled?: boolean; children: React.ReactNode; danger?: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={label}
      className={cn(
        'grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground outline-none hover:bg-muted focus-visible:ring-4 focus-visible:ring-ring/30 disabled:opacity-30',
        danger ? 'hover:text-destructive' : 'hover:text-foreground',
      )}
    >
      {children}
      <span className="sr-only">{label}</span>
    </button>
  )
}

function LinesField({ id, label, value, onChange, placeholder }: { id: string; label: string; value: string[]; onChange: (v: string[]) => void; placeholder?: string }) {
  const [text, setText] = useState(value.join('\n'))
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="text-xs font-medium text-muted-foreground">
        {label} <span className="font-normal">(one per line)</span>
      </label>
      <textarea id={id} rows={3} value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)} onBlur={() => onChange(lines(text))} className={cn(textareaClass, 'min-h-20 text-[13px]')} />
    </div>
  )
}

function LessonEditor({ lesson, idPrefix, onChange }: { lesson: Lesson; idPrefix: string; onChange: (l: Lesson) => void }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <LinesField id={`${idPrefix}-obj`} label="Objectives" key={lesson.objectives.join('|')} value={lesson.objectives} onChange={(objectives) => onChange({ ...lesson, objectives })} />
      <LinesField id={`${idPrefix}-tasks`} label="Tasks" key={lesson.tasks.join('|')} value={lesson.tasks} onChange={(tasks) => onChange({ ...lesson, tasks })} />
      <div className="space-y-2 sm:col-span-2">
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="space-y-1 sm:w-40">
            <label htmlFor={`${idPrefix}-pk`} className="text-xs font-medium text-muted-foreground">
              Practical
            </label>
            <select
              id={`${idPrefix}-pk`}
              value={lesson.practical.kind}
              onChange={(e) => onChange({ ...lesson, practical: { ...lesson.practical, kind: e.target.value } })}
              className={inputClass}
            >
              {Array.from(new Set([lesson.practical.kind, ...PRACTICAL_KINDS])).map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          </div>
          <div className="flex-1 space-y-1">
            <label htmlFor={`${idPrefix}-pp`} className="text-xs font-medium text-muted-foreground">
              Prompt
            </label>
            <input
              id={`${idPrefix}-pp`}
              value={lesson.practical.prompt}
              onChange={(e) => onChange({ ...lesson, practical: { ...lesson.practical, prompt: e.target.value } })}
              className={inputClass}
            />
          </div>
        </div>
        {lesson.practical.kind === 'code' && (
          <div className="space-y-1">
            <label htmlFor={`${idPrefix}-sc`} className="text-xs font-medium text-muted-foreground">
              Starter code
            </label>
            <textarea
              id={`${idPrefix}-sc`}
              rows={5}
              spellCheck={false}
              value={lesson.practical.starterCode ?? ''}
              onChange={(e) => onChange({ ...lesson, practical: { ...lesson.practical, starterCode: e.target.value } })}
              className={cn(textareaClass, 'font-mono text-[12.5px]')}
            />
          </div>
        )}
      </div>
      <div className="sm:col-span-2">
        <LinesField id={`${idPrefix}-dg`} label="Diagram ideas" key={lesson.diagramIdeas.join('|')} value={lesson.diagramIdeas} onChange={(diagramIdeas) => onChange({ ...lesson, diagramIdeas })} />
      </div>
    </div>
  )
}

const blankLesson = (): Lesson => ({ title: 'New lesson', objectives: [], practical: { kind: 'exercise', prompt: '' }, tasks: [], diagramIdeas: [] })
const blankChapter = (): Chapter => ({ title: 'New chapter', summary: '', lessons: [blankLesson()], project: { title: '', brief: '' } })

export function OutlineEditor({ outline, onChange, readOnly }: { outline: Outline; onChange: (o: Outline) => void; readOnly?: boolean }) {
  const [openLesson, setOpenLesson] = useState<string | null>(null)
  const [dragFrom, setDragFrom] = useState<number | null>(null)
  const [dragOver, setDragOver] = useState<number | null>(null)
  const chapters = outline.chapters
  const setChapters = (next: Chapter[]) => onChange({ chapters: next })
  const updateChapter = (i: number, patch: Partial<Chapter>) => setChapters(chapters.map((c, j) => (j === i ? { ...c, ...patch } : c)))

  return (
    <div className="space-y-4">
      <ol className="space-y-4">
        {chapters.map((ch, ci) => (
          <li
            key={ci}
            onDragOver={(e) => {
              if (dragFrom === null) return
              e.preventDefault()
              setDragOver(ci)
            }}
            onDrop={(e) => {
              e.preventDefault()
              if (dragFrom !== null) setChapters(move(chapters, dragFrom, ci))
              setDragFrom(null)
              setDragOver(null)
            }}
            className={cn('rounded-2xl border bg-card transition-colors', dragOver === ci && dragFrom !== ci ? 'border-primary' : 'border-border', dragFrom === ci && 'opacity-60')}
          >
            <div className="flex items-start gap-2 border-b border-border p-3 sm:p-4">
              <span
                draggable={!readOnly}
                onDragStart={(e) => {
                  e.dataTransfer.effectAllowed = 'move'
                  setDragFrom(ci)
                }}
                onDragEnd={() => {
                  setDragFrom(null)
                  setDragOver(null)
                }}
                className="mt-2 hidden cursor-grab text-muted-foreground sm:block"
                aria-hidden="true"
              >
                <GripVertical className="size-4" />
              </span>
              <span className="mt-2.5 font-mono text-xs text-primary">{String(ci + 1).padStart(2, '0')}</span>
              <div className="min-w-0 flex-1 space-y-2">
                <label className="sr-only" htmlFor={`ch-${ci}-title`}>
                  Chapter {ci + 1} title
                </label>
                <input id={`ch-${ci}-title`} value={ch.title} readOnly={readOnly} onChange={(e) => updateChapter(ci, { title: e.target.value })} className={cn(inputClass, 'font-display font-semibold')} />
                <label className="sr-only" htmlFor={`ch-${ci}-summary`}>
                  Chapter {ci + 1} summary
                </label>
                <textarea id={`ch-${ci}-summary`} rows={2} value={ch.summary} readOnly={readOnly} onChange={(e) => updateChapter(ci, { summary: e.target.value })} placeholder="Summary" className={cn(textareaClass, 'min-h-14 text-[13px]')} />
              </div>
              {!readOnly && (
                <div className="flex flex-col sm:flex-row">
                  <IconButton label={`Move chapter ${ci + 1} up`} disabled={ci === 0} onClick={() => setChapters(move(chapters, ci, ci - 1))}>
                    <ArrowUp className="size-4" aria-hidden="true" />
                  </IconButton>
                  <IconButton label={`Move chapter ${ci + 1} down`} disabled={ci === chapters.length - 1} onClick={() => setChapters(move(chapters, ci, ci + 1))}>
                    <ArrowDown className="size-4" aria-hidden="true" />
                  </IconButton>
                  <IconButton danger label={`Delete chapter ${ci + 1}`} disabled={chapters.length <= 1} onClick={() => setChapters(chapters.filter((_, j) => j !== ci))}>
                    <Trash2 className="size-4" aria-hidden="true" />
                  </IconButton>
                </div>
              )}
            </div>

            <ol className="divide-y divide-border">
              {ch.lessons.map((lesson, li) => {
                const key = `${ci}-${li}`
                const open = openLesson === key
                const setLessons = (next: Lesson[]) => updateChapter(ci, { lessons: next })
                return (
                  <li key={li} className="px-3 py-2 sm:px-4">
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        aria-expanded={open}
                        onClick={() => setOpenLesson(open ? null : key)}
                        className="grid size-8 shrink-0 place-items-center rounded-full text-muted-foreground outline-none hover:bg-muted focus-visible:ring-4 focus-visible:ring-ring/30"
                      >
                        <ChevronDown aria-hidden="true" className={cn('size-4 transition-transform', open && 'rotate-180')} />
                        <span className="sr-only">{open ? 'Collapse' : 'Expand'} lesson {li + 1}</span>
                      </button>
                      <label className="sr-only" htmlFor={`ls-${key}`}>
                        Lesson {li + 1} title
                      </label>
                      <input
                        id={`ls-${key}`}
                        value={lesson.title}
                        readOnly={readOnly}
                        onChange={(e) => setLessons(ch.lessons.map((l, j) => (j === li ? { ...l, title: e.target.value } : l)))}
                        className={cn(inputClass, 'h-9 flex-1 border-transparent bg-transparent hover:border-input')}
                      />
                      <span className="hidden rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground sm:inline">{lesson.practical.kind}</span>
                      {!readOnly && (
                        <>
                          <IconButton label={`Move lesson ${li + 1} up`} disabled={li === 0} onClick={() => setLessons(move(ch.lessons, li, li - 1))}>
                            <ArrowUp className="size-4" aria-hidden="true" />
                          </IconButton>
                          <IconButton label={`Move lesson ${li + 1} down`} disabled={li === ch.lessons.length - 1} onClick={() => setLessons(move(ch.lessons, li, li + 1))}>
                            <ArrowDown className="size-4" aria-hidden="true" />
                          </IconButton>
                          <IconButton danger label={`Delete lesson ${li + 1}`} onClick={() => setLessons(ch.lessons.filter((_, j) => j !== li))}>
                            <Trash2 className="size-4" aria-hidden="true" />
                          </IconButton>
                        </>
                      )}
                    </div>
                    {open && (
                      <div className="py-3 pl-10">
                        <LessonEditor lesson={lesson} idPrefix={`ls-${key}`} onChange={(l) => setLessons(ch.lessons.map((x, j) => (j === li ? l : x)))} />
                      </div>
                    )}
                  </li>
                )
              })}
            </ol>

            <div className="space-y-3 border-t border-border p-3 sm:p-4">
              {!readOnly && (
                <BookeyButton type="button" variant="ghost" size="sm" onClick={() => updateChapter(ci, { lessons: [...ch.lessons, blankLesson()] })}>
                  <Plus aria-hidden="true" /> Add lesson
                </BookeyButton>
              )}
              <div className="rounded-xl bg-muted/60 p-3">
                <p className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                  <Hammer aria-hidden="true" className="size-3.5" /> Chapter project
                </p>
                <div className="mt-2 grid gap-2 sm:grid-cols-[1fr_1.5fr]">
                  <label className="sr-only" htmlFor={`pj-${ci}-t`}>
                    Project title
                  </label>
                  <input id={`pj-${ci}-t`} value={ch.project.title} readOnly={readOnly} placeholder="Project title" onChange={(e) => updateChapter(ci, { project: { ...ch.project, title: e.target.value } })} className={inputClass} />
                  <label className="sr-only" htmlFor={`pj-${ci}-b`}>
                    Project brief
                  </label>
                  <input id={`pj-${ci}-b`} value={ch.project.brief} readOnly={readOnly} placeholder="Brief" onChange={(e) => updateChapter(ci, { project: { ...ch.project, brief: e.target.value } })} className={inputClass} />
                </div>
              </div>
            </div>
          </li>
        ))}
      </ol>
      {!readOnly && (
        <BookeyButton type="button" variant="outline" onClick={() => setChapters([...chapters, blankChapter()])}>
          <Plus aria-hidden="true" /> Add chapter
        </BookeyButton>
      )}
    </div>
  )
}
