/**
 * Code runners for the book's practicals.
 *
 * 1. `runPython`: REAL Python via Pyodide (WebAssembly) in a Web Worker inside
 *    a sandboxed opaque-origin iframe (see sandbox-host.ts), runtime files
 *    fetched by the page from the jsDelivr CDN, with a hard timeout. Produces a
 *    line-by-line trace (output + variables) using `sys.settrace`.
 * 2. `runJavaScriptSandboxed`: JS practicals in the same sandboxed worker host
 *    (DOM-using code: a sandboxed iframe), console captured.
 * 3. `runPythonDemo`: the original deterministic simulator. It never executes
 *    code; it matches the seeded examples and replays hand-written traces.
 *    Kept as the offline/no-runtime fallback and the instant narrated path.
 */

import { createSandboxHost, type SandboxHost } from './sandbox-host'

export const UNSUPPORTED_MESSAGE = 'This frontend demo only simulates the seeded examples.'

export interface DemoVariable {
  name: string
  value: string
  type: string
}

export interface DemoStep {
  /** 1-based index among the program's logical lines (blank and comment-only lines skipped). */
  logicalLine: number
  /** Text this step appends to the terminal. May omit the trailing newline. */
  output?: string
  /** Variables in scope after the step, in definition order. */
  variables: DemoVariable[]
}

export interface DemoInputRequest {
  prompt: string
  /** Position of the answer in the `inputs` array. */
  index: number
}

export type DemoRunStatus = 'done' | 'error' | 'awaiting-input' | 'unsupported'

export interface DemoRunResult {
  status: DemoRunStatus
  exampleId: string | null
  steps: DemoStep[]
  stdout: string
  stderr?: string
  inputRequest?: DemoInputRequest
  /** Answers the narrated run uses so input() examples never block playback. */
  sampleInputs?: string[]
  /** Illustrative for the demo runner; measured for Pyodide/sandbox runs. */
  illustrativeDurationMs: number
  message?: string
  /** Which runner produced the result. Missing means the demo simulator. */
  engine?: RunEngine
}

export type RunEngine = 'pyodide' | 'sandbox' | 'demo'

interface TraceOutcome {
  steps: DemoStep[]
  stderr?: string
  inputRequest?: DemoInputRequest
}

interface SeededExample {
  id: string
  source: string
  sampleInputs?: string[]
  trace: (inputs: string[]) => TraceOutcome
}

function createTrace() {
  const variables = new Map<string, DemoVariable>()
  const steps: DemoStep[] = []
  const snapshot = () => Array.from(variables.values())

  return {
    steps,
    assign(logicalLine: number, name: string, value: string, type: string) {
      variables.set(name, { name, value, type })
      steps.push({ logicalLine, variables: snapshot() })
    },
    print(logicalLine: number, text: string) {
      steps.push({ logicalLine, output: `${text}\n`, variables: snapshot() })
    },
    input(logicalLine: number, prompt: string, answer: string, name: string, value: string, type: string) {
      variables.set(name, { name, value, type })
      steps.push({ logicalLine, output: `${prompt}${answer}\n`, variables: snapshot() })
    },
  }
}

const traceback = (line: number, message: string) =>
  `Traceback (most recent call last):\n  File "main.py", line ${line}, in <module>\n${message}`

const pyRepr = (text: string) => `'${text.replaceAll('\\', '\\\\').replaceAll("'", "\\'")}'`

const SEEDED_EXAMPLES: SeededExample[] = [
  {
    id: 'variables-box',
    source: 'x = 10\nprint(x)\nprint(type(x))',
    trace: () => {
      const t = createTrace()
      t.assign(1, 'x', '10', 'int')
      t.print(2, '10')
      t.print(3, "<class 'int'>")
      return { steps: t.steps }
    },
  },
  {
    id: 'reassign-score',
    source: 'score = 10\nscore = score + 2\nprint(score)',
    trace: () => {
      const t = createTrace()
      t.assign(1, 'score', '10', 'int')
      t.assign(2, 'score', '12', 'int')
      t.print(3, '12')
      return { steps: t.steps }
    },
  },
  {
    id: 'data-types',
    source:
      'age = 21\nprice = 3.5\nname = "Priya"\nready = True\nscores = [90, 85, 77]\nprint(type(age), type(price))\nprint(type(name), type(ready))\nprint(type(scores))',
    trace: () => {
      const t = createTrace()
      t.assign(1, 'age', '21', 'int')
      t.assign(2, 'price', '3.5', 'float')
      t.assign(3, 'name', "'Priya'", 'str')
      t.assign(4, 'ready', 'True', 'bool')
      t.assign(5, 'scores', '[90, 85, 77]', 'list')
      t.print(6, "<class 'int'> <class 'float'>")
      t.print(7, "<class 'str'> <class 'bool'>")
      t.print(8, "<class 'list'>")
      return { steps: t.steps }
    },
  },
  {
    id: 'naming-rules',
    source: 'total_score = 42\n_hidden = "ok"\nplayer2 = "Arun"\nprint(total_score, player2)',
    trace: () => {
      const t = createTrace()
      t.assign(1, 'total_score', '42', 'int')
      t.assign(2, '_hidden', "'ok'", 'str')
      t.assign(3, 'player2', "'Arun'", 'str')
      t.print(4, '42 Arun')
      return { steps: t.steps }
    },
  },
  {
    id: 'input-print',
    source:
      'name = input("Your name? ")\nage = int(input("Your age? "))\nprint("Hi", name)\nprint("Next year you will be", age + 1)',
    sampleInputs: ['Priya', '21'],
    trace: (inputs) => {
      const t = createTrace()
      const name = inputs[0]
      if (name === undefined) return { steps: t.steps, inputRequest: { prompt: 'Your name? ', index: 0 } }
      t.input(1, 'Your name? ', name, 'name', pyRepr(name), 'str')

      const ageText = inputs[1]
      if (ageText === undefined) return { steps: t.steps, inputRequest: { prompt: 'Your age? ', index: 1 } }
      const trimmed = ageText.trim()
      if (!/^[+-]?\d+$/.test(trimmed)) {
        t.steps.push({ logicalLine: 2, output: `Your age? ${ageText}\n`, variables: t.steps.at(-1)?.variables ?? [] })
        return {
          steps: t.steps,
          stderr: traceback(2, `ValueError: invalid literal for int() with base 10: ${pyRepr(ageText)}`),
        }
      }
      const age = Number.parseInt(trimmed, 10)
      t.input(2, 'Your age? ', ageText, 'age', String(age), 'int')
      t.print(3, `Hi ${name}`)
      t.print(4, `Next year you will be ${age + 1}`)
      return { steps: t.steps }
    },
  },
  {
    id: 'bill-calculator',
    source: 'item = "Notebook"\nprice = 45.0\nquantity = 3\ntotal = price * quantity\nprint(item, "x", quantity, "=", total)',
    trace: () => {
      const t = createTrace()
      t.assign(1, 'item', "'Notebook'", 'str')
      t.assign(2, 'price', '45.0', 'float')
      t.assign(3, 'quantity', '3', 'int')
      t.assign(4, 'total', '135.0', 'float')
      t.print(5, 'Notebook x 3 = 135.0')
      return { steps: t.steps }
    },
  },
  {
    id: 'type-error',
    source: 'print("5" + 5)',
    trace: () => ({
      steps: [],
      stderr: traceback(1, 'TypeError: can only concatenate str (not "int") to str'),
    }),
  },
  {
    id: 'syntax-error-digit',
    source: '2player = "no"',
    trace: () => ({
      steps: [],
      stderr: '  File "main.py", line 1\n    2player = "no"\n    ^\nSyntaxError: invalid decimal literal',
    }),
  },
]

/** Lines that do something, so formatting and comments don't break matching. */
export function logicalLines(code: string): { text: string; physicalLine: number }[] {
  return code
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((text, index) => ({ text: text.trim(), physicalLine: index + 1 }))
    .filter((line) => line.text !== '' && !line.text.startsWith('#'))
}

function normaliseLine(line: string) {
  return line
    .replace(/\s+#[^"']*$/, '')
    .replace(/'/g, '"')
    .replace(/\s*([=+\-*/(),[\]])\s*/g, '$1')
    .replace(/\s+/g, ' ')
}

function normalise(code: string) {
  return logicalLines(code)
    .map((line) => normaliseLine(line.text))
    .join('\n')
}

const examplesByShape = new Map(SEEDED_EXAMPLES.map((example) => [normalise(example.source), example]))

export function findSeededExample(code: string) {
  return examplesByShape.get(normalise(code)) ?? null
}

/** Maps a trace's logical line back to the 1-based line number in `code`. */
export function physicalLineFor(code: string, logicalLine: number): number | null {
  return logicalLines(code)[logicalLine - 1]?.physicalLine ?? null
}

/**
 * Simulates running `code`. Deterministic: the same code and inputs always
 * give the same result. For input() examples, call again with one more answer
 * whenever the result is `awaiting-input`.
 */
export function runPythonDemo(code: string, inputs: string[] = []): DemoRunResult {
  const example = findSeededExample(code)
  if (!example) {
    return {
      status: 'unsupported',
      exampleId: null,
      steps: [],
      stdout: '',
      illustrativeDurationMs: 0,
      message: UNSUPPORTED_MESSAGE,
    }
  }

  const outcome = example.trace(inputs)
  const stdout = outcome.steps.map((step) => step.output ?? '').join('')
  const status: DemoRunStatus = outcome.stderr ? 'error' : outcome.inputRequest ? 'awaiting-input' : 'done'

  return {
    status,
    exampleId: example.id,
    steps: outcome.steps,
    stdout,
    stderr: outcome.stderr,
    inputRequest: outcome.inputRequest,
    sampleInputs: example.sampleInputs,
    illustrativeDurationMs: 8 + outcome.steps.length * 3 + stdout.length % 7,
  }
}


/* -------------------------------------------------------------------------- */
/* Real Python: Pyodide in a Web Worker                                        */
/* -------------------------------------------------------------------------- */

export const PYODIDE_VERSION = '0.27.2'
export const PYODIDE_INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`
const RUN_TIMEOUT_MS = 10_000
/** How long a run waits for the download before falling back (the download keeps going). */
const LOAD_WAIT_MS = 90_000
/** A download that has not finished by now is treated as stalled and restarted. */
const LOAD_HARD_LIMIT_MS = 300_000

const PY_HARNESS = String.raw`
import sys, builtins, traceback
_src = __bookey_src
_inputs = list(__bookey_inputs)
_out = []
_steps = []
_state = {'line': None, 'outlen': 0, 'size': 0}
_request = {}
class _NeedInput(BaseException): pass
class _TooMuch(BaseException): pass
class _W:
    def write(self, s):
        s = str(s)
        _state['size'] += len(s)
        if _state['size'] > 100000: raise _TooMuch()
        _out.append(s)
        return len(s)
    def flush(self): pass
def _input(prompt=''):
    prompt = str(prompt)
    if not _inputs:
        _request['prompt'] = prompt
        raise _NeedInput()
    v = _inputs.pop(0)
    _out.append(prompt + v + '\n')
    return v
_real_import = builtins.__import__
_BLOCKED = {'js', 'pyodide', 'pyodide_js', '_pyodide', '_pyodide_core', 'micropip', 'pyodide_http'}
def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level == 0 and name.split('.')[0] in _BLOCKED:
        raise ImportError("import of '%s' is disabled in the Bookey sandbox" % name)
    return _real_import(name, globals, locals, fromlist, level)
_safe_builtins = dict(builtins.__dict__)
_safe_builtins['__import__'] = _guarded_import
_safe_builtins['input'] = _input
_ns = {'__name__': '__main__', 'input': _input, '__builtins__': _safe_builtins}
def _snap():
    vs = []
    for k, v in list(_ns.items()):
        if k.startswith('__') or k == 'input' or type(v).__name__ == 'module': continue
        try: r = repr(v)
        except Exception: r = '<?>'
        if len(r) > 80: r = r[:77] + '...'
        vs.append([k, r, type(v).__name__])
    return vs
def _flush():
    if _state['line'] is None: return
    text = ''.join(_out)
    _steps.append([_state['line'], text[_state['outlen']:], _snap()])
    _state['outlen'] = len(text)
def _tracer(frame, event, arg):
    if frame.f_code.co_filename != 'main.py' or frame.f_code.co_name != '<module>': return None
    if event == 'line':
        _flush()
        _state['line'] = frame.f_lineno
        if len(_steps) > 400: raise _TooMuch()
    return _tracer
_status = 'done'
_stderr = ''
_old = sys.stdout
sys.stdout = _W()
try:
    _code = compile(_src, 'main.py', 'exec')
    sys.settrace(_tracer)
    exec(_code, _ns)
except _NeedInput:
    _status = 'awaiting-input'
    _state['line'] = None
except _TooMuch:
    _status = 'error'
    _stderr = 'RuntimeError: the program produced too much output or ran too many steps'
except SyntaxError as e:
    _status = 'error'
    _state['line'] = None
    _stderr = ''.join(traceback.format_exception_only(type(e), e))
except BaseException as e:
    _status = 'error'
    tb = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == 'main.py']
    lines = ['Traceback (most recent call last):']
    for f in tb:
        lines.append('  File "main.py", line %d, in %s' % (f.lineno, f.name))
        src_lines = _src.splitlines()
        if 0 < f.lineno <= len(src_lines): lines.append('    ' + src_lines[f.lineno - 1].strip())
    _stderr = '\n'.join(lines) + '\n' + ''.join(traceback.format_exception_only(type(e), e))
finally:
    sys.settrace(None)
    sys.stdout = _old
    _flush()
{'status': _status, 'steps': _steps, 'stderr': _stderr.rstrip('\n'), 'prompt': _request.get('prompt')}
`

/**
 * Runs inside the sandboxed iframe's worker (opaque origin). The worker has no
 * network of its own for Pyodide: the parent fetches the runtime files (so the
 * service worker can serve them offline) and hands the bytes over. Once Python
 * is loaded, network/storage globals are removed and `js`/`pyodide` imports are
 * blocked for learner code (see PY_HARNESS).
 */
const WORKER_SOURCE = `
const INDEX = ${JSON.stringify(PYODIDE_INDEX_URL)};
const realImportScripts = self.importScripts.bind(self);
let fetchSeq = 0;
const fetches = new Map();
function hostFetch(url) {
  return new Promise((resolve, reject) => {
    fetchSeq += 1;
    fetches.set(fetchSeq, { resolve, reject });
    self.postMessage({ fetchId: fetchSeq, url });
  });
}
const blocked = (name) => function () { throw new Error(name + ' is disabled in the Bookey sandbox'); };
function lockDown() {
  const deny = { fetch: blocked('fetch'), importScripts: blocked('importScripts'), XMLHttpRequest: undefined,
    WebSocket: undefined, EventSource: undefined, indexedDB: undefined, caches: undefined,
    BroadcastChannel: undefined, Worker: undefined, SharedWorker: undefined };
  for (const key of Object.keys(deny)) {
    try { Object.defineProperty(self, key, { value: deny[key], writable: false, configurable: false }); } catch (e) {}
  }
}
let pyodideReady = null;
function load() {
  if (!pyodideReady) {
    pyodideReady = (async () => {
      const scripts = {};
      for (const file of ['pyodide.js', 'pyodide.asm.js']) {
        const res = await hostFetch(INDEX + file);
        scripts[INDEX + file] = URL.createObjectURL(new Blob([res.buffer], { type: 'text/javascript' }));
      }
      self.importScripts = (...urls) => realImportScripts(...urls.map((u) => {
        let key = String(u);
        if (key.startsWith('blob:')) return key;
        try { key = new URL(key, INDEX).href; } catch (e) {}
        if (scripts[key]) return scripts[key];
        throw new Error('importScripts blocked: ' + key);
      }));
      self.fetch = async (input) => {
        const url = new URL(typeof input === 'string' ? input : (input && input.url) || String(input), INDEX).href;
        if (!url.startsWith(INDEX)) throw new TypeError('Network access is disabled in the Bookey sandbox');
        const res = await hostFetch(url);
        const type = url.endsWith('.wasm') ? 'application/wasm' : res.type || 'application/octet-stream';
        return new Response(res.buffer, { status: 200, headers: { 'Content-Type': type } });
      };
      realImportScripts(scripts[INDEX + 'pyodide.js']);
      const py = await loadPyodide({ indexURL: INDEX });
      lockDown();
      return py;
    })();
  }
  return pyodideReady;
}
self.onmessage = async (event) => {
  const data = event.data || {};
  if (data.fetchReply !== undefined) {
    const waiter = fetches.get(data.fetchReply);
    fetches.delete(data.fetchReply);
    if (waiter) data.ok ? waiter.resolve(data) : waiter.reject(new Error(data.error || 'fetch failed'));
    return;
  }
  const { id, type, code, inputs, harness } = data;
  try {
    const py = await load();
    if (type === 'warm') { self.postMessage({ id, ok: true, warm: true }); return; }
    py.globals.set('__bookey_src', code);
    py.globals.set('__bookey_inputs', py.toPy(inputs || []));
    const started = performance.now();
    const proxy = await py.runPythonAsync(harness);
    const result = proxy.toJs({ dict_converter: Object.fromEntries });
    if (proxy.destroy) proxy.destroy();
    self.postMessage({ id, ok: true, result, durationMs: Math.round(performance.now() - started) });
  } catch (error) {
    if (type === 'warm') pyodideReady = null;
    self.postMessage({ id, ok: false, error: String((error && error.message) || error) });
  }
};
`

type PyodideState = 'idle' | 'loading' | 'ready' | 'failed'
interface WorkerReply {
  id: number
  ok: boolean
  warm?: boolean
  error?: string
  durationMs?: number
  result?: {
    status: 'done' | 'error' | 'awaiting-input'
    steps: [number, string, [string, string, string][]][]
    stderr: string
    prompt?: string | null
  }
}

let host: SandboxHost | null = null
let pyState: PyodideState = 'idle'
let warmPromise: Promise<boolean> | null = null
let messageId = 0
const waiting = new Map<number, (reply: WorkerReply) => void>()

/** The worker asks for Pyodide files; only the pinned CDN folder is served. */
async function serveFetch(target: SandboxHost, fetchId: number, url: string) {
  if (!url.startsWith(PYODIDE_INDEX_URL)) {
    target.post({ fetchReply: fetchId, ok: false, error: 'blocked' })
    return
  }
  try {
    const res = await fetch(url, { mode: 'cors', credentials: 'omit' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const buffer = await res.arrayBuffer()
    target.post({ fetchReply: fetchId, ok: true, buffer, type: res.headers.get('Content-Type') ?? '' }, [buffer])
  } catch (error) {
    target.post({ fetchReply: fetchId, ok: false, error: String((error as Error)?.message ?? error) })
  }
}

function getHost(): SandboxHost | null {
  if (typeof window === 'undefined') return null
  if (host) return host
  let created: SandboxHost | null = null
  try {
    created = createSandboxHost(
      WORKER_SOURCE,
      (raw) => {
        if (!raw || typeof raw !== 'object' || !created) return
        const data = raw as Partial<WorkerReply> & { fetchId?: number; url?: string }
        if (typeof data.fetchId === 'number') {
          void serveFetch(created, data.fetchId, String(data.url ?? ''))
          return
        }
        if (typeof data.id !== 'number') return
        const done = waiting.get(data.id)
        waiting.delete(data.id)
        done?.(data as WorkerReply)
      },
      () => {
        if (host && host === created) killWorker('failed')
      },
    )
  } catch {
    created = null
  }
  if (!created) {
    pyState = 'failed'
    return null
  }
  host = created
  return host
}

function killWorker(next: PyodideState) {
  host?.kill()
  host = null
  warmPromise = null
  pyState = next
  for (const [id, done] of waiting) done({ id, ok: false, error: 'worker stopped' })
  waiting.clear()
}

function call(message: Record<string, unknown>, timeoutMs: number): Promise<WorkerReply> {
  const w = getHost()
  if (!w) return Promise.resolve({ id: -1, ok: false, error: 'Web Workers are unavailable' })
  messageId += 1
  const id = messageId
  return new Promise((resolve) => {
    const timer = window.setTimeout(() => {
      waiting.delete(id)
      // A runaway program (or a stalled download) can only be stopped by killing the worker.
      killWorker('idle')
      resolve({ id, ok: false, error: 'timeout' })
    }, timeoutMs)
    waiting.set(id, (reply) => {
      window.clearTimeout(timer)
      resolve(reply)
    })
    w.post({ ...message, id, harness: PY_HARNESS })
  })
}

export function pyodideStatus(): PyodideState {
  return pyState
}

/**
 * Starts downloading Pyodide in the background. Resolves true once Python is
 * ready. The download keeps going when a run gives up waiting (LOAD_WAIT_MS),
 * so a slow connection never restarts from zero; only a stalled one is killed.
 */
export function warmPyodide(): Promise<boolean> {
  if (pyState === 'ready') return Promise.resolve(true)
  if (warmPromise) return warmPromise
  pyState = 'loading'
  const pending: Promise<boolean> = call({ type: 'warm' }, LOAD_HARD_LIMIT_MS).then((reply) => {
    if (warmPromise === pending) {
      pyState = reply.ok ? 'ready' : 'failed'
      if (!reply.ok) warmPromise = null
    }
    return reply.ok
  })
  warmPromise = pending
  return pending
}

/** Warm only when the learner (or a narration cue) is about to run code; respects Data Saver. */
export function warmPyodideOnIntent() {
  if (typeof navigator === 'undefined') return
  const connection = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection
  if (connection?.saveData) return
  void warmPyodide()
}

/**
 * Runs Python for real. For `input()`, pass the answers so far; when they run
 * out the result is `awaiting-input` with the prompt (same contract as the demo).
 * Rejects only when the runtime can't load (offline before caching, no Worker,
 * or still downloading after LOAD_WAIT_MS; the download continues meanwhile).
 */
export async function runPython(code: string, inputs: string[] = []): Promise<DemoRunResult> {
  let waitTimer = 0
  const warmed = await Promise.race([
    warmPyodide(),
    new Promise<boolean>((resolve) => {
      waitTimer = window.setTimeout(() => resolve(false), LOAD_WAIT_MS)
    }),
  ])
  window.clearTimeout(waitTimer)
  if (!warmed) throw new Error('Python runtime unavailable')
  const reply = await call({ type: 'run', code, inputs }, RUN_TIMEOUT_MS)
  if (!reply.ok || !reply.result) {
    if (reply.error === 'timeout') {
      return {
        status: 'error',
        exampleId: findSeededExample(code)?.id ?? null,
        steps: [],
        stdout: '',
        stderr: `TimeoutError: the program ran longer than ${RUN_TIMEOUT_MS / 1000} seconds and was stopped.`,
        illustrativeDurationMs: RUN_TIMEOUT_MS,
        engine: 'pyodide',
      }
    }
    throw new Error(reply.error ?? 'Python run failed')
  }

  const logical = logicalLines(code)
  const toLogical = (physical: number) => {
    const at = logical.findIndex((line) => line.physicalLine === physical)
    return at >= 0 ? at + 1 : 1
  }
  const { status, stderr, prompt } = reply.result
  const steps: DemoStep[] = reply.result.steps.map(([line, output, vars]) => ({
    logicalLine: toLogical(line),
    output: output || undefined,
    variables: vars.map(([name, value, type]) => ({ name, value, type })),
  }))
  const stdout = steps.map((step) => step.output ?? '').join('')
  const example = findSeededExample(code)

  return {
    status,
    exampleId: example?.id ?? null,
    steps,
    stdout,
    stderr: stderr || undefined,
    inputRequest: status === 'awaiting-input' ? { prompt: prompt ?? '', index: inputs.length } : undefined,
    sampleInputs: example?.sampleInputs,
    illustrativeDurationMs: reply.durationMs ?? 0,
    engine: 'pyodide',
  }
}

/* -------------------------------------------------------------------------- */
/* HTML / JavaScript practicals                                                */
/* -------------------------------------------------------------------------- */

export type PracticalLanguage = 'python' | 'javascript' | 'html'

/** Heuristic for practicals whose page metadata only says "python". */
export function detectPracticalLanguage(code: string): PracticalLanguage {
  const trimmed = code.trim()
  if (/^<(!doctype|html|head|body|div|section|main|h[1-6]|p|style|script|button|canvas|svg)\b/i.test(trimmed)) return 'html'
  const looksPython = /^\s*(def |import |from \w+ import|print\()/m.test(trimmed)
  const looksJs = /\bconsole\.(log|error|warn)\s*\(|\bdocument\.|=>\s*[{(]|^\s*(const|let|var)\s+\w+\s*=|function\s+\w+\s*\(/m.test(trimmed)
  return looksJs && !looksPython ? 'javascript' : 'python'
}

const escapeScript = (code: string) => code.replace(/<\/script/gi, '<\\/script')

/** Plain-JS practicals that touch the DOM need a document; everything else runs in a worker. */
export function needsDom(code: string) {
  return /\b(document|window|alert|confirm|prompt|localStorage|sessionStorage|requestAnimationFrame)\b/.test(code)
}

const JS_WORKER_SOURCE = `
const fmt = (a) => a.map((v) => { try { return typeof v === 'string' ? v : JSON.stringify(v) } catch (e) { return String(v) } }).join(' ');
const send = (kind, text) => self.postMessage({ kind, text });
console.log = (...a) => send('log', fmt(a)); console.info = console.log; console.warn = console.log;
console.error = (...a) => send('error', fmt(a));
self.onerror = (m) => { send('error', String(m)); send('done'); return true; };
self.onunhandledrejection = (e) => { send('error', 'Uncaught (in promise) ' + String(e && e.reason)); };
self.onmessage = (event) => {
  try { (0, eval)(String(event.data && event.data.code)); }
  catch (error) { send('error', error && error.name ? error.name + ': ' + error.message : String(error)); }
  setTimeout(() => send('done'), 0);
};
`

/**
 * Runs JavaScript and captures console output. Code without DOM access runs in
 * a Web Worker inside a sandboxed iframe (opaque origin), so an infinite loop
 * can be terminated on timeout instead of freezing the reader. DOM code keeps
 * the sandboxed-iframe path.
 */
export function runJavaScriptSandboxed(code: string, timeoutMs = 4000): Promise<DemoRunResult> {
  if (needsDom(code)) return runJavaScriptInFrame(code, timeoutMs)
  return new Promise((resolve) => {
    const started = typeof performance === 'undefined' ? 0 : performance.now()
    let stdout = ''
    let stderr = ''
    let settled = false
    let timer = 0
    let sandbox: SandboxHost | null = null
    const finish = (status: DemoRunStatus) => {
      if (settled) return
      settled = true
      window.clearTimeout(timer)
      sandbox?.kill()
      resolve({
        status,
        exampleId: null,
        steps: stdout ? [{ logicalLine: 1, output: stdout, variables: [] }] : [],
        stdout,
        stderr: stderr || undefined,
        illustrativeDurationMs: Math.round(performance.now() - started),
        engine: 'sandbox',
      })
    }
    try {
      sandbox = createSandboxHost(
        JS_WORKER_SOURCE,
        (raw) => {
          const data = raw as { kind?: string; text?: string } | null
          if (!data) return
          if (data.kind === 'log') stdout += `${data.text}\n`
          else if (data.kind === 'error') stderr += `${data.text}\n`
          else if (data.kind === 'done') finish(stderr ? 'error' : 'done')
        },
        (message) => {
          stderr += `${message}\n`
          finish('error')
        },
      )
    } catch {
      sandbox = null
    }
    if (!sandbox) {
      resolve({ status: 'unsupported', exampleId: null, steps: [], stdout: '', illustrativeDurationMs: 0, message: 'No browser sandbox.' })
      return
    }
    timer = window.setTimeout(() => {
      stderr += `TimeoutError: script ran longer than ${timeoutMs / 1000}s`
      finish('error')
    }, timeoutMs)
    sandbox.post({ code })
  })
}

/** Runs DOM-using JavaScript in a hidden sandboxed iframe and captures console output. */
function runJavaScriptInFrame(code: string, timeoutMs: number): Promise<DemoRunResult> {
  return new Promise((resolve) => {
    if (typeof document === 'undefined') {
      resolve({ status: 'unsupported', exampleId: null, steps: [], stdout: '', illustrativeDurationMs: 0, message: 'No browser sandbox.' })
      return
    }
    const token = `bk${Math.random().toString(36).slice(2)}`
    const frame = document.createElement('iframe')
    frame.setAttribute('sandbox', 'allow-scripts')
    frame.setAttribute('aria-hidden', 'true')
    frame.style.cssText = 'position:fixed;width:0;height:0;border:0;opacity:0;pointer-events:none'
    const started = performance.now()
    let stdout = ''
    let stderr = ''
    let settled = false
    const finish = (status: DemoRunStatus) => {
      if (settled) return
      settled = true
      window.removeEventListener('message', onMessage)
      window.clearTimeout(timer)
      frame.remove()
      resolve({
        status,
        exampleId: null,
        steps: stdout ? [{ logicalLine: 1, output: stdout, variables: [] }] : [],
        stdout,
        stderr: stderr || undefined,
        illustrativeDurationMs: Math.round(performance.now() - started),
        engine: 'sandbox',
      })
    }
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frame.contentWindow || !event.data || event.data.token !== token) return
      if (event.data.kind === 'log') stdout += `${event.data.text}\n`
      else if (event.data.kind === 'error') stderr += `${event.data.text}\n`
      else if (event.data.kind === 'done') finish(stderr ? 'error' : 'done')
    }
    const timer = window.setTimeout(() => {
      stderr += `TimeoutError: script ran longer than ${timeoutMs / 1000}s`
      finish('error')
    }, timeoutMs)
    window.addEventListener('message', onMessage)
    frame.srcdoc = `<!doctype html><script>
const T=${JSON.stringify(token)};const send=(kind,text)=>parent.postMessage({token:T,kind,text},'*');
const fmt=(a)=>a.map(v=>{try{return typeof v==='string'?v:JSON.stringify(v)}catch(e){return String(v)}}).join(' ');
console.log=(...a)=>send('log',fmt(a));console.info=console.log;console.warn=(...a)=>send('log',fmt(a));
console.error=(...a)=>send('error',fmt(a));
window.onerror=(m)=>{send('error',String(m));send('done');};
</script><script>${escapeScript(code)}
;setTimeout(()=>send('done'),0)</script>`
    document.body.appendChild(frame)
  })
}
