// Learning Workbench: Desktop pane.
// Disk-loaded plugin rules: only @hermes/plugin-sdk, react and react/jsx-runtime may be imported, no JSX
// syntax, theme variables only. Every learner action goes to this plugin's own routes through ctx.rest.
import { Badge, Button, ConfirmDialog, Input, Textarea, host, useValue } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Fragment, jsx, jsxs } from 'react/jsx-runtime'

const PLUGIN_ID = 'learning-workbench'
const EVENT = `plugin.${PLUGIN_ID}.study.changed`
const POLL_MS = 4000
const SAVE_DEBOUNCE_MS = 700

// ---------------------------------------------------------------------------------------------
// small helpers
// ---------------------------------------------------------------------------------------------

function h(type, props, ...children) {
  const { key, ...rest } = props || {}
  const kids = children.flat().filter(c => c !== null && c !== undefined && c !== false)
  if (kids.length === 0) return jsx(type, rest, key)
  if (kids.length === 1) return jsx(type, { ...rest, children: kids[0] }, key)
  return jsxs(type, { ...rest, children: kids }, key)
}

function requestId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') return globalThis.crypto.randomUUID()
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`
}

// ctx.rest rejects non-2xx with an Error whose message carries "<status>: <json body>".
function restError(err) {
  const msg = String((err && err.message) || err || '')
  const statusMatch = msg.match(/(\d{3}):\s*\{/)
  const brace = msg.indexOf('{')
  if (brace >= 0) {
    try {
      const body = JSON.parse(msg.slice(brace))
      return { status: statusMatch ? Number(statusMatch[1]) : 0, code: body.error || 'error',
               message: body.message || 'Something went wrong.', details: body.details || {} }
    } catch (_) { /* not JSON: fall through */ }
  }
  return { status: 0, code: 'network', message: 'The Workbench backend did not answer. Check that Hermes is running.', details: {} }
}

function every(ctx, ms, fn) {
  if (typeof ctx.setInterval === 'function') return ctx.setInterval(fn, ms)
  const id = window.setInterval(fn, ms)
  return () => window.clearInterval(id)
}

function listen(ctx, type, fn) {
  if (typeof ctx.onEvent === 'function') return ctx.onEvent(type, fn)
  if (host && typeof host.onEvent === 'function') return host.onEvent(type, fn)
  return () => {}
}

const T = {
  muted: { color: 'var(--ui-text-tertiary)' },
  faint: { color: 'var(--ui-text-quaternary)' },
  label: { fontSize: '0.72rem', letterSpacing: '0.02em', textTransform: 'uppercase', color: 'var(--ui-text-tertiary)' },
  text: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', lineHeight: 1.55 },
  section: { borderTop: '1px solid var(--ui-stroke-secondary)', paddingTop: 10 },
  quote: { borderLeft: '2px solid var(--ui-stroke-secondary)', paddingLeft: 8, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' },
}

const HELP = { unaided: 'unaided', after_feedback: 'after feedback', hinted: 'after hints', after_reveal: 'after reveal' }
const VERDICT = {
  correct: ['Correct', 'success'], partially_correct: ['Partly correct', 'warn'],
  incorrect: ['Not yet', 'destructive'], cannot_judge: ['Could not judge', 'muted'],
}

function Row({ children, gap = 6, style }) {
  return h('div', { style: { display: 'flex', alignItems: 'center', gap, flexWrap: 'wrap', ...style } }, children)
}

function Label({ children, htmlFor }) {
  return h(htmlFor ? 'label' : 'div', { htmlFor, style: T.label }, children)
}

// ---------------------------------------------------------------------------------------------
// start form (no study in this chat)
// ---------------------------------------------------------------------------------------------

function StartForm({ ctx, storedId, onStarted, sendToTutor }) {
  const [goal, setGoal] = useState('')
  const [material, setMaterial] = useState('')
  const [state, setState] = useState({ busy: false, error: '' })
  const starting = useRef(false)
  const start = async () => {
    if (!goal.trim() || starting.current) return
    starting.current = true
    setState({ busy: true, error: '' })
    try {
      const out = await ctx.rest('/studies', { method: 'POST', body: { session_id: storedId, goal, material_text: material } })
      setState({ busy: false, error: '' })
      onStarted(out.study)
      sendToTutor(`[Learning Workbench] I started a study: “${goal.trim().slice(0, 200)}”. Please diagnose where I should start.`)
    } catch (err) {
      setState({ busy: false, error: restError(err).message })
    } finally {
      starting.current = false
    }
  }
  return h('div', { style: { display: 'flex', flexDirection: 'column', gap: 10 }, 'data-lw': 'start-form' },
    h('div', { style: T.text }, 'Bring a goal and, if you like, the text you are studying. Hermes diagnoses where to start, proposes a short route and poses one exercise at a time here.'),
    h(Label, { htmlFor: 'lw-goal' }, 'Goal'),
    h(Input, { id: 'lw-goal', value: goal, placeholder: 'What do you want to understand or be able to do?',
               onChange: e => setGoal(e.target.value), maxLength: 500, 'data-lw': 'goal' }),
    h(Label, { htmlFor: 'lw-material' }, 'Material (optional)'),
    h(Textarea, { id: 'lw-material', value: material, rows: 6, placeholder: 'Paste the text you are studying',
                  onChange: e => setMaterial(e.target.value), spellCheck: true, 'data-lw': 'material' }),
    h('div', { style: { ...T.faint, fontSize: '0.75rem' } },
      'Saved in this Hermes profile. The material and your submitted answers are sent to your configured model provider when the tutor reads the study.'),
    h(Row, null,
      h(Button, { onClick: start, disabled: !goal.trim() || state.busy || !storedId, 'data-lw': 'start' },
        state.busy ? 'Starting…' : 'Start study'),
      state.error ? h('span', { role: 'alert', style: { color: 'var(--ui-text-secondary)' } }, state.error) : null),
    h('div', { style: { ...T.muted, fontSize: '0.8rem' } },
      'You can also ask in chat, for example: “Teach me this from notes.pdf”. Hermes reads the file with its own tools.'))
}

// ---------------------------------------------------------------------------------------------
// route
// ---------------------------------------------------------------------------------------------

function RouteList({ ctx, study, onChanged, onError }) {
  const route = study.route
  const [editing, setEditing] = useState(null)
  const [title, setTitle] = useState('')
  if (!route) {
    return h('div', { style: T.muted }, study.diagnostics.used
      ? `Diagnosis: ${study.diagnostics.used} of at most ${study.diagnostics.cap} questions. The route comes next.`
      : 'The tutor starts with a short diagnosis, then proposes a route.')
  }
  const act = async (op, stepId, newTitle) => {
    try {
      await ctx.rest(`/studies/${study.study_id}/route`, {
        method: 'POST', body: { base_plan_revision: route.plan_revision, op, step_id: stepId, title: newTitle } })
      setEditing(null)
    } catch (err) {
      const e = restError(err)
      onError(e.code === 'stale_route' ? 'The route changed in another window; it has been reloaded.' : e.message)
    }
    onChanged()
  }
  return h('ol', { style: { margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 6 }, 'data-lw': 'route' },
    route.steps.map(step => {
      const ev = step.evidence
      const skipped = step.status === 'skipped'
      return h('li', { key: step.step_id, style: { opacity: skipped ? 0.6 : 1 }, 'data-lw-step': step.step_id },
        editing === step.step_id
          ? h(Row, null,
              h(Input, { value: title, onChange: e => setTitle(e.target.value), maxLength: 120, 'aria-label': 'Step name', autoFocus: true,
                         onKeyDown: e => { if (e.key === 'Enter' && title.trim()) act('rename', step.step_id, title); if (e.key === 'Escape') setEditing(null) } }),
              h(Button, { size: 'xs', onClick: () => act('rename', step.step_id, title), disabled: !title.trim() }, 'Save'),
              h(Button, { size: 'xs', variant: 'ghost', onClick: () => setEditing(null) }, 'Cancel'))
          : h('div', null,
              h('div', { style: { ...T.text, textDecoration: skipped ? 'line-through' : 'none' } }, step.title),
              h('div', { style: { ...T.muted, fontSize: '0.78rem' } }, step.why),
              h(Row, { gap: 4 },
                h('span', { style: { ...T.faint, fontSize: '0.75rem' } },
                  `${ev.correct_unaided} unaided ✓ · ${ev.correct_with_help} with help · ${ev.attempts} attempt${ev.attempts === 1 ? '' : 's'}`),
                h(Button, { size: 'xs', variant: 'ghost', onClick: () => { setEditing(step.step_id); setTitle(step.title) } }, 'Rename'),
                h(Button, { size: 'xs', variant: 'ghost', onClick: () => act(skipped ? 'restore' : 'skip', step.step_id) },
                  skipped ? 'Restore' : 'Skip'))))
    }),
    route.removed_steps.map(step => h('li', { key: step.step_id, style: T.faint },
      `${step.title} (removed from the route; ${step.evidence.attempts} attempt(s) kept)`)))
}

// ---------------------------------------------------------------------------------------------
// checks (verdicts) and contests
// ---------------------------------------------------------------------------------------------

function Verdicts({ ctx, study, exercise, attempt, onChanged, sendToTutor }) {
  const [contesting, setContesting] = useState(false)
  const [text, setText] = useState('')
  const [state, setState] = useState({ busy: false, error: '' })
  const rid = useRef(null)
  const sending = useRef(false)
  if (!attempt.assessments.length) return null
  const latest = attempt.assessments[attempt.assessments.length - 1]
  const pending = attempt.contests.some(c => !c.answered)
  const send = async () => {
    if (!text.trim() || sending.current) return
    sending.current = true
    if (!rid.current) rid.current = requestId()
    setState({ busy: true, error: '' })
    try {
      const out = await ctx.rest(`/studies/${study.study_id}/assessments/${latest.assessment_id}/contest`, {
        method: 'POST', body: { client_request_id: rid.current, text } })
      rid.current = null
      setContesting(false)
      setText('')
      setState({ busy: false, error: '' })
      onChanged()
      if (!out.duplicate) {
        sendToTutor(`[Learning Workbench] I contested your check of attempt ${attempt.number} on exercise ${exercise.sequence}. Please look again.`)
      }
    } catch (err) {
      setState({ busy: false, error: restError(err).message })
    } finally {
      sending.current = false
    }
  }
  return h('div', { style: { display: 'flex', flexDirection: 'column', gap: 8 }, 'data-lw': 'verdicts' },
    attempt.assessments.map(s => {
      const [label, tone] = VERDICT[s.verdict] || [s.verdict, 'muted']
      return h('div', { key: s.assessment_id, style: { display: 'flex', flexDirection: 'column', gap: 4 }, 'data-lw-verdict': s.verdict },
        h(Row, null,
          h(Badge, { variant: tone }, label),
          h('span', { style: { ...T.faint, fontSize: '0.75rem' } },
            `${s.revision > 1 ? `revised check (revision ${s.revision})` : 'tutor’s check'} · attempt ${attempt.number} · ${HELP[attempt.help_class] || attempt.help_class}`)),
        h('div', { style: { ...T.muted, fontSize: '0.78rem' } }, 'Quoted from your answer:'),
        h('div', { style: T.quote }, `“${s.evidence_quote}”`),
        h('div', { style: T.text }, s.explanation),
        s.gap ? h('div', { style: T.text }, h('span', { style: T.muted }, 'Gap: '), s.gap) : null)
    }),
    attempt.contests.map(c => h('div', { key: c.contest_id, style: { ...T.muted, fontSize: '0.8rem' } },
      `You contested this check${c.answered ? '' : ' (waiting for the tutor)'}: “${c.text}”`)),
    !pending && !contesting
      ? h(Row, null, h(Button, { size: 'xs', variant: 'ghost', onClick: () => setContesting(true), 'data-lw': 'contest' }, 'Contest this check…'))
      : null,
    contesting
      ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: 6 } },
          h(Textarea, { value: text, rows: 3, onChange: e => setText(e.target.value), placeholder: 'Why is the check wrong?',
                        'aria-label': 'Why the check is wrong', spellCheck: true, autoFocus: true, 'data-lw': 'contest-text' }),
          h(Row, null,
            h(Button, { size: 'xs', onClick: send, disabled: !text.trim() || state.busy, 'data-lw': 'contest-send' }, state.busy ? 'Sending…' : 'Send contest'),
            h(Button, { size: 'xs', variant: 'ghost', onClick: () => { setContesting(false); setState({ busy: false, error: '' }) } }, 'Cancel'),
            state.error ? h('span', { role: 'alert' }, state.error) : null),
          h('div', { style: { ...T.faint, fontSize: '0.75rem' } }, 'The current verdict stays on record next to any new one.'))
      : null)
}

// ---------------------------------------------------------------------------------------------
// the open (or latest) exercise
// ---------------------------------------------------------------------------------------------

function ExerciseCard({ ctx, study, exercise, draft, onChanged, onError, sendToTutor, tutorBusy }) {
  const open = exercise.state === 'awaiting_answer'
  const waiting = exercise.state === 'awaiting_check'
  const isLatest = study.latest_exercise_id === exercise.exercise_id
  const lastAttempt = exercise.attempts.length ? exercise.attempts[exercise.attempts.length - 1] : null
  const [answer, setAnswer] = useState(draft ? draft.answer : '')
  const [reasoning, setReasoning] = useState(draft ? draft.reasoning : '')
  const [save, setSave] = useState({ phase: 'idle', message: '' })
  const [submit, setSubmit] = useState({ phase: 'idle', message: '' })
  const [revealOpen, setRevealOpen] = useState(false)
  const [conflict, setConflict] = useState(null)
  const rev = useRef(draft ? draft.revision : 0)
  const dirty = useRef(false)
  const timer = useRef(null)
  const reqId = useRef(null)
  const submitting = useRef(false)
  const answerRef = useRef(null)

  // Adopt the server draft when it changes (retry prefill, another window) and the learner has no unsaved edits.
  useEffect(() => {
    if (!draft) return
    if (!dirty.current && draft.revision !== rev.current) {
      rev.current = draft.revision
      setAnswer(draft.answer)
      setReasoning(draft.reasoning)
    }
  }, [draft && draft.revision])

  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current) }, [])

  const saveNow = useCallback(async (a, r) => {
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null }
    setSave({ phase: 'saving', message: 'Saving…' })
    try {
      const out = await ctx.rest(`/studies/${study.study_id}/exercises/${exercise.exercise_id}/draft`, {
        method: 'PUT', body: { base_revision: rev.current, answer: a, reasoning: r } })
      rev.current = out.draft_revision
      dirty.current = false
      setSave({ phase: 'saved', message: 'Draft saved' })
    } catch (err) {
      const e = restError(err)
      if (e.code === 'draft_conflict') {
        setConflict(e.details)
        setSave({ phase: 'conflict', message: 'Changed in another window' })
      } else if (e.code === 'exercise_not_open') {
        setSave({ phase: 'idle', message: '' })
        onChanged()
      } else {
        setSave({ phase: 'failed', message: 'Draft not saved. Your text is still here.' })
      }
    }
  }, [study.study_id, exercise.exercise_id])

  const edit = (a, r) => {
    setAnswer(a)
    setReasoning(r)
    dirty.current = true
    setSave({ phase: 'pending', message: 'Unsaved changes' })
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => saveNow(a, r), SAVE_DEBOUNCE_MS)
  }

  const resolveConflict = keepMine => {
    if (!conflict) return
    rev.current = conflict.revision
    if (keepMine) {
      setConflict(null)
      saveNow(answer, reasoning)
    } else {
      setAnswer(conflict.answer || '')
      setReasoning(conflict.reasoning || '')
      dirty.current = false
      setConflict(null)
      setSave({ phase: 'saved', message: 'Loaded the other window’s draft' })
    }
  }

  const doSubmit = async () => {
    if (!answer.trim() || submitting.current) return
    submitting.current = true // synchronous guard: a double click must not start two submissions
    if (timer.current) { window.clearTimeout(timer.current); timer.current = null }
    if (!reqId.current) reqId.current = requestId()
    setSubmit({ phase: 'saving', message: 'Saving your answer…' })
    let receipt
    try {
      receipt = await ctx.rest(`/studies/${study.study_id}/exercises/${exercise.exercise_id}/attempts`, {
        method: 'POST', body: { client_request_id: reqId.current, answer, reasoning } })
    } catch (err) {
      submitting.current = false
      const e = restError(err)
      setSubmit({ phase: 'failed', message: `Not saved: ${e.message}` })
      if (e.code === 'exercise_not_open') onChanged()
      return
    }
    submitting.current = false
    reqId.current = null
    dirty.current = false
    setSubmit({ phase: 'saved', message: `Saved as attempt ${receipt.number} (${HELP[receipt.help_class]}).` })
    onChanged()
    if (!receipt.duplicate) {
      sendToTutor(`[Learning Workbench] I submitted attempt ${receipt.number} for exercise ${receipt.exercise_sequence}. Please check it.`)
    }
  }

  const action = async (path, body, after) => {
    try {
      const out = await ctx.rest(`/studies/${study.study_id}/exercises/${exercise.exercise_id}/${path}`, { method: 'POST', body: body || {} })
      if (after) after(out)
    } catch (err) {
      onError(restError(err).message)
    }
    onChanged()
  }

  const keyDown = e => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      doSubmit()
    }
  }

  const hintsLeft = exercise.hints_total - exercise.hints_shown
  const reasoningLabel = exercise.reasoning === 'requested' ? 'Your reasoning (requested)' : 'Your reasoning (optional)'
  const canRetry = isLatest && exercise.state === 'checked' && exercise.outcome !== 'correct'
  const canReveal = !exercise.solution_revealed && (open || exercise.state === 'checked' || exercise.state === 'skipped')

  return h('div', { style: { display: 'flex', flexDirection: 'column', gap: 10 }, 'data-lw': 'exercise', 'data-lw-exercise': exercise.exercise_id, 'data-lw-state': exercise.state },
    h(Row, null,
      h('span', { style: T.label }, `Exercise ${exercise.sequence} · ${exercise.kind}`),
      exercise.step_title ? h('span', { style: { ...T.faint, fontSize: '0.75rem' } }, exercise.step_title) : null),
    h('div', { style: { ...T.text, fontSize: '0.95rem' }, 'data-lw': 'prompt' }, exercise.prompt),
    exercise.hints.length
      ? h('ol', { style: { margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 4 }, 'data-lw': 'hints' },
          exercise.hints.map((hint, i) => h('li', { key: i, style: T.text }, h('span', { style: T.muted }, `Hint ${i + 1}: `), hint)))
      : null,
    exercise.solution_revealed && exercise.reference_answer
      ? h('div', { 'data-lw': 'solution' }, h(Label, null, 'Solution (revealed)'), h('div', { style: T.quote }, exercise.reference_answer))
      : null,
    open
      ? h(Fragment, null,
          h(Label, { htmlFor: 'lw-answer' }, 'Your answer'),
          h(Textarea, { id: 'lw-answer', ref: answerRef, value: answer, rows: 4, spellCheck: true, 'data-lw': 'answer',
                        onChange: e => edit(e.target.value, reasoning), onKeyDown: keyDown,
                        onBlur: () => { if (dirty.current) saveNow(answer, reasoning) } }),
          h(Label, { htmlFor: 'lw-reasoning' }, reasoningLabel),
          h(Textarea, { id: 'lw-reasoning', value: reasoning, rows: 3, spellCheck: true, 'data-lw': 'reasoning',
                        placeholder: 'How did you get there? A line or two is enough.',
                        onChange: e => edit(answer, e.target.value), onKeyDown: keyDown,
                        onBlur: () => { if (dirty.current) saveNow(answer, reasoning) } }),
          conflict
            ? h('div', { role: 'alert', style: { display: 'flex', flexDirection: 'column', gap: 6 } },
                h('div', null, 'This draft was changed in another window.'),
                h('div', { style: T.quote }, conflict.answer || '(empty)'),
                h(Row, null,
                  h(Button, { size: 'xs', onClick: () => resolveConflict(true) }, 'Keep mine'),
                  h(Button, { size: 'xs', variant: 'ghost', onClick: () => resolveConflict(false) }, 'Use the other version')))
            : null,
          h(Row, null,
            h(Button, { onClick: doSubmit, disabled: !answer.trim() || submit.phase === 'saving', 'data-lw': 'submit',
                        title: 'Submit (Ctrl+Enter)' }, submit.phase === 'saving' ? 'Saving…' : 'Submit'),
            hintsLeft > 0
              ? h(Button, { variant: 'outline', onClick: () => action('hint', { seen: exercise.hints_shown }), 'data-lw': 'hint' },
                  `Hint (${hintsLeft} left)`)
              : null,
            canReveal ? h(Button, { variant: 'ghost', onClick: () => setRevealOpen(true), 'data-lw': 'reveal' }, 'Reveal solution…') : null,
            h(Button, { variant: 'ghost', onClick: () => action('skip', null, () => sendToTutor(`[Learning Workbench] I skipped exercise ${exercise.sequence}.`)), 'data-lw': 'skip' }, 'Skip'),
            h('span', { style: { ...T.faint, fontSize: '0.75rem' }, 'data-lw-save': save.phase, role: save.phase === 'failed' ? 'alert' : undefined }, save.message)),
          submit.phase === 'failed' ? h('div', { role: 'alert', 'data-lw-submit': 'failed' }, submit.message) : null)
      : null,
    waiting
      ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: 6 }, 'data-lw': 'waiting' },
          lastAttempt ? h('div', null, h(Label, null, `Your attempt ${lastAttempt.number} · ${HELP[lastAttempt.help_class]}`),
            h('div', { style: T.quote }, lastAttempt.answer),
            lastAttempt.reasoning ? h('div', { style: { ...T.quote, ...T.muted } }, lastAttempt.reasoning) : null) : null,
          h(Row, null,
            h('span', { style: T.muted }, tutorBusy ? 'Hermes is replying…' : 'Waiting for the tutor’s check.'),
            !tutorBusy && lastAttempt
              ? h(Button, { size: 'xs', variant: 'ghost', 'data-lw': 'ask-again',
                            onClick: () => sendToTutor(`[Learning Workbench] Please check attempt ${lastAttempt.number} for exercise ${exercise.sequence}.`) },
                  'Ask the tutor to check')
              : null))
      : null,
    !open && !waiting && lastAttempt
      ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: 8 } },
          h('div', null, h(Label, null, `Your attempt ${lastAttempt.number}`), h('div', { style: T.quote }, lastAttempt.answer)),
          h(Verdicts, { ctx, study, exercise, attempt: lastAttempt, onChanged, sendToTutor }),
          h(Row, null,
            canRetry ? h(Button, { size: 'sm', onClick: () => action('retry', null, () => window.setTimeout(() => answerRef.current && answerRef.current.focus(), 50)), 'data-lw': 'retry' }, 'Try again') : null,
            canReveal ? h(Button, { size: 'sm', variant: 'ghost', onClick: () => setRevealOpen(true), 'data-lw': 'reveal' }, 'Reveal solution…') : null))
      : null,
    exercise.state === 'skipped' ? h('div', { style: T.muted }, 'You skipped this exercise.') : null,
    h(ConfirmDialog, {
      open: revealOpen,
      onClose: () => setRevealOpen(false),
      onConfirm: async () => { await action('reveal', { confirm: true }); setRevealOpen(false) },
      title: 'Reveal the solution?',
      description: 'You will see the tutor’s reference answer. Any later attempt on this exercise is recorded as “after reveal”, so it never counts as unaided.',
      confirmLabel: 'Reveal', cancelLabel: 'Keep trying',
    }))
}

// ---------------------------------------------------------------------------------------------
// earlier exercises, study actions
// ---------------------------------------------------------------------------------------------

function History({ ctx, study, current, onChanged, sendToTutor }) {
  const earlier = study.exercises.filter(e => e.exercise_id !== current)
  if (!earlier.length) return null
  return h('details', { style: T.section, 'data-lw': 'history' },
    h('summary', { style: { cursor: 'pointer', ...T.muted } }, `Earlier exercises (${earlier.length})`),
    h('div', { style: { display: 'flex', flexDirection: 'column', gap: 12, marginTop: 8 } },
      earlier.slice().reverse().map(ex => h('div', { key: ex.exercise_id, style: { display: 'flex', flexDirection: 'column', gap: 6 } },
        h('div', { style: T.label }, `Exercise ${ex.sequence} · ${ex.kind}${ex.state === 'skipped' ? ' · skipped' : ''}`),
        h('div', { style: T.text }, ex.prompt),
        ex.attempts.map(a => h('div', { key: a.attempt_id, style: { display: 'flex', flexDirection: 'column', gap: 4 } },
          h('div', { style: { ...T.faint, fontSize: '0.75rem' } }, `Attempt ${a.number} · ${HELP[a.help_class] || a.help_class}`),
          h('div', { style: T.quote }, a.answer),
          h(Verdicts, { ctx, study, exercise: ex, attempt: a, onChanged, sendToTutor })))))))
}

function StudyActions({ ctx, study, onChanged, onError, onDeleted }) {
  const [exported, setExported] = useState(null)
  const [busy, setBusy] = useState('')
  const [finishOpen, setFinishOpen] = useState(false)
  const [deleteReq, setDeleteReq] = useState(null)
  const doExport = async () => {
    setBusy('export')
    try {
      setExported(await ctx.rest(`/studies/${study.study_id}/export`, { method: 'POST', body: {} }))
    } catch (err) {
      onError(restError(err).message)
    } finally {
      setBusy('')
    }
  }
  const askDelete = async () => {
    try {
      setDeleteReq(await ctx.rest(`/studies/${study.study_id}/delete-request`, { method: 'POST', body: {} }))
    } catch (err) {
      onError(restError(err).message)
    }
  }
  return h('div', { style: { ...T.section, display: 'flex', flexDirection: 'column', gap: 8 } },
    h(Row, null,
      h(Button, { size: 'xs', variant: 'outline', onClick: doExport, disabled: busy === 'export', 'data-lw': 'export' },
        busy === 'export' ? 'Exporting…' : 'Export record'),
      study.status === 'active' ? h(Button, { size: 'xs', variant: 'ghost', onClick: () => setFinishOpen(true), 'data-lw': 'finish' }, 'Finish study…') : null,
      h(Button, { size: 'xs', variant: 'ghost', onClick: askDelete, 'data-lw': 'delete' }, 'Delete…')),
    exported
      ? h('div', { style: { display: 'flex', flexDirection: 'column', gap: 4 }, 'data-lw': 'exported' },
          h('div', { style: { ...T.muted, fontSize: '0.78rem', overflowWrap: 'anywhere' } }, `Saved Markdown record: ${exported.path}`),
          h(Row, null,
            ctx.os && ctx.os.revealPath ? h(Button, { size: 'xs', variant: 'ghost', onClick: () => ctx.os.revealPath(exported.path) }, 'Show file') : null,
            ctx.os && ctx.os.writeClipboard ? h(Button, { size: 'xs', variant: 'ghost', onClick: () => ctx.os.writeClipboard(exported.markdown) }, 'Copy Markdown') : null))
      : null,
    h('div', { style: { ...T.faint, fontSize: '0.72rem' } },
      'Stored locally in this Hermes profile. The material and submitted answers go to your configured model provider when the tutor reads them; drafts stay local.'),
    h(ConfirmDialog, {
      open: finishOpen, onClose: () => setFinishOpen(false),
      onConfirm: async () => {
        try { await ctx.rest(`/studies/${study.study_id}/archive`, { method: 'POST', body: {} }) } catch (err) { onError(restError(err).message) }
        setFinishOpen(false)
        onChanged()
      },
      title: 'Finish this study?', confirmLabel: 'Finish',
      description: 'It stays readable and exportable, and this chat can start a new study.',
    }),
    h(ConfirmDialog, {
      open: Boolean(deleteReq), onClose: () => setDeleteReq(null), destructive: true,
      onConfirm: async () => {
        try {
          await ctx.rest(`/studies/${study.study_id}/delete`, { method: 'POST', body: { confirm_token: deleteReq.confirm_token } })
          setDeleteReq(null)
          onDeleted()
        } catch (err) {
          setDeleteReq(null)
          onError(restError(err).message)
        }
      },
      title: 'Delete this study permanently?', confirmLabel: 'Delete',
      description: deleteReq ? `“${deleteReq.goal}”: ${deleteReq.exercises} exercise(s), ${deleteReq.attempts} attempt(s), ${deleteReq.checks} check(s) and ${deleteReq.exports} exported file(s). Other studies are not affected. This cannot be undone.` : '',
    }))
}

// ---------------------------------------------------------------------------------------------
// the pane
// ---------------------------------------------------------------------------------------------

function Workbench({ ctx }) {
  const storedId = useValue(host.state.focusedStoredSessionId || host.state.activeSessionId)
  const runtimeId = useValue(host.state.focusedSessionId || host.state.activeSessionId)
  const profile = useValue(host.state.profile)
  const busyMap = useValue(host.state.busyBySession) || {}
  const tutorBusy = Boolean(runtimeId && busyMap[runtimeId])
  const [view, setView] = useState({ loading: true, error: null, study: null, earlier: [] })
  const [notice, setNotice] = useState('')
  const [sendState, setSendState] = useState({ phase: 'idle', message: '' })
  const key = useRef(0)
  const scopeKey = `${profile || ''}|${storedId || ''}`

  const load = useCallback(async () => {
    const mine = key.current
    if (!storedId) {
      setView({ loading: false, error: null, study: null, earlier: [] })
      return
    }
    try {
      const res = await ctx.rest(`/session-study?session_id=${encodeURIComponent(storedId)}`)
      if (mine !== key.current) return // a late answer for a chat or profile we already left
      setView({ loading: false, error: null, study: res.study, earlier: res.earlier || [] })
    } catch (err) {
      if (mine !== key.current) return
      setView(v => ({ ...v, loading: false, error: restError(err).message }))
    }
  }, [scopeKey])

  useEffect(() => {
    key.current += 1
    setView({ loading: true, error: null, study: null, earlier: [] })
    setNotice('')
    setSendState({ phase: 'idle', message: '' })
    load()
    const stopPoll = every(ctx, POLL_MS, load)
    const stopEvent = listen(ctx, EVENT, () => load())
    return () => { stopPoll(); stopEvent() }
  }, [load])

  // Start a normal chat turn with the learner's message, exactly as the composer does. `queued` holds it
  // until a running turn finishes instead of redirecting that turn.
  const sendToTutor = useCallback(text => {
    const target = host.state.focusedSessionId ? host.state.focusedSessionId.get() : runtimeId
    if (!target) {
      setSendState({ phase: 'unsent', message: 'Saved. Open the chat and send any message so Hermes can check it.' })
      return
    }
    setSendState({ phase: 'sent', message: 'Sent to Hermes.' })
    Promise.resolve(host.request('prompt.submit', { session_id: target, text, queued: true })).catch(() => {
      setSendState({ phase: 'unsent', message: 'Saved, but Hermes was not asked. Use “Ask the tutor to check” or send a message.' })
    })
  }, [runtimeId])

  const onError = msg => setNotice(msg)
  const study = view.study

  let body
  if (!storedId) {
    body = h('div', { style: T.muted }, 'Open a chat to use the Learning Workbench.')
  } else if (view.loading) {
    body = h('div', { style: T.muted, 'aria-busy': 'true' }, 'Loading…')
  } else if (view.error && !study) {
    body = h('div', { role: 'alert', style: { display: 'flex', flexDirection: 'column', gap: 8 } },
      h('div', null, 'The Workbench could not load.'), h('div', { style: T.muted }, view.error),
      h(Row, null, h(Button, { size: 'sm', onClick: load }, 'Retry')))
  } else if (!study) {
    body = h(Fragment, null,
      h(StartForm, { ctx, storedId, onStarted: s => setView(v => ({ ...v, study: s })), sendToTutor }),
      view.earlier.length ? h('div', { style: { ...T.section, ...T.muted, fontSize: '0.8rem' } },
        `Finished studies in this chat: ${view.earlier.map(s => s.goal).join(' · ')}`) : null)
  } else {
    const open = study.open_exercise_id ? study.exercises.find(e => e.exercise_id === study.open_exercise_id) : null
    const current = open || (study.latest_exercise_id ? study.exercises.find(e => e.exercise_id === study.latest_exercise_id) : null)
    body = h(Fragment, null,
      h('div', { style: { display: 'flex', flexDirection: 'column', gap: 4 } },
        h(Label, null, study.status === 'active' ? 'Goal' : 'Goal (finished)'),
        h('div', { style: { ...T.text, fontWeight: 600 }, 'data-lw': 'goal-text' }, study.goal),
        study.material.chars
          ? h('div', { style: { ...T.faint, fontSize: '0.75rem' } }, `Material: ${study.material.title} · ${study.material.chars.toLocaleString()} characters`)
          : null),
      h('div', { style: T.section }, h(Label, null, 'Route'), h(RouteList, { ctx, study, onChanged: load, onError })),
      h('div', { style: T.section },
        current
          ? h(ExerciseCard, { key: current.exercise_id, ctx, study, exercise: current,
                              draft: study.draft && study.draft.exercise_id === current.exercise_id ? study.draft : null,
                              onChanged: load, onError, sendToTutor, tutorBusy })
          : h('div', { style: T.muted }, tutorBusy ? 'Hermes is replying…' : 'No exercise yet. The tutor will pose one here.')),
      sendState.message ? h('div', { style: { ...T.muted, fontSize: '0.8rem' }, 'data-lw-send': sendState.phase, role: sendState.phase === 'unsent' ? 'alert' : undefined }, sendState.message) : null,
      h(History, { ctx, study, current: current && current.exercise_id, onChanged: load, sendToTutor }),
      h(StudyActions, { ctx, study, onChanged: load, onError, onDeleted: () => { setNotice('Study deleted.'); load() } }))
  }

  return h('div', { 'data-lw-root': '1', style: { display: 'flex', flexDirection: 'column', gap: 12, padding: 12, height: '100%', overflowY: 'auto', fontSize: '0.875rem', color: 'var(--ui-text-primary)' } },
    notice ? h('div', { role: 'status', style: { display: 'flex', gap: 8, alignItems: 'baseline', ...T.muted } },
      h('span', { style: { flex: 1 } }, notice), h(Button, { size: 'xs', variant: 'ghost', onClick: () => setNotice('') }, 'Dismiss')) : null,
    view.error && study ? h('div', { role: 'status', style: { ...T.faint, fontSize: '0.75rem' } }, 'Reconnecting… showing the last loaded state.') : null,
    body)
}

export default {
  id: PLUGIN_ID,
  name: 'Learning Workbench',
  register(ctx) {
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Learning Workbench',
      data: { placement: 'right', width: '400px' },
      render: () => jsx(Workbench, { ctx }),
    })
  },
}
