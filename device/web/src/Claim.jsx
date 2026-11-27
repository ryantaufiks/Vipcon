import { useEffect, useState } from 'react'
import { claimDevice, claimStatus, saveAuth } from './api.js'
import { PublicLayout, Button, TextInput, PasswordInput, Notice, Spinner } from './ui.jsx'
import { CheckCircle, Alert, Shield } from './icons.jsx'
import { friendly } from './format.js'
import { RecoveryKit } from './Recovery.jsx'

// Read the one-time code once, then drop it from the address bar so it does not
// linger in browser history or get shared by copying the URL.
function takeClaimCode() {
  const params = new URLSearchParams(window.location.search)
  const code = params.get('c') || ''
  if (code) window.history.replaceState(null, '', '/claim')
  return code
}

const MIN_PASSWORD = 10
const USERNAME_RE = /^[a-z0-9][a-z0-9._-]{2,31}$/

function StateCard({ icon, tone = '', title, children, action }) {
  return (
    <PublicLayout>
      <div className="state-card">
        <div className={`state-icon ${tone}`}>{icon}</div>
        <h1 className="state-title">{title}</h1>
        <div className="muted">{children}</div>
        {action && <div className="state-actions">{action}</div>}
      </div>
    </PublicLayout>
  )
}

export default function Claim() {
  const [code] = useState(takeClaimCode)
  const [status, setStatus] = useState(null) // null = loading
  const [orgName, setOrgName] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPw, setConfirmPw] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [errors, setErrors] = useState({})
  const [done, setDone] = useState(false)
  const [codes, setCodes] = useState(null) // the Recovery Kit, shown once right after activation

  useEffect(() => {
    claimStatus().then(setStatus).catch(() => setStatus({ claimed: false, unreachable: true }))
  }, [])

  async function submit(e) {
    e.preventDefault()
    setError('')
    const user = username.trim().toLowerCase()
    const errs = {}
    if (!displayName.trim()) errs.displayName = 'Enter your full name.'
    if (!USERNAME_RE.test(user)) errs.username = '3–32 characters: lowercase letters, numbers, dots, hyphens or underscores.'
    if (password.length < MIN_PASSWORD) errs.password = `At least ${MIN_PASSWORD} characters.`
    else if (password.toLowerCase() === user) errs.password = 'The password must not be the username.'
    if (!errs.password && password !== confirmPw) errs.confirm = 'The passwords do not match.'
    setErrors(errs)
    if (Object.keys(errs).length) return
    setBusy(true)
    try {
      const data = await claimDevice({ claim_code: code, username: user, password, display_name: displayName.trim(), org_name: orgName.trim() })
      saveAuth(data.token, data.username)
      setPassword('')
      setConfirmPw('')
      if (data.recovery_codes?.length) setCodes(data.recovery_codes)
      setDone(true)
    } catch (err) {
      setError(friendly(err))
    } finally {
      setBusy(false)
    }
  }

  if (done && codes) {
    return (
      <PublicLayout>
        <div className="public-card" style={{ maxWidth: 480 }}>
          <div className="card card-pad form">
            <div>
              <h1 className="page-title">Save your Recovery Kit</h1>
              <p className="page-sub">
                If you ever forget the password for <strong>{username.trim().toLowerCase()}</strong>, one of these codes lets you
                set a new one. Nobody else, including VIPCON, can reset it for you.
              </p>
            </div>
            <RecoveryKit codes={codes} username={username.trim().toLowerCase()} onDone={() => setCodes(null)} />
          </div>
        </div>
      </PublicLayout>
    )
  }
  if (done) {
    return (
      <StateCard icon={<CheckCircle size={26} />} tone="brand" title="Your VIPCON device is ready"
                 action={<Button variant="primary" size="lg" onClick={() => { window.location.href = '/admin' }}>Open the console</Button>}>
        The Client Admin account <strong>{username.trim().toLowerCase()}</strong> has been created. Next, add your hosts and
        schedule your first meeting.
      </StateCard>
    )
  }
  if (status === null) {
    return <PublicLayout><div className="state-card muted"><Spinner large />Checking the device…</div></PublicLayout>
  }
  if (status.claimed) {
    return (
      <StateCard icon={<Shield size={26} />} title="This device is already activated"
                 action={<Button variant="primary" onClick={() => { window.location.href = '/admin' }}>Sign in</Button>}>
        It already has a Client Admin. Sign in with that account.
      </StateCard>
    )
  }
  if (!code) {
    return (
      <StateCard icon={<Alert size={26} />} tone="danger" title="Incomplete activation link">
        Touch the device’s screen and scan the activation QR code again. The link only works when opened straight from that code.
      </StateCard>
    )
  }

  return (
    <PublicLayout>
      <div className="public-card" style={{ maxWidth: 480 }}>
        <form className="card card-pad form" onSubmit={submit} noValidate>
          <div>
            <h1 className="page-title">Activate VIPCON</h1>
            <p className="page-sub">
              Create the <strong>Client Admin</strong> account. It manages people and meetings on this device, and can only be created once.
            </p>
          </div>
          {status.device_id && <div className="os-pick"><span className="subtle">Device</span><span className="mono">{status.device_id}</span></div>}
          <TextInput label="Organisation" optional value={orgName} onChange={(e) => setOrgName(e.target.value)} maxLength={100}
                     placeholder="e.g. Example Holdings" autoComplete="organization" hint="Shown to guests on the join page." />
          <TextInput label="Full name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={80}
                     placeholder="Your name" autoComplete="name" error={errors.displayName} />
          <TextInput label="Username" value={username} onChange={(e) => setUsername(e.target.value)} maxLength={32}
                     placeholder="e.g. admin" autoComplete="username" autoCapitalize="none" spellCheck={false} error={errors.username} />
          <PasswordInput label="Password" value={password} onChange={(e) => setPassword(e.target.value)}
                         autoComplete="new-password" hint={`At least ${MIN_PASSWORD} characters.`} error={errors.password} />
          <PasswordInput label="Confirm password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)}
                         autoComplete="new-password" error={errors.confirm} />
          {status.unreachable && <Notice kind="warning">The device is not responding. Make sure your phone is on the office network.</Notice>}
          {error && <Notice kind="error">{error}</Notice>}
          <Button type="submit" variant="primary" size="lg" block loading={busy}>Activate device</Button>
        </form>
      </div>
    </PublicLayout>
  )
}
