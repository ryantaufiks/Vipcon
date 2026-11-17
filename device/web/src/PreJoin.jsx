import { useEffect, useState } from 'react'
import { joinRoom, hostToken, lobbyStatus, getToken, getUsername } from './api.js'
import { Sun, Moon } from './icons.jsx'

export default function PreJoin({ room, isHost, onJoined, theme, onToggleTheme }) {
  const [name, setName] = useState(() => (isHost ? getUsername() : ''))
  const [passcode, setPasscode] = useState('')
  const [busy, setBusy] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const [error, setError] = useState('')

  async function submit(e) {
    e.preventDefault()
    if (!name.trim()) {
      setError('Enter your name')
      return
    }
    setBusy(true)
    setError('')
    try {
      if (isHost) {
        const data = await hostToken(room, name.trim(), getToken())
        onJoined({ room, identity: name.trim(), token: data.token, wsUrl: data.ws_url, isHost: true })
      } else {
        const data = await joinRoom(room, name.trim(), passcode.trim())
        if (data.status === 'waiting') {
          setWaiting(true)
          return
        }
        onJoined({ room, identity: name.trim(), token: data.token, wsUrl: data.ws_url, isHost: false })
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (!waiting) return
    let cancelled = false
    async function poll() {
      try {
        const data = await lobbyStatus(room, name.trim())
        if (cancelled) return
        if (data.token) {
          onJoined({ room, identity: name.trim(), token: data.token, wsUrl: data.ws_url, isHost: false })
        } else if (data.status === 'rejected') {
          setWaiting(false)
          setError('The host rejected your request')
        }
      } catch {}
    }
    poll()
    const t = setInterval(poll, 2000)
    return () => { cancelled = true; clearInterval(t) }
  }, [waiting, room, name, onJoined])

  if (waiting) {
    return (
      <div className="prejoin">
        <header className="topbar">
          <div className="brand">VIP<span>CON</span></div>
          <button className="icon-btn" onClick={onToggleTheme}>{theme === 'dark' ? <Sun /> : <Moon />}</button>
        </header>
        <div className="prejoin-card">
          <h1>Waiting for host</h1>
          <p className="room-code">{room}</p>
          <p className="setup-sub">You are in the lobby. You will join automatically once the host admits you.</p>
          <div className="room-connecting">Waiting…</div>
        </div>
      </div>
    )
  }

  return (
    <div className="prejoin">
      <header className="topbar">
        <div className="brand">VIP<span>CON</span></div>
        <button className="icon-btn" onClick={onToggleTheme}>{theme === 'dark' ? <Sun /> : <Moon />}</button>
      </header>
      <form className="prejoin-card" onSubmit={submit}>
        <h1>{isHost ? 'Join as host' : 'Join meeting'}</h1>
        <p className="room-code">{room}</p>
        <label className="field">
          <span className="field-label">Display name</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Your name" autoFocus />
        </label>
        {!isHost && (
          <label className="field">
            <span className="field-label">Passcode <em>optional</em></span>
            <input type="password" value={passcode} onChange={(e) => setPasscode(e.target.value)} placeholder="••••" />
          </label>
        )}
        {error && <p className="form-error">{error}</p>}
        <button className="btn btn-primary" disabled={busy}>
          {busy ? 'Joining…' : isHost ? 'Join as host' : 'Join meeting'}
        </button>
      </form>
    </div>
  )
}
