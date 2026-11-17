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
      setError('Username 3–32 karakter: huruf kecil, angka, titik, minus atau garis bawah.')
      return
    }
    if (password.length < MIN_PASSWORD) {
      setError(`Password minimal ${MIN_PASSWORD} karakter.`)
      return
    }
    if (password !== confirm) {
      setError('Konfirmasi password tidak sama.')
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
      <button className="icon-btn" onClick={onToggleTheme} title="Ganti tema">
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
      <Card title="Device aktif">
        <p className="setup-sub">
          Akun Client Admin <strong>{username.trim().toLowerCase()}</strong> sudah dibuat. Dari dashboard Anda bisa
          menambah pengguna dan membuat meeting.
        </p>
        <a className="btn btn-primary setup-download" href="/admin">Buka dashboard</a>
      </Card>
    )
  }

  if (status === null) {
    return <Card title="Aktivasi Vipcon"><div className="room-connecting">Memeriksa device…</div></Card>
  }

  if (status.claimed) {
    return (
      <Card title="Sudah diaktivasi">
        <p className="setup-sub">Device ini sudah punya Client Admin. Masuk dengan akun yang sudah dibuat.</p>
        <a className="btn btn-primary setup-download" href="/admin">Masuk</a>
      </Card>
    )
  }

  if (!code) {
    return (
      <Card title="Link aktivasi tidak lengkap">
        <p className="setup-sub">
          Sentuh layar device, lalu scan ulang QR aktivasi yang tampil. Link harus dibuka langsung dari QR tersebut.
        </p>
      </Card>
    )
  }

  return (
    <div className="prejoin">
      {header}
      <form className="prejoin-card" onSubmit={submit}>
        <h1>Aktivasi Vipcon</h1>
        <p className="setup-sub">
          Buat akun <strong>Client Admin</strong> untuk device ini. Akun ini mengelola pengguna dan meeting, dan hanya
          bisa dibuat sekali.
        </p>
        {status.device_id && <p className="room-code">{status.device_id}</p>}
        <label className="field">
          <span className="field-label">Nama organisasi <em>opsional</em></span>
          <input value={orgName} onChange={(e) => setOrgName(e.target.value)} maxLength={100}
                 placeholder="PT Contoh" autoComplete="organization" />
        </label>
        <label className="field">
          <span className="field-label">Nama lengkap</span>
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={80}
                 placeholder="Nama Anda" autoComplete="name" required />
        </label>
        <label className="field">
          <span className="field-label">Username</span>
          <input value={username} onChange={(e) => setUsername(e.target.value)} maxLength={32}
                 placeholder="admin" autoComplete="username" autoCapitalize="none" spellCheck={false} required />
        </label>
        <label className="field">
          <span className="field-label">Password <em>min. {MIN_PASSWORD} karakter</em></span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                 autoComplete="new-password" minLength={MIN_PASSWORD} required />
        </label>
        <label className="field">
          <span className="field-label">Ulangi password</span>
          <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)}
                 autoComplete="new-password" required />
        </label>
        {status.unreachable && <p className="form-info">Device tidak merespons. Pastikan HP Anda terhubung ke jaringan yang benar.</p>}
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="btn btn-primary" disabled={busy}>{busy ? 'Mengaktifkan…' : 'Aktifkan device'}</button>
      </form>
    </div>
  )
}
