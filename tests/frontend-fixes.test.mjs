// Plain node tests for pure helpers (Node >= 23.6 strips TS types natively).
// Run: node --test tests/
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { buildSandboxSrcdoc, scriptLiteral } from '../lib/sandbox-host.ts'
import { resolveApiBase } from '../lib/api/base-url.ts'

test('scriptLiteral round-trips and never contains a closing script tag', () => {
  const nasty = 'a</script><script>alert(1)</script> "\'\\'
  const literal = scriptLiteral(nasty)
  assert.ok(!literal.toLowerCase().includes('</script'))
  assert.equal(eval(literal), nasty)
})

test('sandbox srcdoc embeds worker source safely and targets the parent origin', () => {
  const doc = buildSandboxSrcdoc('postMessage("</script>")', 'tok', 'http://localhost:3000')
  assert.equal(doc.toLowerCase().split('</script>').length, 2) // only the real closing tag
  assert.ok(doc.includes('"http://localhost:3000"'))
  assert.ok(buildSandboxSrcdoc('x', 't', 'null').includes('"*"'))
})

test('resolveApiBase: one answer for "is a backend configured"', () => {
  assert.equal(resolveApiBase(undefined, undefined, 'development'), null)
  assert.equal(resolveApiBase('', '1', 'production'), null)
  assert.equal(resolveApiBase('http://127.0.0.1:8000/', undefined, 'development'), '')
  assert.equal(resolveApiBase('http://127.0.0.1:8000/', undefined, 'production'), 'http://127.0.0.1:8000')
  assert.equal(resolveApiBase('https://api.x', '0', 'development'), 'https://api.x')
  assert.equal(resolveApiBase('https://api.x', '1', 'production'), '')
})
