import { useEffect, useState } from 'react'
import { claimDevice, claimStatus, saveAuth } from './api.js'
import { Sun, Moon } from './icons.jsx'

// Read the one-time code once, then drop it from the address bar so it does not
// linger in browser history or get shared by copying the URL.
function takeClaimCode() {
  const params = new URLSearchParams(window.location.search)
  const code = params.get('c') || ''
  if (code) window.history.replaceState(null, '', '/claim')
  return code
}

const MIN_PASSWORD = 10

export default function Claim({ theme, onToggleTheme }) {
  const [code] = useState(takeClaimCode)
  const [status, setStatus] = useState(null) // null = loading
  const [orgName, setOrgName] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [done, setDone] = useState(false)

  useEffect(() => {
    claimStatus()
      .then(setStatus)
      .catch(() => setStatus({ claimed: false, unreachable: true }))
  }, [])

  async function submit(e) {
    e.preventDefault()
    setError('')
    const user = username.trim().toLowerCase()
    if (!/^[a-z0-9][a-z0-9._-]{2,31}$/.test(user)) {
      setError('Username must be 3–32 characters: lowercase letters, numbers, dots, hyphens or underscores.')
      return
    }
    if (password.length < MIN_PASSWORD) {
      setError(`Password must be at least ${MIN_PASSWORD} characters.`)
      return
    }
    if (password !== confirm) {
      setError('Passwords do not match.')
      return
    }
    setBusy(true)
    try {
      const data = await claimDevice({
        claim_code: code,
        username: user,
        password,
        display_name: displayName.trim(),
        org_name: orgName.trim(),
      })
      saveAuth(data.token, data.username)
      setPassword('')
      setConfirm('')
      setDone(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  const header = (
    <header className="topbar">
      <div className="brand">VIP<span>CON</span></div>
      <button className="icon-btn" onClick={onToggleTheme} title="Toggle theme">
        {theme === 'dark' ? <Sun /> : <Moon />}
      </button>
    </header>
  )

  function Card({ title, children }) {
    return (
      <div className="prejoin">
        {header}
        <div className="prejoin-card">
          <h1>{title}</h1>
          {children}
        </div>
      </div>
    )
  }

  if (done) {
    return (
      <Card title="Device activated">
        <p className="setup-sub">
          The Client Admin account <strong>{username.trim().toLowerCase()}</strong> has been created. From the
          dashboard you can add users and create meetings.
        </p>
        <a className="btn btn-primary setup-download" href="/admin">Open dashboard</a>
      </Card>
    )
  }

  if (status === null) {
    return <Card title="Activate Vipcon"><div className="room-connecting">Checking device…</div></Card>
  }

  if (status.claimed) {
    return (
      <Card title="Already activated">
        <p className="setup-sub">This device already has a Client Admin. Sign in with that account.</p>
        <a className="btn btn-primary setup-download" href="/admin">Sign in</a>
      </Card>
    )
  }

  if (!code) {
    return (
      <Card title="Incomplete activation link">
        <p className="setup-sub">
          Touch the device's screen and scan the activation QR code again. The link must be opened directly from
          that QR code.
        </p>
      </Card>
    )
  }

  return (
    <div className="prejoin">
      {header}
      <form className="prejoin-card" onSubmit={submit}>
        <h1>Activate Vipcon</h1>
        <p className="setup-sub">
          Create the <strong>Client Admin</strong> account for this device. This account manages users and meetings,
          and can only be created once.
        </p>
        {status.device_id && <p className="room-code">{status.device_id}</p>}
        <label className="field">
          <span className="field-label">Organisation name <em>optional</em></span>
          <input value={orgName} onChange={(e) => setOrgName(e.target.value)} maxLength={100}
                 placeholder="Example Pte Ltd" autoComplete="organization" />
        </label>
        <label className="field">
          <span className="field-label">Full name</span>
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={80}
                 placeholder="Your name" autoComplete="name" required />
        </label>
        <label className="field">
          <span className="field-label">Username</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} maxLength={32}
                 placeholder="admin" autoComplete="username" autoCapitalize="none" spellCheck={false} required />
        </label>
        <label className="field">
          <span className="field-label">Password <em>min. {MIN_PASSWORD} characters</em></span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                 autoComplete="new-password" minLength={MIN_PASSWORD} required />
        </label>
        <label className="field">
          <span className="field-label">Confirm password</span>
          <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)}
                 autoComplete="new-password" required />
        </label>
        {status.unreachable && <p className="form-info">The device is not responding. Make sure your phone is on the right network.</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="btn btn-primary" disabled={busy}>{busy ? 'Activating…' : 'Activate device'}</button>
      </form>
    </div>
  )
}
