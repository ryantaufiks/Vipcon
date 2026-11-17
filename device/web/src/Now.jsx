import { useEffect, useState } from 'react'
import { meetingsNow } from './api.js'
import { Sun, Moon } from './icons.jsx'

const REFRESH_MS = 5000

function since(iso) {
  if (!iso) return ''
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (min < 1) return 'Just started'
  if (min < 60) return `Started ${min} min ago`
  return `Started ${Math.floor(min / 60)} h ${min % 60} min ago`
}

// The page behind the QR code on the device's screen: meetings in progress in this room.
export default function Now({ theme, onToggleTheme }) {
  const [meetings, setMeetings] = useState(null) // null = first load
  const [error, setError] = useState('')
  const [, tick] = useState(0)

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        const data = await meetingsNow()
        if (!alive) return
        setMeetings(data.meetings || [])
        setError('')
      } catch (err) {
        if (alive) setError(err.message)
      }
    }
    load()
    const t = setInterval(() => { load(); tick((n) => n + 1) }, REFRESH_MS)
    return () => { alive = false; clearInterval(t) }
  }, [])

  return (
    <div className="prejoin">
      <header className="topbar">
        <div className="brand">VIP<span>CON</span></div>
        <button className="icon-btn" onClick={onToggleTheme} title="Toggle theme">
          {theme === 'dark' ? <Sun /> : <Moon />}
        </button>
      </header>
      <div className="prejoin-card now-card">
        <h1>Meetings in progress</h1>
        <p className="setup-sub">Choose a meeting to join. This list updates automatically.</p>
        {error && <p className="form-error" role="alert">{error}</p>}
        {meetings === null && !error && <div className="room-connecting">Loading…</div>}
        {meetings && meetings.length === 0 && (
          <div className="now-empty">
            <p>No meetings in progress right now.</p>
            <p className="dash-meta">When a host starts a meeting it will appear here.</p>
          </div>
        )}
        {meetings && meetings.length > 0 && (
          <ul className="now-list">
            {meetings.map((m) => (
              <li key={m.room_name}>
                <a className="now-item" href={`/m/${encodeURIComponent(m.room_name)}`}>
                  <span className="now-live" aria-hidden="true" />
                  <span className="now-main">
                    <span className="now-title">{m.title}</span>
                    <span className="dash-meta">
                      {since(m.started_at)} · {m.participants} {m.participants === 1 ? 'person' : 'people'}
                      {m.has_passcode ? ' · Passcode' : ''}{m.lobby ? ' · Host approval' : ''}
                    </span>
                  </span>
                  <span className="btn btn-primary now-join">Join</span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
