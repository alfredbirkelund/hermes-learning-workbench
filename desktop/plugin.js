// Learning Workbench — Desktop pane (spike build).
// Only @hermes/plugin-sdk, react and react/jsx-runtime are imported (Desktop plugin rules).
import { host, useValue, Button, Textarea } from '@hermes/plugin-sdk'
import { useCallback, useEffect, useRef, useState } from 'react'
import { jsx, jsxs, Fragment } from 'react/jsx-runtime'

const PLUGIN_ID = 'learning-workbench'
const EVENT = `plugin.${PLUGIN_ID}.study.changed`

function storedAtom() {
  return host.state.focusedStoredSessionId || host.state.activeSessionId
}

function runtimeAtom() {
  return host.state.focusedSessionId || host.state.activeSessionId
}

function newRequestId() {
  if (globalThis.crypto && typeof globalThis.crypto.randomUUID === 'function') return globalThis.crypto.randomUUID()
  return `req-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`
}

const muted = { color: 'var(--ui-text-tertiary)' }
const label = { fontSize: '0.75rem', color: 'var(--ui-text-tertiary)', marginBottom: 4 }
const block = { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', lineHeight: 1.5 }

function Workbench({ ctx }) {
  const storedId = useValue(storedAtom())
  const [state, setState] = useState({ loading: true, error: null, study: null })
  const [answer, setAnswer] = useState('')
  const [reasoning, setReasoning] = useState('')
  const [saveState, setSaveState] = useState('idle')
  const [submitState, setSubmitState] = useState({ phase: 'idle', message: '' })
  const draftRev = useRef(0)
  const draftFor = useRef(null)
  const dirty = useRef(false)
  const requestId = useRef(null)
  const saveTimer = useRef(null)
  const viewKey = useRef(0)

  const load = useCallback(async () => {
    const key = ++viewKey.current
    if (!storedId) {
      setState({ loading: false, error: null, study: null })
      return
    }
    try {
      const res = await ctx.rest(`/session-study?session_id=${encodeURIComponent(storedId)}`)
      if (key !== viewKey.current) return
      setState({ loading: false, error: null, study: res.study })
      const draft = res.study && res.study.draft
      if (draft && (draftFor.current !== draft.exercise_id || !dirty.current)) {
        draftFor.current = draft.exercise_id
        draftRev.current = draft.revision
        setAnswer(draft.answer)
        setReasoning(draft.reasoning)
      }
    } catch (err) {
      if (key !== viewKey.current) return
      setState(s => ({ ...s, loading: false, error: String((err && err.message) || err) }))
    }
  }, [storedId])

  useEffect(() => {
    dirty.current = false
    draftFor.current = null
    setAnswer('')
    setReasoning('')
    setState({ loading: true, error: null, study: null })
    load()
    const stopTimer = ctx.setInterval(load, 4000)
    const stopEvent = ctx.onEvent(EVENT, () => load())
    return () => {
      stopTimer()
      stopEvent()
    }
  }, [load])

  const study = state.study
  const open = study && study.open_exercise_id ? study.exercises.find(e => e.exercise_id === study.open_exercise_id) : null

  const saveDraft = useCallback(async (nextAnswer, nextReasoning) => {
    if (!study || !open || open.state !== 'awaiting_answer') return
    setSaveState('saving')
    try {
      const res = await ctx.rest(`/studies/${study.study_id}/exercises/${open.exercise_id}/draft`, {
        method: 'PUT',
        body: { base_revision: draftRev.current, answer: nextAnswer, reasoning: nextReasoning }
      })
      draftRev.current = res.draft_revision
      dirty.current = false
      setSaveState('saved')
    } catch (err) {
      setSaveState('failed')
    }
  }, [study && study.study_id, open && open.exercise_id, open && open.state])

  const onEdit = (nextAnswer, nextReasoning) => {
    setAnswer(nextAnswer)
    setReasoning(nextReasoning)
    dirty.current = true
    setSaveState('pending')
    if (saveTimer.current) saveTimer.current()
    saveTimer.current = ctx.setTimeout(() => saveDraft(nextAnswer, nextReasoning), 700)
  }

  const submit = async () => {
    if (!study || !open || !answer.trim()) return
    if (!requestId.current) requestId.current = newRequestId()
    setSubmitState({ phase: 'saving', message: 'Saving your answer…' })
    let receipt
    try {
      receipt = await ctx.rest(`/studies/${study.study_id}/exercises/${open.exercise_id}/attempts`, {
        method: 'POST',
        body: { client_request_id: requestId.current, answer, reasoning }
      })
    } catch (err) {
      setSubmitState({ phase: 'failed', message: 'Not saved. Your text is still here; try again.' })
      return
    }
    requestId.current = null
    dirty.current = false
    setSubmitState({ phase: 'saved', message: `Saved as attempt ${receipt.number} (${receipt.help_class}). Asking the tutor to check…` })
    const runtimeId = runtimeAtom().get()
    const text = `[Learning Workbench] I submitted attempt ${receipt.number} for exercise ${open.sequence} (attempt id ${receipt.attempt_id}). Please check it.`
    try {
      await host.request('prompt.submit', { session_id: runtimeId, text })
      setSubmitState({ phase: 'sent', message: `Attempt ${receipt.number} saved. The tutor is checking it.` })
    } catch (err) {
      setSubmitState({ phase: 'unsent', message: `Attempt ${receipt.number} saved, but the tutor was not asked yet.` })
    }
    load()
  }

  if (!storedId) {
    return jsx('div', { className: 'p-3 text-sm', style: muted, children: 'Open a chat to use the Learning Workbench.' })
  }
  if (state.loading) return jsx('div', { className: 'p-3 text-sm', style: muted, children: 'Loading…' })
  if (state.error) {
    return jsxs('div', { className: 'p-3 text-sm', children: [
      jsx('div', { children: 'The workbench could not load.' }),
      jsx('div', { style: muted, children: state.error }),
      jsx(Button, { onClick: load, children: 'Retry' })
    ] })
  }
  if (!study) {
    return jsx('div', { className: 'p-3 text-sm', style: muted,
      children: 'No study in this chat yet. Ask Hermes to start a Learning Workbench study with your goal and material.' })
  }

  const saveLabel = { idle: '', pending: 'Unsaved changes', saving: 'Saving…', saved: 'Draft saved', failed: 'Draft not saved' }[saveState]

  return jsxs('div', { className: 'flex h-full flex-col gap-3 overflow-auto p-3 text-sm', 'data-lw-root': '1', children: [
    jsxs('div', { children: [jsx('div', { style: label, children: 'Goal' }), jsx('div', { style: block, children: study.goal })] }),
    open ? jsxs(Fragment, { children: [
      jsxs('div', { 'data-lw-exercise': open.exercise_id, children: [
        jsx('div', { style: label, children: `Exercise ${open.sequence} · ${open.kind}` }),
        jsx('div', { style: block, children: open.prompt })
      ] }),
      open.state === 'awaiting_answer' ? jsxs(Fragment, { children: [
        jsx(Textarea, { 'aria-label': 'Your answer', 'data-lw-answer': '1', value: answer, rows: 4,
          onChange: e => onEdit(e.target.value, reasoning), placeholder: 'Your answer' }),
        jsx(Textarea, { 'aria-label': 'Your reasoning (optional)', 'data-lw-reasoning': '1', value: reasoning, rows: 3,
          onChange: e => onEdit(answer, e.target.value), placeholder: 'Your reasoning (optional)' }),
        jsxs('div', { className: 'flex items-center gap-2', children: [
          jsx(Button, { 'data-lw-submit': '1', disabled: !answer.trim() || submitState.phase === 'saving', onClick: submit, children: 'Submit' }),
          jsx('span', { style: muted, 'data-lw-save-state': saveState, children: saveLabel })
        ] })
      ] }) : jsx('div', { style: muted, children: 'Submitted. Waiting for the tutor’s check.' })
    ] }) : jsx('div', { style: muted, children: 'No open exercise. The tutor will pose one.' }),
    submitState.message ? jsx('div', { 'data-lw-submit-state': submitState.phase, style: muted, children: submitState.message }) : null
  ] })
}

export default {
  id: PLUGIN_ID,
  name: 'Learning Workbench',
  register(ctx) {
    ctx.register({
      id: 'pane',
      area: 'panes',
      title: 'Learning Workbench',
      data: { placement: 'right', width: '380px' },
      render: () => jsx(Workbench, { ctx })
    })
  }
}
