'use client'

import {
  ArrowRight,
  AudioLines,
  CheckCircle2,
  Command,
  MessageCircleQuestion,
  Mic,
  Pause,
  Play,
  RotateCcw,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
} from 'lucide-react'
import { useState } from 'react'
import { Hint } from '@/components/ui-kit/hint'
import { useReaderPreferences } from '@/lib/stores/reader-preferences'
import { cn } from '@/lib/utils'
import { COMMAND_EXAMPLES, type VoiceIntent } from '@/lib/voice-commands'
import { NARRATION_RATES, type NarrationStatus, type VoiceDockProps } from '@/types/book'
import type { HandsFreeState } from './tutor/use-hands-free'
import { AnyQuestionsPanel } from './tutor/any-questions-panel'
import { MicDemoPanel } from './voice-panels'
import { VoiceWaveform } from './voice-waveform'
import { TutorOrb, TUTOR_NAME, type TutorPresenceState } from './tutor/tutor-orb'

export type VoiceDockFullProps = VoiceDockProps & {
  /** Barge-in: interrupt the narration and ask by voice. When set, replaces the demo mic. */
  onBargeIn?: () => void
  /** Interrupt and type a question in the in-book tutor note. */
  onAskInline?: () => void
  presence?: TutorPresenceState
  /** Hands-free listening for spoken commands (default off). */
  handsFree?: { on: boolean; state: HandsFreeState; onToggle: () => void }
  /** Visible equivalents of every voice command. */
  onCommand?: (intent: VoiceIntent) => void
}

const handsFreeLabel: Record<HandsFreeState, string> = {
  off: 'Hands-free off',
  starting: 'Starting the microphone…',
  listening: 'Hands-free: listening for commands',
  hearing: 'Hands-free: hearing you…',
  transcribing: 'Hands-free: understanding…',
  unsupported: 'Hands-free is not supported in this browser',
  error: 'Hands-free: microphone unavailable',
}

const statusLabel: Record<NarrationStatus, string> = {
  idle: 'Ready',
  speaking: 'Speaking',
  listening: 'Listening',
  thinking: 'Thinking',
  paused: 'Paused',
  completed: 'Page complete',
}

const iconButton =
  'grid size-8 shrink-0 place-items-center rounded-full text-foreground/80 transition-colors hover:bg-white/10 hover:text-foreground focus-visible:ring-2 focus-visible:ring-book-orange focus-visible:outline-none disabled:pointer-events-none disabled:opacity-30 sm:size-9 aria-pressed:bg-book-orange/15 aria-pressed:text-book-marker aria-expanded:bg-book-orange/15 aria-expanded:text-book-marker'

/**
 * Narration dock: tutor presence, transport, speed, mute and the two ways to
 * interrupt (hold-to-talk voice or a typed question). With audio, words follow
 * the voice; without it the dock says it's a timed simulation.
 */
export function VoiceDock({
  page,
  streamingEnabled,
  stream,
  rate,
  onRateChange,
  muted,
  onMutedChange,
  onEnableGuided,
  hasNextPage,
  onNextPage,
  queue,
  onQueueQuestion,
  tutorBusy,
  onAskTutor,
  onAskQueued,
  onOpenChat,
  onBargeIn,
  onAskInline,
  presence,
  handsFree,
  onCommand,
}: VoiceDockFullProps) {
  const [questionsDismissed, setQuestionsDismissed] = useState(false)
  const [commandsOpen, setCommandsOpen] = useState(false)
  const { status } = stream
  const speaking = status === 'speaking'
  const completed = status === 'completed'
  const nextRate = NARRATION_RATES[(NARRATION_RATES.indexOf(rate) + 1) % NARRATION_RATES.length]
  const voiceGender = useReaderPreferences((s) => s.voiceGender)
  const setVoiceGender = useReaderPreferences((s) => s.setVoiceGender)
  const showQuestions = streamingEnabled && completed && !questionsDismissed

  function handlePlay() {
    if (!streamingEnabled) {
      onEnableGuided()
      stream.play()
      return
    }
    if (completed) {
      setQuestionsDismissed(false)
      stream.restart()
    } else stream.toggle()
  }

  function toggleMic() {
    if (onBargeIn) {
      onBargeIn()
      return
    }
    if (speaking) {
      onQueueQuestion('mic')
      return
    }
    if (status === 'listening') stream.stopListening()
    else stream.startListening()
  }

  function handleAsk() {
    if (onAskInline) {
      onAskInline()
      return
    }
    if (speaking) {
      onQueueQuestion('ask')
      return
    }
    if (status === 'listening') stream.stopListening()
    onOpenChat()
  }

  function askFromPanel(question: string) {
    const sent = onAskTutor(question)
    if (sent) onOpenChat()
    return sent
  }

  const playLabel = !streamingEnabled
    ? 'Narrate this page in guided mode'
    : completed
      ? 'Replay narration'
      : speaking
        ? 'Pause narration'
        : status === 'paused'
          ? 'Resume narration'
          : 'Play narration'

  const caption = !streamingEnabled
    ? 'Full page view. Press play to switch to guided narration.'
    : status === 'listening'
      ? 'Listening…'
      : status === 'thinking'
        ? 'Thinking about your question…'
        : completed
          ? null
          : stream.buffering
            ? `${TUTOR_NAME} is getting ready to speak…`
            : (stream.currentSentence?.text ?? `Press play and ${TUTOR_NAME} will teach this page. Interrupt any time.`)

  const sentenceNumber = stream.currentSentence ? stream.currentSentence.index + 1 : null

  return (
    <div role="region" aria-label="Voice narration" className="flex w-full flex-col items-center gap-2">
      {status === 'listening' && <MicDemoPanel onClose={stream.stopListening} />}
      {commandsOpen && onCommand && (
        <div
          id="lesson-commands"
          role="group"
          aria-label="Lesson commands"
          className="flex w-full max-w-2xl flex-col gap-1.5 rounded-2xl border border-book-amber/25 bg-black/30 px-3 py-2 backdrop-blur-xl animate-in fade-in slide-in-from-bottom-1"
        >
          <p className="text-[11px] text-muted-foreground">
            Tap, type it in the question box, or say it (English or Tanglish){handsFree?.on ? ' — hands-free is listening.' : ' — tap the mic first, or turn on hands-free.'}
          </p>
          <ul className="flex flex-wrap gap-1.5">
            {COMMAND_EXAMPLES.map((item) => (
              <li key={item.intent}>
                <button
                  type="button"
                  onClick={() => onCommand(item.intent)}
                  title={`Say ${item.say}`}
                  aria-label={`${item.label}. You can also say ${item.say.replace(/[“”]/g, '')}`}
                  disabled={
                    (item.intent === 'next_page' && !hasNextPage) ||
                    (item.intent === 'simpler' && tutorBusy)
                  }
                  className="inline-flex h-7 items-center rounded-full border border-white/10 bg-white/5 px-2.5 text-xs font-medium transition-colors hover:bg-white/10 focus-visible:ring-2 focus-visible:ring-book-orange focus-visible:outline-none disabled:opacity-35"
                >
                  {item.label}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {showQuestions && (
        <AnyQuestionsPanel
          queue={queue}
          busy={tutorBusy}
          hasNextPage={hasNextPage}
          onAsk={askFromPanel}
          onAskQueued={(item) => {
            onAskQueued(item)
            onOpenChat()
          }}
          onNextPage={onNextPage}
          onClose={() => setQuestionsDismissed(true)}
        />
      )}
      {!completed && queue.length > 0 && (
        <ul aria-label="Queued questions" className="flex max-w-2xl flex-wrap justify-center gap-1.5">
          {queue.map((item, index) => (
            <li
              key={item.id}
              className="inline-flex h-6 max-w-56 items-center gap-1 rounded-full border border-book-amber/30 bg-book-orange/10 px-2 text-[11px] text-foreground/85 animate-in fade-in zoom-in-95"
            >
              {item.kind === 'mic' ? (
                <Mic className="size-3 shrink-0 text-book-marker" aria-hidden="true" />
              ) : (
                <MessageCircleQuestion className="size-3 shrink-0 text-book-marker" aria-hidden="true" />
              )}
              <span className="truncate">
                Q{index + 1} · {item.excerpt}
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="flex w-full max-w-2xl flex-col items-center gap-1 px-1 text-center">
        <p className="flex items-center gap-1.5 font-mono text-[10px] tracking-[0.14em] text-muted-foreground uppercase">
          <span
            aria-hidden="true"
            className={cn('size-1.5 rounded-full bg-book-amber/60', speaking && 'bg-book-orange motion-safe:animate-pulse')}
          />
          <span>{stream.player.producesSound ? `${TUTOR_NAME} · voice` : 'Narration (timed text)'}</span>
          <span aria-hidden="true">·</span>
          <span>{streamingEnabled ? statusLabel[status] : 'Off in full view'}</span>
          {sentenceNumber && !completed && (
            <span className="hidden sm:inline">
              · Sentence {sentenceNumber}/{stream.sentences.length}
            </span>
          )}
        </p>
        {completed ? (
          <div className="flex flex-wrap items-center justify-center gap-2 text-sm">
            <span className="inline-flex items-center gap-1.5 text-foreground/90">
              <CheckCircle2 className="size-4 text-book-marker" aria-hidden="true" />
              Page narrated
            </span>
            {hasNextPage && (
              <button
                type="button"
                onClick={onNextPage}
                className="inline-flex h-8 items-center gap-1 rounded-full bg-book-orange px-3 text-sm font-semibold text-book-ink shadow-[0_0_24px_-4px_rgb(249_115_22/0.7)] transition-colors hover:bg-book-marker focus-visible:ring-2 focus-visible:ring-book-marker focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none"
              >
                Next page
                <ArrowRight className="size-4" aria-hidden="true" />
              </button>
            )}
          </div>
        ) : (
          <p lang={stream.currentSentence ? 'ta-Latn' : undefined} className="line-clamp-2 min-h-5 text-sm text-pretty text-foreground/85 sm:line-clamp-1">
            {caption}
          </p>
        )}
      </div>

      <div
        className={cn(
          'flex w-full max-w-2xl items-center gap-0.5 rounded-full border border-book-amber/20 bg-[linear-gradient(135deg,rgb(255_237_213/0.10),rgb(255_255_255/0.03))] py-1 pr-1.5 pl-1 backdrop-blur-xl transition-shadow duration-500 sm:gap-1 sm:pr-2',
          speaking
            ? 'shadow-[0_0_0_1px_rgb(249_115_22/0.35),0_12px_44px_-10px_rgb(249_115_22/0.65),inset_0_1px_0_rgb(255_255_255/0.08)]'
            : 'shadow-[0_10px_36px_-14px_rgb(249_115_22/0.35),inset_0_1px_0_rgb(255_255_255/0.06)]',
        )}
      >
        {presence && <TutorOrb state={presence} getLevel={stream.getLevel} size={38} className="mr-0.5 hidden sm:flex" />}
        <Hint label={playLabel}>
          <button
            type="button"
            onClick={handlePlay}
            aria-label={playLabel}
            className="grid size-9 shrink-0 place-items-center rounded-full bg-book-orange text-book-ink shadow-[0_0_20px_-4px_rgb(249_115_22/0.8)] transition-colors hover:bg-book-marker focus-visible:ring-2 focus-visible:ring-book-marker focus-visible:ring-offset-2 focus-visible:ring-offset-background focus-visible:outline-none sm:size-10"
          >
            {completed ? (
              <RotateCcw className="size-4" aria-hidden="true" />
            ) : speaking ? (
              <Pause className="size-4 fill-current" aria-hidden="true" />
            ) : (
              <Play className="size-4 translate-x-px fill-current" aria-hidden="true" />
            )}
          </button>
        </Hint>

        <Hint label="Previous sentence">
          <button
            type="button"
            onClick={stream.previousSentence}
            disabled={!streamingEnabled || !stream.canSeek}
            aria-label="Previous sentence"
            className={iconButton}
          >
            <SkipBack className="size-4" aria-hidden="true" />
          </button>
        </Hint>
        <Hint label="Next sentence">
          <button
            type="button"
            onClick={stream.nextSentence}
            disabled={!streamingEnabled || !stream.canSeek || completed}
            aria-label="Next sentence"
            className={iconButton}
          >
            <SkipForward className="size-4" aria-hidden="true" />
          </button>
        </Hint>

        <div className="flex min-w-0 flex-1 justify-center overflow-hidden px-1">
          <VoiceWaveform status={streamingEnabled ? status : 'idle'} seed={stream.wordIndex} />
        </div>

        <Hint label={`Tutor voice: ${voiceGender} — switch to ${voiceGender === 'male' ? 'female' : 'male'}`}>
          <button
            type="button"
            onClick={() => setVoiceGender(voiceGender === 'male' ? 'female' : 'male')}
            aria-label={`Tutor voice ${voiceGender}. Switch to ${voiceGender === 'male' ? 'female' : 'male'}`}
            className={cn(iconButton, 'w-auto min-w-8 px-1.5 text-xs font-semibold sm:min-w-10')}
          >
            {voiceGender === 'male' ? 'Male' : 'Female'}
          </button>
        </Hint>
        <Hint label={`Speed ${rate}× — change to ${nextRate}×`}>
          <button
            type="button"
            onClick={() => onRateChange(nextRate)}
            aria-label={`Narration speed ${rate} times. Change to ${nextRate} times`}
            className={cn(iconButton, 'w-auto min-w-8 px-1.5 font-mono text-xs font-semibold tabular-nums sm:min-w-10')}
          >
            {rate}×
          </button>
        </Hint>
        <Hint
          label={
            muted
              ? stream.player.producesSound
                ? 'Unmute narration'
                : 'Unmute'
              : stream.player.producesSound
                ? 'Mute narration'
                : 'Mute'
          }
        >
          <button
            type="button"
            onClick={() => onMutedChange(!muted)}
            aria-label="Mute narration"
            aria-pressed={muted}
            className={iconButton}
          >
            {muted ? <VolumeX className="size-4" aria-hidden="true" /> : <Volume2 className="size-4" aria-hidden="true" />}
          </button>
        </Hint>
        {handsFree && (
          <Hint label={handsFree.on ? `${handsFreeLabel[handsFree.state]} — turn off` : 'Hands-free: listen for spoken commands while she teaches'}>
            <button
              type="button"
              onClick={handsFree.onToggle}
              aria-pressed={handsFree.on}
              aria-label="Hands-free voice commands"
              className={cn(iconButton, 'relative')}
            >
              <AudioLines className="size-4" aria-hidden="true" />
              {handsFree.on && (
                <span
                  aria-hidden="true"
                  className={cn(
                    'absolute top-1 right-1 size-1.5 rounded-full bg-emerald-400',
                    handsFree.state === 'hearing' && 'bg-book-orange motion-safe:animate-ping',
                    (handsFree.state === 'error' || handsFree.state === 'unsupported') && 'bg-red-400',
                  )}
                />
              )}
            </button>
          </Hint>
        )}
        {onCommand && (
          <Hint label="Lesson commands">
            <button
              type="button"
              onClick={() => setCommandsOpen((open) => !open)}
              aria-expanded={commandsOpen}
              aria-controls="lesson-commands"
              aria-label="Lesson commands"
              className={iconButton}
            >
              <Command className="size-4" aria-hidden="true" />
            </button>
          </Hint>
        )}
        <Hint label={onBargeIn ? 'Interrupt and ask by voice' : speaking ? 'Queue a voice question' : 'Voice input'}>
          <button
            type="button"
            onClick={toggleMic}
            aria-label={onBargeIn ? `Interrupt ${TUTOR_NAME} and ask by voice` : speaking ? 'Queue a voice question for the end of this section' : 'Voice input'}
            aria-expanded={status === 'listening'}
            className={iconButton}
          >
            <Mic className="size-4" aria-hidden="true" />
          </button>
        </Hint>
        <Hint label={onAskInline ? 'Interrupt and type a question' : speaking ? 'Queue a question for later' : 'Ask a question'}>
          <button
            type="button"
            onClick={handleAsk}
            aria-label={onAskInline ? `Interrupt ${TUTOR_NAME} and type a question` : speaking ? 'Queue a question for the end of this section' : 'Ask a question in course chat'}
            className={iconButton}
          >
            <MessageCircleQuestion className="size-4" aria-hidden="true" />
          </button>
        </Hint>
      </div>

      <p className="sr-only" aria-live="polite">
        {streamingEnabled ? `Narration ${statusLabel[status].toLowerCase()}` : ''}
      </p>
      {handsFree && (
        <p className="sr-only" aria-live="polite">
          {!handsFree.on
            ? 'Hands-free off'
            : handsFree.state === 'error' || handsFree.state === 'unsupported'
              ? handsFreeLabel[handsFree.state]
              : 'Hands-free on, listening for commands'}
        </p>
      )}
    </div>
  )
}
