import { createContext, useCallback, useContext, useEffect, useId, useRef, useState } from 'react'
import logoOnDark from './assets/vipcon-logo-light.png'
import logoOnLight from './assets/vipcon-logo-dark.png'
import { Sun, Moon, X, Alert, Info, CheckCircle, Eye } from './icons.jsx'
import { initials, hue } from './format.js'

// ---------------------------------------------------------------- theme

const ThemeContext = createContext({ theme: 'dark', toggle: () => {} })

function initialTheme() {
  const saved = localStorage.getItem('vipcon-theme')
  if (saved === 'dark' || saved === 'light') return saved
  return window.matchMedia?.('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(initialTheme)
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])
  const toggle = useCallback(() => {
    setTheme((t) => {
      const next = t === 'dark' ? 'light' : 'dark'
      localStorage.setItem('vipcon-theme', next)
      return next
    })
  }, [])
  return <ThemeContext.Provider value={{ theme, toggle }}>{children}</ThemeContext.Provider>
}

export const useTheme = () => useContext(ThemeContext)

export function ThemeToggle({ className = 'btn btn-ghost btn-icon' }) {
  const { theme, toggle } = useTheme()
  const label = theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'
  return (
    <button type="button" className={className} onClick={toggle} title={label} aria-label={label}>
      {theme === 'dark' ? <Sun /> : <Moon />}
    </button>
  )
}

export function Logo({ large = false, onDark = false }) {
  const { theme } = useTheme()
  const src = onDark || theme === 'dark' ? logoOnDark : logoOnLight
  return <img className={`logo ${large ? 'logo-lg' : ''}`} src={src} alt="VIPCON" />
}

// ---------------------------------------------------------------- basics

export function Button({ variant = 'secondary', size, block, icon, loading, children, className = '', ...rest }) {
  const cls = ['btn', variant !== 'secondary' && `btn-${variant}`, size && `btn-${size}`, block && 'btn-block',
    !children && 'btn-icon', className].filter(Boolean).join(' ')
  return (
    <button type="button" className={cls} disabled={loading || rest.disabled} {...rest}>
      {loading ? <span className="spinner" /> : icon}
      {children}
    </button>
  )
}

export function Spinner({ large }) {
  return <span className={`spinner ${large ? 'spinner-lg' : ''}`} role="status" aria-label="Loading" />
}

export function Field({ label, optional, hint, error, children, htmlFor }) {
  return (
    <div className="field">
      {label && (
        <label className="field-label" htmlFor={htmlFor}>
          <span>{label}</span>
          {optional && <span className="opt">Optional</span>}
        </label>
      )}
      {children}
      {error ? <span className="field-error" role="alert">{error}</span> : hint && <span className="field-hint">{hint}</span>}
    </div>
  )
}

export function TextInput({ label, optional, hint, error, className = '', ...rest }) {
  const id = useId()
  return (
    <Field label={label} optional={optional} hint={hint} error={error} htmlFor={id}>
      <input id={id} className={`input ${className}`} aria-invalid={error ? 'true' : undefined} {...rest} />
    </Field>
  )
}

export function PasswordInput({ label, hint, error, ...rest }) {
  const id = useId()
  const [show, setShow] = useState(false)
  return (
    <Field label={label} hint={hint} error={error} htmlFor={id}>
      <div className="input-group">
        <input id={id} className="input" type={show ? 'text' : 'password'} aria-invalid={error ? 'true' : undefined} {...rest} />
        <button type="button" className="btn btn-ghost btn-sm btn-icon input-addon" onClick={() => setShow((s) => !s)}
                aria-label={show ? 'Hide password' : 'Show password'} title={show ? 'Hide' : 'Show'}>
          <Eye off={show} size={16} />
        </button>
      </div>
    </Field>
  )
}

export function Switch({ checked, onChange, label, description, disabled }) {
  const id = useId()
  return (
    <div className="switch-row">
      <div className="switch-text">
        <label className="switch-title" htmlFor={id}>{label}</label>
        {description && <span className="switch-desc">{description}</span>}
      </div>
      <button id={id} type="button" role="switch" aria-checked={checked} className="switch" disabled={disabled}
              onClick={() => onChange(!checked)} />
    </div>
  )
}

export function Choice({ value, onChange, options }) {
  return (
    <div className="choice-group" role="radiogroup">
      {options.map((o) => (
        <div key={o.value} className="choice" role="radio" aria-checked={value === o.value} tabIndex={0}
             onClick={() => onChange(o.value)}
             onKeyDown={(e) => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); onChange(o.value) } }}>
          <span className="choice-dot" />
          <span>
            <span className="choice-title">{o.label}</span>
            <span className="choice-desc" style={{ display: 'block' }}>{o.description}</span>
          </span>
        </div>
      ))}
    </div>
  )
}

export function Notice({ kind = 'info', children }) {
  const icon = kind === 'success' ? <CheckCircle size={16} /> : kind === 'info' ? <Info size={16} /> : <Alert size={16} />
  return <div className={`alert alert-${kind}`} role={kind === 'error' ? 'alert' : undefined}>{icon}<div>{children}</div></div>
}

export function Avatar({ name, size = 32 }) {
  return (
    <span className="avatar" style={{ '--s': `${size}px`, '--hue': hue(name) }} aria-hidden="true">
      {initials(name)}
    </span>
  )
}

export function EmptyState({ icon, title, children, action }) {
  return (
    <div className="empty">
      {icon && <div className="empty-icon">{icon}</div>}
      <div className="empty-title">{title}</div>
      {children && <div>{children}</div>}
      {action}
    </div>
  )
}

// ---------------------------------------------------------------- menus and dialogs

function useDismiss(open, onClose, ref) {
  useEffect(() => {
    if (!open) return
    const key = (e) => { if (e.key === 'Escape') onClose() }
    const click = (e) => { if (ref?.current && !ref.current.contains(e.target)) onClose() }
    document.addEventListener('keydown', key)
    document.addEventListener('mousedown', click)
    return () => {
      document.removeEventListener('keydown', key)
      document.removeEventListener('mousedown', click)
    }
  }, [open, onClose, ref])
}

export function Menu({ trigger, children, up = false }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  const close = useCallback(() => setOpen(false), [])
  useDismiss(open, close, ref)
  return (
    <div className="menu-wrap" ref={ref}>
      {trigger({ open, toggle: () => setOpen((o) => !o) })}
      {open && (
        <div className={`menu ${up ? 'menu-up' : ''}`} role="menu" onClick={close}>
          {children}
        </div>
      )}
    </div>
  )
}

export function MenuItem({ icon, danger, children, ...rest }) {
  return (
    <button type="button" role="menuitem" className={`menu-item ${danger ? 'danger' : ''}`} {...rest}>
      {icon}{children}
    </button>
  )
}

export function Modal({ open, onClose, title, description, children, footer, size }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const key = (e) => { if (e.key === 'Escape') onClose?.() }
    document.addEventListener('keydown', key)
    const first = ref.current?.querySelector('input, select, textarea, button:not(.modal-close)')
    first?.focus()
    return () => document.removeEventListener('keydown', key)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose?.() }}>
      <div className={`modal ${size === 'sm' ? 'modal-sm' : ''}`} role="dialog" aria-modal="true" aria-label={title} ref={ref}>
        <div className="modal-head">
          <div>
            <h2 className="modal-title">{title}</h2>
            {description && <p className="modal-desc">{description}</p>}
          </div>
          {onClose && (
            <button type="button" className="btn btn-ghost btn-sm btn-icon modal-close" onClick={onClose} aria-label="Close">
              <X size={16} />
            </button>
          )}
        </div>
        {children && <div className="modal-body">{children}</div>}
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

// confirm({ title, message, confirmLabel, danger }) -> Promise<boolean>
const ConfirmContext = createContext(async () => false)

export function ConfirmProvider({ children }) {
  const [state, setState] = useState(null)
  const confirm = useCallback((opts) => new Promise((resolve) => setState({ ...opts, resolve })), [])
  const done = (ok) => {
    state?.resolve(ok)
    setState(null)
  }
  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Modal open={!!state} onClose={() => done(false)} title={state?.title} description={state?.message} size="sm"
             footer={<>
               <Button onClick={() => done(false)}>Cancel</Button>
               <Button variant={state?.danger ? 'danger' : 'primary'} onClick={() => done(true)}>
                 {state?.confirmLabel || 'Confirm'}
               </Button>
             </>} />
    </ConfirmContext.Provider>
  )
}

export const useConfirm = () => useContext(ConfirmContext)

// toast({ kind: 'success'|'error'|'info', message, action: { label, onClick }, duration })
const ToastContext = createContext(() => {})

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])
  const dismiss = useCallback((id) => setToasts((t) => t.filter((x) => x.id !== id)), [])
  const toast = useCallback((t) => {
    const id = Math.random().toString(36).slice(2)
    setToasts((list) => [...list.filter((x) => !t.key || x.key !== t.key), { kind: 'success', ...t, id }].slice(-4))
    if (t.duration !== 0) setTimeout(() => dismiss(id), t.duration || 4000)
    return id
  }, [dismiss])
  return (
    <ToastContext.Provider value={toast}>
      {children}
      <div className="toasts" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            {t.kind === 'error' ? <Alert size={18} /> : t.kind === 'info' ? <Info size={18} /> : <CheckCircle size={18} />}
            <div className="toast-body">
              <span>{t.message}</span>
              {t.actions && (
                <div className="toast-actions">
                  {t.actions.map((a) => (
                    <Button key={a.label} size="sm" variant={a.primary ? 'primary' : 'secondary'}
                            onClick={() => { a.onClick(); dismiss(t.id) }}>{a.label}</Button>
                  ))}
                </div>
              )}
            </div>
            <button type="button" className="btn btn-ghost btn-sm btn-icon" onClick={() => dismiss(t.id)} aria-label="Dismiss">
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export const useToast = () => useContext(ToastContext)

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Plain-HTTP pages (e.g. the device's IP address) have no clipboard API.
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    return ok
  }
}

// ---------------------------------------------------------------- layout for pages outside the console

export function PublicLayout({ children, right, footer = true }) {
  return (
    <div className="public">
      <header className="public-top">
        <Logo />
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          {right}
          <ThemeToggle />
        </div>
      </header>
      <main className="public-main">{children}</main>
      {footer && <footer className="public-foot">Private meetings, hosted on your own VIPCON device</footer>}
    </div>
  )
}
