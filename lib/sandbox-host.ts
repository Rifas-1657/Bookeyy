/**
 * Runs untrusted code (learner Python/JS) in a Web Worker that lives inside a
 * hidden `sandbox="allow-scripts"` iframe. The iframe has an opaque origin, so
 * nothing it runs can read the app's cookies, storage, IndexedDB or DOM, and
 * same-origin requests from it are cross-site. Killing the host removes the
 * iframe, which also terminates its worker (runaway loops never freeze the page).
 *
 * Kept free of imports so `node --test` can load it directly.
 */

/**
 * Makes a string safe to embed inside an inline <script> as a JS string literal.
 * U+2028/U+2029 are legal in string literals since ES2019, so `<` (as in
 * `</script>`) is the only hazard.
 */
export function scriptLiteral(text: string): string {
  return JSON.stringify(text).replace(/</g, '\\u003c')
}

/**
 * The iframe document: starts the worker from `workerSource` and relays
 * messages both ways. Only messages from `parent` are forwarded to the worker;
 * replies go to `parentOrigin` only, tagged with `token`.
 */
export function buildSandboxSrcdoc(workerSource: string, token: string, parentOrigin: string): string {
  // An opaque-origin parent ("null") cannot be targeted by origin; fall back to '*'.
  const target = parentOrigin && parentOrigin !== 'null' ? parentOrigin : '*'
  return `<!doctype html><meta charset="utf-8"><script>
(function(){
var T=${scriptLiteral(token)},O=${scriptLiteral(target)};
var send=function(m){m.__bk=T;parent.postMessage(m,O)};
var w;
try{w=new Worker(URL.createObjectURL(new Blob([${scriptLiteral(workerSource)}],{type:'text/javascript'})))}
catch(e){send({hostError:String((e&&e.message)||e)});return}
w.onmessage=function(e){send({data:e.data})};
w.onerror=function(e){if(e&&e.preventDefault)e.preventDefault();send({hostError:String((e&&e.message)||'worker error')})};
addEventListener('message',function(e){
  if(e.source!==parent)return;
  var d=e.data;var t=d&&d.buffer instanceof ArrayBuffer?[d.buffer]:[];
  w.postMessage(d,t);
});
send({ready:true});
})();
</script>`
}

export interface SandboxHost {
  post(message: Record<string, unknown>, transfer?: Transferable[]): void
  kill(): void
}

/**
 * Creates a sandboxed worker. `onMessage` gets the worker's messages;
 * `onFailure` fires once if the worker can't start or crashes.
 * Returns null outside the browser.
 */
export function createSandboxHost(
  workerSource: string,
  onMessage: (data: unknown) => void,
  onFailure: (message: string) => void,
): SandboxHost | null {
  if (typeof document === 'undefined' || typeof window === 'undefined') return null
  const token = `bk${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`
  const frame = document.createElement('iframe')
  frame.setAttribute('sandbox', 'allow-scripts')
  frame.setAttribute('aria-hidden', 'true')
  frame.setAttribute('tabindex', '-1')
  frame.style.cssText = 'position:fixed;width:0;height:0;border:0;opacity:0;pointer-events:none'
  let ready = false
  let dead = false
  const queue: [Record<string, unknown>, Transferable[]][] = []

  const onWindowMessage = (event: MessageEvent) => {
    if (dead || event.source !== frame.contentWindow) return
    const data = event.data as { __bk?: string; ready?: boolean; data?: unknown; hostError?: string } | null
    if (!data || data.__bk !== token) return
    if (data.ready) {
      ready = true
      for (const [message, transfer] of queue.splice(0)) frame.contentWindow?.postMessage(message, '*', transfer)
      return
    }
    if (data.hostError !== undefined) {
      onFailure(data.hostError)
      return
    }
    onMessage(data.data)
  }

  window.addEventListener('message', onWindowMessage)
  frame.srcdoc = buildSandboxSrcdoc(workerSource, token, window.location.origin)
  document.body.appendChild(frame)

  return {
    post(message, transfer = []) {
      if (dead) return
      // The frame's origin is opaque, so '*' is the only usable target; the
      // frame itself only accepts messages whose source is this window.
      if (ready) frame.contentWindow?.postMessage(message, '*', transfer)
      else queue.push([message, transfer])
    },
    kill() {
      if (dead) return
      dead = true
      queue.length = 0
      window.removeEventListener('message', onWindowMessage)
      frame.remove()
    },
  }
}
