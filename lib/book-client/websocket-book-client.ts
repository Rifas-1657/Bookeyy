/**
 * WebSocket-backed BookClient — B1 version.
 *
 * Selected by NEXT_PUBLIC_BOOKEY_CLIENT=ws when NEXT_PUBLIC_BOOKEY_WS_URL is set.
 * Falls back gracefully to the mock client when the URL is absent.
 *
 * Features:
 * - WS ticket auth (B1): fetches a short-lived ticket via POST /api/ws-ticket and
 *   sends it as the FIRST frame { v:2, type:'auth_ticket', payload:{ticket} }.
 *   The server replies with auth_ok or auth_error (connection-level, not in protocol union).
 * - Frame validation via lib/contracts/validate.ts (invalid frames become recoverable error events)
 * - Exponential backoff + jitter on disconnect (max 30 s)
 * - Heartbeat ping/pong (30 s interval, 10 s timeout)
 * - Reconnect: the server can't resume a narration session, so after a
 *   reconnect the client emits `onReconnect` and the narration hook re-requests
 *   `start_page` and seeks to the current word. Stale transport frames
 *   (pause/seek/...) queued while offline are dropped.
 * - Close 4001 (auth rejected) or repeated 4003 (no ticket) stop reconnecting.
 * - Connection status exposed via `connectionStatus` property
 *
 * Protocol: version 2 (BOOK_PROTOCOL_VERSION)
 * Browsers cannot set Authorization headers on WebSocket. Do NOT put tokens in the URL.
 */

import type { BookClientKind } from '@/lib/contracts/book-client'
import type { BookClientEvent, ClientEventPayloads } from '@/lib/contracts/book-events'
import { BOOK_PROTOCOL_VERSION } from '@/lib/contracts/book-events'
import { BaseBookClient } from './base-client'
import { MockBookClient } from './mock-book-client'

export type ConnectionStatus = 'connecting' | 'authenticating' | 'connected' | 'disconnected' | 'reconnecting'

/** Async function that returns a single-use WS ticket string. */
export type TicketFetcher = () => Promise<string>

const AUTH_TIMEOUT_MS = 5_000
const HEARTBEAT_INTERVAL_MS = 30_000
const HEARTBEAT_TIMEOUT_MS = 10_000
const BASE_BACKOFF_MS = 500
const MAX_BACKOFF_MS = 30_000
const MAX_RECONNECT_ATTEMPTS = 10
/** Frames queued while the socket is down; the oldest are dropped beyond this. */
const MAX_PENDING_FRAMES = 50
/** Consecutive "ticket fetch failed" closes before giving up. */
const MAX_TICKET_FAILURES = 3
const CLOSE_AUTH_REJECTED = 4001
const CLOSE_TICKET_FAILED = 4003
/** Playback-control frames that are meaningless after a reconnect (the hook re-requests the page). */
const STALE_AFTER_RECONNECT = new Set<string>(['start_page', 'pause', 'resume', 'seek', 'speed', 'audio_in'])

/* Pure helpers, exported for tests. */

export function trimPendingFrames<T>(frames: T[], max = MAX_PENDING_FRAMES): T[] {
  return frames.length > max ? frames.slice(frames.length - max) : frames
}

export function dropStaleFrames<T extends { type: string }>(frames: T[]): T[] {
  return frames.filter((frame) => !STALE_AFTER_RECONNECT.has(frame.type))
}

/** Parses a text frame; returns null for binary or non-JSON frames. */
export function parseFrame(data: unknown): { type?: unknown; sessionId?: unknown; payload?: Record<string, unknown> } | null {
  if (typeof data !== 'string') return null
  try {
    const parsed = JSON.parse(data)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

function jitter(ms: number) {
  return ms * (0.8 + Math.random() * 0.4)
}

function backoffDelay(attempt: number) {
  return Math.min(jitter(BASE_BACKOFF_MS * 2 ** attempt), MAX_BACKOFF_MS)
}

export class WebSocketBookClient extends BaseBookClient {
  readonly kind: BookClientKind = 'websocket'

  private url: string
  /** Optional ticket fetcher — when set, auth_ticket is sent as first frame. */
  private ticketFetcher: TicketFetcher | null
  private ws: WebSocket | null = null
  private authenticated = false
  private authTimeoutTimer: ReturnType<typeof setTimeout> | null = null
  private reconnectAttempts = 0
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null
  private heartbeatTimeoutTimer: ReturnType<typeof setTimeout> | null = null
  private disposed = false

  /** Frames queued before auth completes. */
  private pendingFrames: BookClientEvent[] = []

  /** Recorded from `session_started` (diagnostics; the server cannot resume a session). */
  private lastSessionId: string | null = null
  /** True once a connection has been authenticated; later ones are reconnects. */
  private everConnected = false
  private ticketFailures = 0
  /** Close code we initiated (the server may echo a different one). */
  private localCloseCode: number | null = null
  private reconnectListeners = new Set<() => void>()

  private _status: ConnectionStatus = 'connecting'
  private statusListeners = new Set<(status: ConnectionStatus) => void>()

  /**
   * @param url       WebSocket URL, e.g. ws://localhost:8000/ws/book
   * @param options.ticketFetcher  When provided, calls this to get a ticket and
   *                               sends auth_ticket as the first frame (B1 auth mode).
   *                               When absent, connects without auth (B0 / mock mode).
   */
  /**
   * Practicals run in the learner's browser (real Python via Pyodide, JS/HTML sandboxed),
   * exactly like the offline client. The server's runner only simulates seeded examples.
   */
  private localRunner = new MockBookClient()

  constructor(url: string, options?: { ticketFetcher?: TicketFetcher }) {
    super()
    this.url = url
    this.ticketFetcher = options?.ticketFetcher ?? null
    this.localRunner.on('run_result', (event) => this.deliver(event))
    this.connect()
  }

  override runCode(payload: Omit<ClientEventPayloads['run_code'], 'codeId'>) {
    return this.localRunner.runCode(payload)
  }

  /** Fires after the socket reconnects and authenticates (not on the first connect). */
  onReconnect(listener: () => void): () => void {
    this.reconnectListeners.add(listener)
    return () => this.reconnectListeners.delete(listener)
  }

  get sessionId(): string | null {
    return this.lastSessionId
  }

  get connectionStatus(): ConnectionStatus {
    return this._status
  }

  onStatusChange(listener: (status: ConnectionStatus) => void): () => void {
    this.statusListeners.add(listener)
    return () => this.statusListeners.delete(listener)
  }

  private setStatus(status: ConnectionStatus) {
    this._status = status
    for (const listener of this.statusListeners) {
      try { listener(status) } catch {}
    }
  }

  private connect() {
    if (this.disposed) return
    this.localCloseCode = null
    try {
      this.ws = new WebSocket(this.url)
      this.ws.onopen = this.handleOpen
      this.ws.onmessage = this.handleMessage
      this.ws.onerror = this.handleError
      this.ws.onclose = this.handleClose
    } catch (err) {
      console.error('[ws-book-client] failed to open WebSocket:', err)
      this.scheduleReconnect()
    }
  }

  private closeWith(code: number, reason: string) {
    this.localCloseCode = code
    this.ws?.close(code, reason)
  }

  /** Auth succeeded (or isn't required): flush queued frames, announce reconnects. */
  private onAuthenticated() {
    this.authenticated = true
    this.reconnectAttempts = 0
    this.ticketFailures = 0
    this.setStatus('connected')
    this.startHeartbeat()
    const reconnect = this.everConnected
    this.everConnected = true
    if (reconnect) this.pendingFrames = dropStaleFrames(this.pendingFrames)
    this._flushPending()
    if (reconnect) {
      for (const listener of [...this.reconnectListeners]) {
        try { listener() } catch {}
      }
    }
  }

  private handleOpen = async () => {
    this.authenticated = false

    if (this.ticketFetcher) {
      this.setStatus('authenticating')
      // 5-second auth timeout: close the socket if auth_ok is not received
      this.authTimeoutTimer = setTimeout(() => {
        if (!this.authenticated) {
          if (process.env.NODE_ENV === 'development') {
            console.warn('[ws-book-client] auth timeout — closing socket')
          }
          this.closeWith(4002, 'auth timeout')
        }
      }, AUTH_TIMEOUT_MS)

      // Fetch ticket and send auth_ticket as the very first frame
      try {
        const ticket = await this.ticketFetcher()
        this._rawSend({ v: BOOK_PROTOCOL_VERSION, type: 'auth_ticket', payload: { ticket } } as BookClientEvent)
      } catch (err) {
        console.error('[ws-book-client] failed to fetch WS ticket:', err)
        this.closeWith(CLOSE_TICKET_FAILED, 'ticket fetch failed')
      }
    } else {
      // No auth (B0 / mock mode) — mark authenticated immediately
      this.onAuthenticated()
    }
  }

  private handleMessage = (event: MessageEvent) => {
    this.resetHeartbeatTimeout()

    const raw = event.data
    // Binary or non-JSON frames are not part of the protocol.
    const parsed = parseFrame(raw)
    if (!parsed) return

    // Pong: connection-level, skip
    if (parsed.type === 'pong') return

    // Auth frames: connection-level, handle before deliver()
    if (parsed.type === 'auth_ok') {
      if (this.authTimeoutTimer !== null) { clearTimeout(this.authTimeoutTimer); this.authTimeoutTimer = null }
      this.onAuthenticated()
      return
    }
    if (parsed.type === 'auth_error') {
      if (process.env.NODE_ENV === 'development') {
        console.error('[ws-book-client] auth rejected:', parsed.payload?.message)
      }
      this.closeWith(CLOSE_AUTH_REJECTED, 'auth rejected')
      return
    }
    if (parsed.type === 'session_started') {
      const sessionId = parsed.sessionId ?? parsed.payload?.sessionId
      if (typeof sessionId === 'string') this.lastSessionId = sessionId
    }

    // Normal frames
    try {
      this.deliver(raw)
    } catch (err) {
      if (process.env.NODE_ENV === 'development') {
        console.warn('[ws-book-client] failed to deliver frame:', err)
      }
    }
  }

  private handleError = (event: Event) => {
    if (process.env.NODE_ENV === 'development') {
      console.warn('[ws-book-client] WebSocket error:', event)
    }
  }

  private handleClose = (event: CloseEvent) => {
    if (this.authTimeoutTimer !== null) { clearTimeout(this.authTimeoutTimer); this.authTimeoutTimer = null }
    this.stopHeartbeat()
    if (this.disposed) return
    this.authenticated = false
    const code = this.localCloseCode ?? event?.code
    this.localCloseCode = null
    if (code === CLOSE_AUTH_REJECTED) {
      // Retrying would be rejected again; the learner has to sign in.
      this.setStatus('disconnected')
      return
    }
    if (code === CLOSE_TICKET_FAILED) {
      this.ticketFailures += 1
      if (this.ticketFailures >= MAX_TICKET_FAILURES) {
        this.setStatus('disconnected')
        return
      }
    }
    this.setStatus('reconnecting')
    this.scheduleReconnect()
  }

  private scheduleReconnect() {
    if (this.disposed || this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      this.setStatus('disconnected')
      return
    }
    const delay = backoffDelay(this.reconnectAttempts)
    this.reconnectAttempts += 1
    this.reconnectTimer = setTimeout(() => {
      if (!this.disposed) {
        this.setStatus('reconnecting')
        this.connect()
      }
    }, delay)
  }

  private startHeartbeat() {
    this.stopHeartbeat()
    this.heartbeatTimer = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }))
        this.heartbeatTimeoutTimer = setTimeout(() => {
          this.closeWith(4000, 'heartbeat timeout')
        }, HEARTBEAT_TIMEOUT_MS)
      }
    }, HEARTBEAT_INTERVAL_MS)
  }

  private stopHeartbeat() {
    if (this.heartbeatTimer !== null) clearInterval(this.heartbeatTimer)
    if (this.heartbeatTimeoutTimer !== null) clearTimeout(this.heartbeatTimeoutTimer)
    this.heartbeatTimer = null
    this.heartbeatTimeoutTimer = null
  }

  private resetHeartbeatTimeout() {
    if (this.heartbeatTimeoutTimer !== null) {
      clearTimeout(this.heartbeatTimeoutTimer)
      this.heartbeatTimeoutTimer = null
    }
  }

  private _rawSend(event: BookClientEvent | object) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify(event))
      } catch (err) {
        if (process.env.NODE_ENV === 'development') {
          console.warn('[ws-book-client] send failed:', err)
        }
      }
    }
  }

  private _flushPending() {
    for (const frame of this.pendingFrames) {
      this._rawSend(frame)
    }
    this.pendingFrames = []
  }

  send(event: BookClientEvent): void {
    if (event.type === 'start_page') {
      this.lastSessionId = null
    }
    if (!this.authenticated) {
      // Queue until auth completes (bounded: a long outage must not grow it forever)
      this.pendingFrames = trimPendingFrames([...this.pendingFrames, event])
      return
    }
    this._rawSend(event)
  }

  /** Override to send the audio_in frame over the WebSocket. */
  override audioIn(payload: ClientEventPayloads['audio_in']): void {
    if (process.env.NEXT_PUBLIC_BOOKEY_MIC !== 'on') return
    this.send({ v: BOOK_PROTOCOL_VERSION, type: 'audio_in', payload } as BookClientEvent)
  }

  override dispose() {
    this.disposed = true
    if (this.authTimeoutTimer !== null) clearTimeout(this.authTimeoutTimer)
    this.stopHeartbeat()
    if (this.reconnectTimer !== null) clearTimeout(this.reconnectTimer)
    this.ws?.close(1000, 'disposed')
    this.ws = null
    this.pendingFrames = []
    this.localRunner.dispose()
    this.setStatus('disconnected')
    super.dispose()
  }
}

/** Reads NEXT_PUBLIC_BOOKEY_WS_URL from env. Returns null when not set. */
export function getWsUrl(): string | null {
  return process.env.NEXT_PUBLIC_BOOKEY_WS_URL ?? null
}

/** True when the WebSocket client should be used. */
export function shouldUseWsClient(): boolean {
  return (
    process.env.NEXT_PUBLIC_BOOKEY_CLIENT === 'ws' && getWsUrl() !== null
  )
}
