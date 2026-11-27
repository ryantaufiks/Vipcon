// Two-factor sign-in (TOTP): set up with an authenticator app, switch off, the enrollment gate the
// device shows when its policy requires two-factor for admins and hosts, and the policy switch.
import { useEffect, useState } from 'react'
import { totpSetup, totpEnable, totpDisable, getSettings, saveSettings } from './api.js'
import { Button, TextInput, PasswordInput, Notice, Modal, Switch, Spinner, Logo, ThemeToggle, useToast, copyText } from './ui.jsx'
import { Copy, Lock } from './icons.jsx'
import { friendly } from './format.js'

function svgSrc(svg) {
  return 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svg)))
}

function CodeInput({ value, onChange, error, label = 'Code from your authenticator app', autoFocus }) {
  return (
    <TextInput label={label} value={value} onChange={(e) => onChange(e.target.value.replace(/[^\d ]/g, '').slice(0, 7))}
               inputMode="numeric" autoComplete="one-time-code" placeholder="123 456" className="input-mono"
               maxLength={7} error={error} autoFocus={autoFocus} />
  )
}

// Scan, then confirm a code. Calls onDone once two-factor is on.
export function TwoFactorSetup({ token, onDone, onCancel, cancelLabel = 'Cancel' }) {
  const toast = useToast()
  const [data, setData] = useState(null)
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let alive = true
    totpSetup(token).then((d) => alive && setData(d)).catch((err) => alive && setError(friendly(err)))
    return () => { alive = false }
  }, [token])

  async function submit(e) {
    e.preventDefault()
    if (code.replace(/\D/g, '').length !== 6) { setError('Enter the 6-digit code.'); return }
    setBusy(true)
    setError('')
    try {
      await totpEnable(token, code)
      toast({ message: 'Two-factor sign-in is on.' })
      onDone()
    } catch (err) {
      setError(friendly(err))
    } finally {
      setBusy(false)
    }
  }

  if (!data) return error ? <Notice kind="error">{error}</Notice> : <div className="empty"><Spinner large /></div>
  return (
    <form className="form" onSubmit={submit} noValidate>
      <ol className="steps-mini">
        <li>Open an authenticator app on your phone (Google Authenticator, Microsoft Authenticator, 1Password…).</li>
        <li>Scan this code, or enter the setup key by hand.</li>
        <li>Type the 6-digit code the app shows.</li>
      </ol>
      <div className="totp-qr"><img src={svgSrc(data.qr_svg)} alt="QR code for your authenticator app" width="200" height="200" /></div>
      <div className="copy-field">
        <input className="input input-mono" readOnly value={data.secret.replace(/(.{4})/g, '$1 ').trim()} aria-label="Setup key"
               onFocus={(e) => e.target.select()} />
        <Button icon={<Copy size={16} />} aria-label="Copy setup key" onClick={async () => {
          const ok = await copyText(data.secret)
          toast(ok ? { message: 'Setup key copied.' } : { kind: 'error', message: 'Could not copy. Select the key and copy it.' })
        }} />
      </div>
      <CodeInput value={code} onChange={setCode} />
      {error && <Notice kind="error">{error}</Notice>}
      <div className="kit-actions" style={{ justifyContent: 'flex-end' }}>
        {onCancel && <Button onClick={onCancel}>{cancelLabel}</Button>}
        <Button variant="primary" type="submit" loading={busy}>Turn on</Button>
      </div>
    </form>
  )
}

// Account menu → Two-factor sign-in.
export function TwoFactorModal({ open, onClose, token, me, onChanged }) {
  const toast = useToast()
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [required, setRequired] = useState(false)

  useEffect(() => {
    if (!open) return
    setPassword('')
    setCode('')
    setError('')
    getSettings(token).then((s) => setRequired(!!s.require_2fa && ['admin', 'host'].includes(me.role))).catch(() => {})
  }, [open, token, me.role])

  async function disable(e) {
    e.preventDefault()
    if (!password || code.replace(/\D/g, '').length !== 6) { setError('Enter your password and a current code.'); return }
    setBusy(true)
    setError('')
    try {
      await totpDisable(token, password, code)
      toast({ message: 'Two-factor sign-in is off.' })
      onChanged()
      onClose()
    } catch (err) {
      setError(friendly(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Two-factor sign-in"
           description={me.totp_enabled ? 'On: signing in needs your password and a code from your phone.'
             : 'Add a code from your phone to every sign-in, so a stolen password alone is not enough.'}>
      {!open ? null : me.totp_enabled ? (
        required ? (
          <Notice kind="info">Two-factor sign-in is required for your role on this device, so it cannot be switched off. If you change phones, ask a Client Admin to reset it, then set it up again.</Notice>
        ) : (
          <form className="form" onSubmit={disable} noValidate>
            <PasswordInput label="Password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
            <CodeInput value={code} onChange={setCode} />
            {error && <Notice kind="error">{error}</Notice>}
            <div className="kit-actions" style={{ justifyContent: 'flex-end' }}>
              <Button onClick={onClose}>Cancel</Button>
              <Button variant="danger" type="submit" loading={busy}>Turn off</Button>
            </div>
          </form>
        )
      ) : (
        <TwoFactorSetup token={token} onCancel={onClose} onDone={() => { onChanged(); onClose() }} />
      )}
    </Modal>
  )
}

// Shown instead of the console while the policy requires two-factor and this user has not set it up.
export function TwoFactorGate({ token, onDone, onSignOut }) {
  return (
    <div className="public">
      <header className="public-top"><span /><ThemeToggle /></header>
      <main className="public-main">
        <div className="public-card" style={{ maxWidth: 460 }}>
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 24 }}><Logo large /></div>
          <div className="card card-pad form">
            <div>
              <h1 className="page-title">Set up two-factor sign-in</h1>
              <p className="page-sub">Your organisation requires it for admins and hosts on this device. It takes a minute.</p>
            </div>
            <TwoFactorSetup token={token} onDone={onDone} onCancel={onSignOut} cancelLabel="Sign out" />
          </div>
        </div>
      </main>
    </div>
  )
}

// Security page card: the policy.
export function TwoFactorPolicy({ token, fail, me, onChanged }) {
  const toast = useToast()
  const [on, setOn] = useState(null)
  useEffect(() => { getSettings(token).then((s) => setOn(!!s.require_2fa)).catch(fail) }, [token, fail])

  async function toggle(value) {
    setOn(value)
    try {
      await saveSettings(token, { require_2fa: value })
      toast({ message: value ? 'Two-factor sign-in is now required for admins and hosts.' : 'Two-factor sign-in is now optional.' })
      onChanged()
    } catch (err) {
      setOn(!value)
      fail(err)
    }
  }

  return (
    <div className="card card-pad form" style={{ marginTop: 16 }}>
      <div className="sec-head">
        <Lock size={20} />
        <div>
          <h2 className="sec-title">Two-factor sign-in</h2>
          <p className="muted" style={{ margin: 0 }}>A 6-digit code from a phone app at every sign-in. Each person sets it up from the account menu; a Client Admin can reset it for someone who loses their phone.</p>
        </div>
      </div>
      <Notice kind={me.totp_enabled ? 'success' : 'warning'}>
        {me.totp_enabled ? 'On for your account.' : 'Off for your account. Turn it on from the account menu (⚙ → Two-factor sign-in).'}
      </Notice>
      {on === null ? <Spinner /> : (
        <Switch checked={on} onChange={toggle} label="Require for admins and hosts"
                description="They are asked to set it up at their next sign-in and cannot use the console until they do." />
      )}
    </div>
  )
}
