// Stand-in for @hermes/plugin-sdk in the pane harness. Components render native elements with the same props
// the real kit takes; host state uses nanostores atoms like the real SDK. This is a logic harness, not a
// visual check: the rendered Desktop is inspected separately.
import { atom } from 'nanostores'
import { useStore } from '@nanostores/react'
import { jsx, jsxs } from 'react/jsx-runtime'

export const useValue = useStore

const handlers = new Map()
export const requests = []

export const host = {
  state: {
    activeSessionId: atom(null),
    focusedSessionId: atom(null),
    focusedStoredSessionId: atom(null),
    profile: atom('default'),
    busyBySession: atom({}),
  },
  request: async (method, params) => {
    requests.push({ method, params })
    if (host.failRequests) throw new Error('gateway unavailable')
    return { ok: true }
  },
  onEvent: (type, fn) => {
    if (!handlers.has(type)) handlers.set(type, new Set())
    handlers.get(type).add(fn)
    return () => handlers.get(type).delete(fn)
  },
  failRequests: false,
}

export function fire(type, payload) {
  for (const fn of handlers.get(type) || []) fn(payload)
}

function strip(props) {
  const { variant, size, ...rest } = props
  return rest
}

export function Button(props) {
  return jsx('button', { type: 'button', ...strip(props) })
}

export function Input(props) {
  return jsx('input', strip(props))
}

export function Textarea(props) {
  return jsx('textarea', strip(props))
}

export function Badge(props) {
  return jsx('span', { 'data-badge': props.variant || 'default', children: props.children })
}

export function ConfirmDialog({ open, onClose, onConfirm, title, description, confirmLabel = 'Confirm', cancelLabel = 'Cancel', destructive }) {
  if (!open) return null
  return jsxs('div', { role: 'dialog', 'data-destructive': destructive ? '1' : '0', children: [
    jsx('h2', { children: title }),
    description ? jsx('p', { children: description }) : null,
    jsx('button', { type: 'button', 'data-dialog': 'confirm', onClick: () => onConfirm(), children: confirmLabel }),
    jsx('button', { type: 'button', 'data-dialog': 'cancel', onClick: () => onClose(), children: cancelLabel }),
  ] })
}
