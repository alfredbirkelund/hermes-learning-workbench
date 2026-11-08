// Pane logic harness: the real desktop/plugin.js rendered with React 19 in jsdom against the REAL plugin
// backend (tests/e2e/pane_server.py). Host services are stand-ins (sdk-stub.mjs). Not a visual check.
import assert from 'node:assert/strict'
import { JSDOM } from 'jsdom'

const PORT = Number(process.env.LW_PANE_PORT)
const BASE = `http://127.0.0.1:${PORT}`
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/', pretendToBeVisual: true })
for (const k of ['window', 'document', 'navigator', 'HTMLElement', 'HTMLTextAreaElement', 'HTMLInputElement', 'Node', 'Event', 'MouseEvent', 'KeyboardEvent', 'getComputedStyle', 'requestAnimationFrame', 'cancelAnimationFrame']) {
  if (!(k in globalThis) || k === 'window' || k === 'document' || k === 'navigator') {
    Object.defineProperty(globalThis, k, { value: dom.window[k], configurable: true, writable: true })
  }
}
const { createRoot } = await import('react-dom/client')
const sdk = await import('./sdk-stub.mjs')
const plugin = (await import('./plugin.mjs')).default
const { host, fire, requests } = sdk

const sleep = ms => new Promise(r => setTimeout(r, ms))
async function waitFor(fn, label, timeout = 8000) {
  const end = Date.now() + timeout
  let last
  while (Date.now() < end) {
    try { const v = fn(); if (v) return v } catch (e) { last = e }
    await sleep(40)
  }
  throw new Error(`timed out: ${label}${last ? ` (${last.message})` : ''}`)
}
const results = []
async function step(name, fn) {
  try { await fn(); results.push(['PASS', name]); console.log(`PASS ${name}`) }
  catch (e) { results.push(['FAIL', name]); console.log(`FAIL ${name}: ${e.stack || e}`) }
}

async function rest(path, opts = {}) {
  const res = await fetch(`${BASE}/api/plugins/learning-workbench${path.startsWith('/') ? path : '/' + path}`, {
    method: opts.method || 'GET', headers: { 'content-type': 'application/json' },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined })
  const text = await res.text()
  if (res.status >= 400) throw new Error(`${res.status}: ${text}`) // same shape as Desktop's bridge
  return JSON.parse(text)
}
async function agent(tool, args, session = 'chat-A') {
  const res = await fetch(`${BASE}/__agent/${tool}`, { method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ session_id: session, args }) })
  return res.json()
}
const clipboard = []
function mount() {
  let contribution
  const ctx = { source: 'learning-workbench', rest, os: { writeClipboard: async t => { clipboard.push(t); return true }, revealPath: async () => true },
                register: c => { contribution = c; return () => {} }, onDispose: () => {} }
  plugin.register(ctx)
  const el = document.createElement('div')
  document.body.appendChild(el)
  const root = createRoot(el)
  root.render(contribution.render())
  return { el, root, unmount: () => { root.unmount(); el.remove() } }
}
const q = (el, sel) => el.querySelector(sel)
const qa = (el, sel) => [...el.querySelectorAll(sel)]
function type(node, text) {
  const proto = node.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype
  Object.getOwnPropertyDescriptor(proto, 'value').set.call(node, text)
  node.dispatchEvent(new window.Event('input', { bubbles: true }))
}
const click = node => node.dispatchEvent(new window.MouseEvent('click', { bubbles: true }))
const byText = (el, tag, text) => qa(el, tag).find(n => n.textContent.trim().startsWith(text))
const changed = () => fire('plugin.learning-workbench.study.changed', {})
const lastPrompt = () => requests.filter(r => r.method === 'prompt.submit').at(-1)

host.state.focusedStoredSessionId.set('chat-A')
host.state.focusedSessionId.set('runtime-A')
let pane = mount()
let study

await step('empty chat shows the start form', async () => {
  await waitFor(() => q(pane.el, '[data-lw="start-form"]'), 'start form')
})

await step('starting from the pane creates a learner study and asks the tutor', async () => {
  type(q(pane.el, '[data-lw="goal"]'), 'Understand why d/dx x² = 2x')
  type(q(pane.el, '[data-lw="material"]'), 'The derivative is the limit of (f(x+h) − f(x))/h.')
  await waitFor(() => !q(pane.el, '[data-lw="start"]').disabled, 'start enabled')
  click(q(pane.el, '[data-lw="start"]'))
  await waitFor(() => q(pane.el, '[data-lw="goal-text"]'), 'goal shown')
  const view = (await agent('learning_workbench_get', {})).study
  study = view
  assert.equal(view.created_by, 'learner')
  assert.equal(view.material.source, 'pasted_by_learner')
  const p = lastPrompt()
  assert.equal(p.params.session_id, 'runtime-A')
  assert.equal(p.params.queued, true)
  assert.match(p.params.text, /^\[Learning Workbench\] I started a study/)
})

await step('an exercise the agent proposes appears in the pane, with hints and solution hidden', async () => {
  const out = await agent('learning_workbench_propose_exercise', { study_id: study.study_id, study_revision: study.study_revision,
    kind: 'diagnostic', prompt: 'Expand (x+h)².', reference_answer: 'x² + 2xh + h²', rubric: 'All three terms.',
    hints: ['Write it as (x+h)(x+h).', 'Use FOIL.'], reasoning: 'requested' })
  assert.equal(out.ok, true, JSON.stringify(out))
  changed()
  await waitFor(() => q(pane.el, '[data-lw="prompt"]')?.textContent === 'Expand (x+h)².', 'prompt shown')
  assert.equal(q(pane.el, '[data-lw="solution"]'), null)
  assert.equal(q(pane.el, '[data-lw="hints"]'), null)
  assert.match(q(pane.el, '[data-lw="hint"]').textContent, /2 left/)
  assert.match(pane.el.textContent, /Your reasoning \(requested\)/)
})

await step('typing autosaves the draft and shows the save state', async () => {
  type(q(pane.el, '[data-lw="answer"]'), 'x² + h²')
  await waitFor(() => q(pane.el, '[data-lw-save="pending"]'), 'pending state')
  await waitFor(() => q(pane.el, '[data-lw-save="saved"]'), 'saved state')
  const s = await rest('/session-study?session_id=chat-A')
  assert.equal(s.study.draft.answer, 'x² + h²')
})

await step('closing and reopening the pane restores the same exercise and draft', async () => {
  type(q(pane.el, '[data-lw="reasoning"]'), 'square each term')
  await waitFor(() => q(pane.el, '[data-lw-save="saved"]'), 'saved again')
  pane.unmount()
  pane = mount()
  await waitFor(() => q(pane.el, '[data-lw="answer"]')?.value === 'x² + h²', 'answer restored')
  assert.equal(q(pane.el, '[data-lw="reasoning"]').value, 'square each term')
})

await step('a second window editing the same draft gets a conflict, not a silent overwrite', async () => {
  const other = mount()
  await waitFor(() => q(other.el, '[data-lw="answer"]')?.value === 'x² + h²', 'second window loaded')
  type(q(other.el, '[data-lw="answer"]'), 'from window two')
  await waitFor(() => q(other.el, '[data-lw-save="saved"]'), 'window two saved')
  type(q(pane.el, '[data-lw="answer"]'), 'x² + h² (window one)')
  await waitFor(() => /changed in another window/.test(pane.el.textContent), 'conflict shown')
  click(byText(pane.el, 'button', 'Keep mine'))
  await waitFor(() => q(pane.el, '[data-lw-save="saved"]'), 'kept mine saved')
  const s = await rest('/session-study?session_id=chat-A')
  assert.equal(s.study.draft.answer, 'x² + h² (window one)')
  other.unmount()
})

await step('a double-clicked hint reveals one hint', async () => {
  const btn = q(pane.el, '[data-lw="hint"]')
  click(btn); click(btn)
  await waitFor(() => qa(pane.el, '[data-lw="hints"] li').length === 1, 'one hint shown')
  await sleep(300)
  assert.equal(qa(pane.el, '[data-lw="hints"] li').length, 1)
  assert.match(q(pane.el, '[data-lw="hint"]').textContent, /1 left/)
})

await step('submit records one attempt (even when double-clicked) and starts a tutor turn', async () => {
  const before = requests.length
  type(q(pane.el, '[data-lw="answer"]'), 'x² + h²')
  const btn = q(pane.el, '[data-lw="submit"]')
  click(btn); click(btn)
  await waitFor(() => q(pane.el, '[data-lw="waiting"]'), 'waiting for check')
  const view = (await agent('learning_workbench_get', {})).study
  assert.equal(view.exercises[0].attempts.length, 1)
  const a = view.exercises[0].attempts[0]
  assert.equal(a.answer, 'x² + h²')
  assert.equal(a.reasoning, 'square each term')
  assert.equal(a.help_class, 'hinted')
  const prompts = requests.slice(before).filter(r => r.method === 'prompt.submit')
  assert.equal(prompts.length, 1)
  assert.match(prompts[0].params.text, /I submitted attempt 1 for exercise 1/)
  study = view
})

await step('the verdict shows the quoted learner words; the learner retries', async () => {
  const attempt = study.exercises[0].attempts[0]
  const out = await agent('learning_workbench_assess', { study_id: study.study_id, attempt_id: attempt.attempt_id,
    verdict: 'incorrect', evidence_quote: 'x² + h²', explanation: 'The cross term is missing.', gap: '(x+h)² has 2xh.' })
  assert.equal(out.ok, true, JSON.stringify(out))
  changed()
  await waitFor(() => q(pane.el, '[data-lw-verdict="incorrect"]'), 'verdict shown')
  assert.match(pane.el.textContent, /“x² \+ h²”/)
  click(q(pane.el, '[data-lw="retry"]'))
  await waitFor(() => q(pane.el, '[data-lw="answer"]')?.value === 'x² + h²', 'retry prefilled from the last answer')
})

await step('reveal needs confirmation; the next attempt is recorded after reveal', async () => {
  click(q(pane.el, '[data-lw="reveal"]'))
  await waitFor(() => q(pane.el, '[role="dialog"]'), 'dialog open')
  click(q(pane.el, '[data-dialog="cancel"]'))
  await waitFor(() => !q(pane.el, '[role="dialog"]'), 'dialog closed')
  assert.equal(q(pane.el, '[data-lw="solution"]'), null)
  click(q(pane.el, '[data-lw="reveal"]'))
  await waitFor(() => q(pane.el, '[role="dialog"]'), 'dialog open again')
  click(q(pane.el, '[data-dialog="confirm"]'))
  await waitFor(() => q(pane.el, '[data-lw="solution"]'), 'solution shown')
  type(q(pane.el, '[data-lw="answer"]'), 'x² + 2xh + h²')
  click(q(pane.el, '[data-lw="submit"]'))
  await waitFor(() => q(pane.el, '[data-lw="waiting"]'), 'waiting again')
  const view = (await agent('learning_workbench_get', {})).study
  assert.deepEqual(view.exercises[0].attempts.map(a => a.help_class), ['hinted', 'after_reveal'])
})

await step('switching chats never shows the previous chat’s study, even with a late response', async () => {
  host.state.focusedStoredSessionId.set('chat-B')
  host.state.focusedSessionId.set('runtime-B')
  await waitFor(() => q(pane.el, '[data-lw="start-form"]'), 'chat B shows its own empty state')
  await sleep(4500) // one poll cycle: a stale chat-A response must not repaint chat B
  assert.ok(q(pane.el, '[data-lw="start-form"]'))
  host.state.focusedStoredSessionId.set('chat-A')
  host.state.focusedSessionId.set('runtime-A')
  await waitFor(() => q(pane.el, '[data-lw="goal-text"]'), 'chat A study back')
})

await step('when Hermes cannot be asked, the attempt stays saved and the pane says so', async () => {
  const view = (await agent('learning_workbench_get', {})).study
  const attempt = view.exercises[0].attempts.at(-1)
  await agent('learning_workbench_assess', { study_id: view.study_id, attempt_id: attempt.attempt_id, verdict: 'correct',
    evidence_quote: '2xh', explanation: 'All three terms.' })
  const v2 = (await agent('learning_workbench_get', {})).study
  await agent('learning_workbench_propose_plan', { study_id: v2.study_id, study_revision: v2.study_revision,
    steps: [{ title: 'Expand (x+h)²', why: 'needed' }, { title: 'Limit h → 0', why: 'definition' }] })
  const v3 = (await agent('learning_workbench_get', {})).study
  await agent('learning_workbench_propose_exercise', { study_id: v3.study_id, study_revision: v3.study_revision, kind: 'check',
    step_id: v3.route.steps[1].step_id, prompt: 'Simplify (2xh + h²)/h.', reference_answer: '2x + h', rubric: 'Divide each term by h.' })
  changed()
  await waitFor(() => q(pane.el, '[data-lw="prompt"]')?.textContent.startsWith('Simplify'), 'new exercise')
  host.failRequests = true
  type(q(pane.el, '[data-lw="answer"]'), '2x + h')
  click(q(pane.el, '[data-lw="submit"]'))
  await waitFor(() => q(pane.el, '[data-lw-send="unsent"]'), 'unsent notice')
  host.failRequests = false
  const v4 = (await agent('learning_workbench_get', {})).study
  assert.equal(v4.exercises.at(-1).attempts.length, 1)
  click(q(pane.el, '[data-lw="ask-again"]'))
  await waitFor(() => q(pane.el, '[data-lw-send="sent"]'), 'sent after ask again')
})

await step('route edits from the pane are recorded as the learner’s', async () => {
  const stepEl = await waitFor(() => qa(pane.el, '[data-lw="route"] li')[0], 'route rendered')
  click(byText(stepEl, 'button', 'Skip'))
  await waitFor(() => byText(qa(pane.el, '[data-lw="route"] li')[0], 'button', 'Restore'), 'restore offered')
  const s = await rest('/session-study?session_id=chat-A')
  assert.equal(s.study.route.steps[0].status, 'skipped')
  assert.equal(s.study.route.revised_by, 'learner')
})

await step('export writes a Markdown record and can copy it', async () => {
  click(q(pane.el, '[data-lw="export"]'))
  await waitFor(() => q(pane.el, '[data-lw="exported"]'), 'export shown')
  click(byText(pane.el, 'button', 'Copy Markdown'))
  await waitFor(() => clipboard.length === 1, 'clipboard written')
  assert.match(clipboard[0], /^# Learning record: Understand why d\/dx x² = 2x/)
})

await step('delete asks with exact counts and removes the study', async () => {
  click(q(pane.el, '[data-lw="delete"]'))
  const dialog = await waitFor(() => q(pane.el, '[role="dialog"][data-destructive="1"]'), 'delete dialog')
  assert.match(dialog.textContent, /2 exercise\(s\), 3 attempt\(s\), 2 check\(s\) and 1 exported file\(s\)/)
  click(q(dialog, '[data-dialog="confirm"]'))
  await waitFor(() => q(pane.el, '[data-lw="start-form"]'), 'back to empty state')
})

pane.unmount()
const failed = results.filter(r => r[0] === 'FAIL').length
console.log(`${results.length - failed}/${results.length} pane harness steps passed`)
process.exit(failed ? 1 : 0)
