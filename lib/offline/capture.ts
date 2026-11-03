import { notifyOfflineChanged, putCapture, type CaptureKind } from './db'

interface CaptureInput {
  courseId: string
  pageId: string
  chapterNumber: number
  kind: CaptureKind
  title: string
}

/**
 * Screenshots a DOM node (diagram canvas, code output) into a PNG blob and
 * stores it in IndexedDB for the lesson recap. Never throws: a failed capture
 * just means the recap shows the text version.
 */
export async function captureElement(node: HTMLElement | null, input: CaptureInput): Promise<boolean> {
  if (!node || typeof window === 'undefined') return false
  try {
    const { toBlob } = await import('html-to-image')
    const background = getComputedStyle(node).backgroundColor
    const blob = await toBlob(node, {
      pixelRatio: Math.min(2, window.devicePixelRatio || 1),
      cacheBust: true,
      backgroundColor: background && background !== 'rgba(0, 0, 0, 0)' ? background : undefined,
      // Skip interactive chrome (toolbars, buttons) so the picture reads like a book figure.
      filter: (el) => !(el instanceof HTMLElement && el.dataset.captureIgnore !== undefined),
    })
    if (!blob) return false
    await putCapture({ ...input, blob })
    notifyOfflineChanged(input.courseId)
    return true
  } catch (error) {
    if (process.env.NODE_ENV === 'development') console.warn('[capture] failed', error)
    return false
  }
}
