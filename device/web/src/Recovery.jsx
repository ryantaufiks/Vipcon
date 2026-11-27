// Passwords and account recovery: the Recovery Kit, "Forgot password", change password, an admin
// resetting someone else's password, and the Security page (kit status, device-screen reset switch,
// recent security events). The device app enforces every rule; these screens only explain them.
import { useCallback, useEffect, useState } from 'react'
import {
  changePassword, newRecoveryCodes, recoveryReset, recoveryStatus, resetUserPassword, saveSettings, screenReset,
  securityEvents, getSettings,
} from './api.js'
import { Button, TextInput, PasswordInput, Notice, Modal, Switch, Spinner, useToast, copyText } from './ui.jsx'
import { Copy, Download, Shield, Monitor, Refresh, Clock } from './icons.jsx'
import { friendly } from './format.js'

const MIN_PASSWORD = 10

function passwordProblem(username, password, confirm) {
  if (password.length < MIN_PASSWORD) return { password: `At least ${MIN_PASSWORD} characters.` }
  if (username && password.toLowerCase() === username.toLowerCase()) return { password: 'The password must not be the username.' }
  if (confirm !== undefined && password !== confirm) return { confirm: 'The passwords do not match.' }
  return null
}

function kitText(codes, username) {
  const when = new Date().toLocaleString()
  return [
    'VIPCON Recovery Kit',
    `Account: ${username}`,
    `Created: ${when}`,
    '',
    'Each code can be used once to set a new password for this Client Admin account',
    'if the password is forgotten (sign-in page > Forgot password > Recovery code).',
    'Keep this somewhere safe and private, such as a password manager or a safe.',
    '',
    ...codes.map((c, i) => `${String(i + 1).padStart(2, ' ')}. ${c}`),
    '',
  ].join('\n')
}

// ---------------------------------------------------------------- Recovery Kit

export function RecoveryKit({ codes, username, onDone, doneLabel = 'Continue' }) {
  const toast = useToast()
  const [saved, setSaved] = useState(false)

  async function copyAll() {
    const ok = await copyText(kitText(codes, username))
    toast(ok ? { message: 'Recovery codes copied.' } : { kind: 'error', message: 'Could not copy. Download the kit instead.' })
  }

  function download() {
    const url = URL.createObjectURL(new Blob([kitText(codes, username)], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = `vipcon-recovery-kit-${username}.txt`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return (
    <div className="form">
      <Notice kind="warning">
        Save these codes now. They are shown only once. If you forget your password, one code lets you set a new one.
      </Notice>
      <ol className="codes" aria-label="Recovery codes">
        {codes.map((c) => <li key={c}><code>{c}</code></li>)}
      </ol>
      <div className="kit-actions">
        <Button icon={<Download size={16} />} onClick={download}>Download</Button>
        <Button icon={<Copy size={16} />} onClick={copyAll}>Copy</Button>
      </div>
      <label className="check-row">
        <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        <span>I have saved these codes somewhere safe</span>
      </label>
      <Button variant="primary" size="lg" block disabled={!saved} onClick={onDone}>{doneLabel}</Button>
    </div>
  )
}

// ---------------------------------------------------------------- forgot password (signed out)

export function ForgotPassword({ onBack, initialMethod = 'code' }) {
  const [method, setMethod] = useState(initialMethod)
  const [username, setUsername] = useState('')
  const [code, setCode] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [errors, setErrors] = useState({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)

  async function submit(e) {
    e.preventDefault()
    setError('')
    const user = username.trim().toLowerCase()
    const errs = {}
    if (!user) errs.username = 'Enter the Client Admin username.'
    if (!code.trim()) errs.code = method === 'code' ? 'Enter one of your recovery codes.' : 'Enter the code shown on the device.'
    Object.assign(errs, passwordProblem(user, password, confirmPw) || {})
    setErrors(errs)
    if (Object.keys(errs).length) return
    setBusy(true)
    try {
      const r = method === 'code' ? await recoveryReset(user, code, password) : await screenReset(user, code, password)
      setDone({ remaining: r.remaining })
      setPassword('')
      setConfirmPw('')
    } catch (err) {
      setError(friendly(err))
    } finally {
      setBusy(false)
    }
  }

  if (done) {
    return (
      <div className="card card-pad form">
        <div>
          <h1 className="page-title">Password changed</h1>
          <p className="page-sub">Sign in with your new password. Any other signed-in sessions have been signed out.</p>
        </div>
        {done.remaining !== undefined && (
          <Notice kind={done.remaining <= 2 ? 'warning' : 'info'}>
            {done.remaining} recovery {done.remaining === 1 ? 'code' : 'codes'} left. {done.remaining <= 2 && 'Create a new Recovery Kit from the Security page after you sign in.'}
          </Notice>
        )}
        <Button variant="primary" size="lg" block onClick={onBack}>Back to sign in</Button>
      </div>
    )
  }

  return (
    <form className="card card-pad form" onSubmit={submit} noValidate>
      <div>
        <h1 className="page-title">Reset admin password</h1>
        <p className="page-sub">For Client Admins. Hosts and members: ask a Client Admin to reset your password.</p>
      </div>
      <div className="tabs" role="tablist" style={{ alignSelf: 'stretch' }}>
        <button type="button" className="tab" role="tab" aria-selected={method === 'code'} style={{ flex: 1, justifyContent: 'center' }}
                onClick={() => { setMethod('code'); setCode(''); setError('') }}>
          <Shield size={14} />Recovery code
        </button>
        <button type="button" className="tab" role="tab" aria-selected={method === 'screen'} style={{ flex: 1, justifyContent: 'center' }}
                onClick={() => { setMethod('screen'); setCode(''); setError('') }}>
          <Monitor size={14} />Device screen
        </button>
      </div>
      {method === 'code' ? (
        <p className="field-hint" style={{ margin: 0 }}>Use one of the codes from the Recovery Kit you saved when the device was activated. Each code works once.</p>
      ) : (
        <p className="field-hint" style={{ margin: 0 }}>At the device, press and hold its screen for 8 seconds until a code appears, then enter it here. The code is valid for 10 minutes.</p>
      )}
      <TextInput label="Username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username"
                 autoCapitalize="none" spellCheck={false} error={errors.username} />
      <TextInput label={method === 'code' ? 'Recovery code' : 'Code on the device screen'} value={code}
                 onChange={(e) => setCode(e.target.value)} className="input-mono" autoComplete="one-time-code"
                 autoCapitalize="characters" spellCheck={false} placeholder={method === 'code' ? 'XXXXX-XXXXX' : '1234 5678'}
                 inputMode={method === 'screen' ? 'numeric' : undefined} error={errors.code} />
      <PasswordInput label="New password" value={password} onChange={(e) => setPassword(e.target.value)}
                     autoComplete="new-password" hint={`At least ${MIN_PASSWORD} characters.`} error={errors.password} />
      <PasswordInput label="Confirm new password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)}
                     autoComplete="new-password" error={errors.confirm} />
      {error && <Notice kind="error">{error}</Notice>}
      <Button type="submit" variant="primary" size="lg" block loading={busy}>Set new password</Button>
      <Button variant="ghost" block onClick={onBack}>Back to sign in</Button>
    </form>
  )
}

// ---------------------------------------------------------------- change your own password

export function ChangePasswordModal({ open, onClose, token, username, onChanged }) {
  const toast = useToast()
  const [current, setCurrent] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [errors, setErrors] = useState({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setCurrent('')
    setPassword('')
    setConfirmPw('')
    setErrors({})
    setError('')
  }, [open])

  async function submit(e) {
    e.preventDefault()
    setError('')
    const errs = { ...(current ? {} : { current: 'Enter your current password.' }), ...(passwordProblem(username, password, confirmPw) || {}) }
    setErrors(errs)
    if (Object.keys(errs).length) return
    setBusy(true)
    try {
      const r = await changePassword(token, current, password)
      onChanged(r.token)
      toast({ message: 'Password changed. Other devices have been signed out.' })
      onClose()
    } catch (err) {
      setError(friendly(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Change password" description="You stay signed in here; every other session is signed out."
           footer={<>
             <Button onClick={onClose}>Cancel</Button>
             <Button variant="primary" type="submit" form="change-password" loading={busy}>Change password</Button>
           </>}>
      <form id="change-password" className="form" onSubmit={submit} noValidate>
        <PasswordInput label="Current password" value={current} onChange={(e) => setCurrent(e.target.value)}
                       autoComplete="current-password" error={errors.current} />
        <PasswordInput label="New password" value={password} onChange={(e) => setPassword(e.target.value)}
                       autoComplete="new-password" hint={`At least ${MIN_PASSWORD} characters.`} error={errors.password} />
        <PasswordInput label="Confirm new password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)}
                       autoComplete="new-password" error={errors.confirm} />
        {error && <Notice kind="error">{error}</Notice>}
      </form>
    </Modal>
  )
}

// ---------------------------------------------------------------- admin resets someone else's password

export function ResetUserPasswordModal({ user, onClose, token, fail }) {
  const toast = useToast()
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState({})
  const [busy, setBusy] = useState(false)

  useEffect(() => { setPassword(''); setErrors({}) }, [user])
  if (!user) return null

  async function submit(e) {
    e.preventDefault()
    const errs = passwordProblem(user.username, password) || {}
    setErrors(errs)
    if (Object.keys(errs).length) return
    setBusy(true)
    try {
      await resetUserPassword(token, user.id, password)
      toast({ message: `Password reset. ${user.display_name || user.username} has been signed out everywhere.` })
      onClose()
    } catch (err) {
      fail(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open onClose={onClose} title={`Reset password for ${user.display_name || user.username}`}
           description="Give them the temporary password in person or over a secure channel, and ask them to change it after signing in."
           footer={<>
             <Button onClick={onClose}>Cancel</Button>
             <Button variant="primary" type="submit" form="reset-user-password" loading={busy}>Reset password</Button>
           </>}>
      <form id="reset-user-password" className="form" onSubmit={submit} noValidate>
        <PasswordInput label="Temporary password" value={password} onChange={(e) => setPassword(e.target.value)}
                       autoComplete="new-password" hint={`At least ${MIN_PASSWORD} characters.`} error={errors.password} />
      </form>
    </Modal>
  )
}

// ---------------------------------------------------------------- Security page (admins)

const EVENT_LABELS = {
  'device.claimed': 'Device activated',
  'password.changed': 'Changed their password',
  'password.reset_by_admin': 'Reset a password',
  'password.reset.recovery_code': 'Password reset with a recovery code',
  'password.reset.device_screen': 'Password reset from the device screen',
  'password.reset.device_screen_burned': 'Device-screen code cancelled after wrong attempts',
  'recovery_codes.generated': 'Created a new Recovery Kit',
  'screen_reset.enabled': 'Turned on reset from the device screen',
  'screen_reset.disabled': 'Turned off reset from the device screen',
  'user.created': 'Added a user',
  'user.removed': 'Removed a user',
}

export function useRecoveryStatus(token, isAdmin) {
  const [status, setStatus] = useState(null)
  const reload = useCallback(() => {
    if (!isAdmin) return
    recoveryStatus(token).then(setStatus).catch(() => {})
  }, [token, isAdmin])
  useEffect(() => { reload() }, [reload])
  return [status, reload]
}

export function SecurityPage({ token, user, fail, recovery, reloadRecovery }) {
  const toast = useToast()
  const [screenOn, setScreenOn] = useState(null)
  const [events, setEvents] = useState(null)
  const [confirming, setConfirming] = useState(false)
  const [password, setPassword] = useState('')
  const [pwError, setPwError] = useState('')
  const [busy, setBusy] = useState(false)
  const [codes, setCodes] = useState(null)

  const loadEvents = useCallback(() => {
    securityEvents(token).then(setEvents).catch(fail)
  }, [token, fail])

  useEffect(() => {
    getSettings(token).then((s) => setScreenOn(s.screen_reset !== false)).catch(fail)
    loadEvents()
  }, [token, fail, loadEvents])

  async function toggleScreen(on) {
    setScreenOn(on)
    try {
      await saveSettings(token, { screen_reset: on })
      toast({ message: on ? 'Reset from the device screen is on.' : 'Reset from the device screen is off.' })
      loadEvents()
    } catch (err) {
      setScreenOn(!on)
      fail(err)
    }
  }

  async function makeKit(e) {
    e.preventDefault()
    if (!password) { setPwError('Enter your password.'); return }
    setBusy(true)
    setPwError('')
    try {
      const r = await newRecoveryCodes(token, password)
      setCodes(r.codes)
      setConfirming(false)
      setPassword('')
    } catch (err) {
      setPwError(friendly(err))
    } finally {
      setBusy(false)
    }
  }

  const none = recovery && recovery.remaining === 0
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Security</h1>
          <p className="page-sub">How you get back in if a password is forgotten, and what happened recently.</p>
        </div>
      </div>

      <div className="card card-pad form">
        <div className="sec-head">
          <Shield size={20} />
          <div>
            <h2 className="sec-title">Your Recovery Kit</h2>
            <p className="muted" style={{ margin: 0 }}>
              One-time codes that let you set a new password if you forget yours. Only you have them; the codes are stored on this device in a form that cannot be read back.
            </p>
          </div>
        </div>
        {recovery === null ? <Spinner /> : (
          <Notice kind={none ? 'warning' : recovery.remaining <= 2 ? 'warning' : 'success'}>
            {none ? 'You have no recovery codes. Create a Recovery Kit now.'
              : `${recovery.remaining} of ${recovery.total} codes left${recovery.created_at ? ` · created ${new Date(recovery.created_at).toLocaleDateString()}` : ''}.`}
          </Notice>
        )}
        <div><Button variant={none ? 'primary' : 'secondary'} icon={<Refresh size={16} />} onClick={() => { setConfirming(true); setPwError('') }}>
          {none ? 'Create Recovery Kit' : 'Create a new kit'}
        </Button></div>
        <p className="field-hint" style={{ margin: 0 }}>A new kit replaces the old one: the old codes stop working. Tip: add a second Client Admin on the People page, so either of you can reset the other’s password.</p>
      </div>

      <div className="card card-pad form" style={{ marginTop: 16 }}>
        <div className="sec-head">
          <Monitor size={20} />
          <div>
            <h2 className="sec-title">Reset from the device screen</h2>
            <p className="muted" style={{ margin: 0 }}>Last resort when no recovery code is at hand. Someone at the device presses and holds its screen for 8 seconds; a code appears that resets a Client Admin password from the office network. Every use is logged below.</p>
          </div>
        </div>
        {screenOn === null ? <Spinner /> : (
          <Switch checked={screenOn} onChange={toggleScreen} label="Allow reset from the device screen"
                  description="Turn this off if the device may be in a place others can reach." />
        )}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-pad" style={{ paddingBottom: 8 }}>
          <div className="sec-head">
            <Clock size={20} />
            <h2 className="sec-title">Recent security events</h2>
          </div>
        </div>
        {events === null ? <div className="empty"><Spinner /></div> : events.length === 0 ? (
          <p className="muted card-pad" style={{ paddingTop: 0 }}>Nothing yet.</p>
        ) : (
          <div className="list">
            {events.map((e, i) => (
              <div className="list-row" key={i}>
                <div className="row-text">
                  <span className="row-title">{EVENT_LABELS[e.action] || e.action}{e.detail ? ` · ${e.detail}` : ''}</span>
                  <span className="row-meta subtle">{e.user ? `@${e.user} · ` : ''}{e.at ? new Date(e.at + (e.at.endsWith('Z') || e.at.includes('+') ? '' : 'Z')).toLocaleString() : ''}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <Modal open={confirming} onClose={() => setConfirming(false)} title="Create a new Recovery Kit"
             description="Enter your password to continue. Your current codes will stop working."
             footer={<>
               <Button onClick={() => setConfirming(false)}>Cancel</Button>
               <Button variant="primary" type="submit" form="new-kit" loading={busy}>Create kit</Button>
             </>}>
        <form id="new-kit" className="form" onSubmit={makeKit} noValidate>
          <PasswordInput label="Password" value={password} onChange={(e) => setPassword(e.target.value)}
                         autoComplete="current-password" error={pwError} />
        </form>
      </Modal>

      <Modal open={!!codes} onClose={() => {}} title="Your new Recovery Kit" description={`For @${user.username}`}>
        {codes && <RecoveryKit codes={codes} username={user.username} doneLabel="Done"
                               onDone={() => { setCodes(null); reloadRecovery(); loadEvents() }} />}
      </Modal>
    </div>
  )
}

