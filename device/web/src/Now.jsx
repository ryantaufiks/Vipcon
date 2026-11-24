import { useEffect, useState } from 'react'
import { meetingsNow } from './api.js'
import { PublicLayout, Notice, Spinner, EmptyState } from './ui.jsx'
import { People, Lock, Shield, Video, DoorIn } from './icons.jsx'
import { friendly } from './format.js'

const REFRESH_MS = 5000

function since(iso) {
  if (!iso) return ''
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000))
  if (min < 1) return 'Just started'
  if (min < 60) return `Started ${min} min ago`
  return `Started ${Math.floor(min / 60)} h ${min % 60} min ago`
}

// The page behind the QR code on the device's screen: meetings in progress in this room.
export default function Now() {
  const [meetings, setMeetings] = useState(null)
  const [adminUrl, setAdminUrl] = useState('')
  const [error, setError] = useState('')
  const [, tick] = useState(0)

  useEffect(() => {
    let alive = true
    async function load() {
      try {
        // The room screen's QR carries a key; over the internet the list opens only with it.
        const data = await meetingsNow(new URLSearchParams(window.location.search).get('k'))
        if (!alive) return
        setMeetings(data.meetings || [])
        setAdminUrl(data.admin_url || '')
        setError('')
      } catch (err) {
        if (alive) setError(err.status === 403 ? err.message : friendly(err))
      }
    }
    load()
    const t = setInterval(() => { load(); tick((n) => n + 1) }, REFRESH_MS)
    return () => { alive = false; clearInterval(t) }
  }, [])

  return (
    <PublicLayout>
      <div className="public-card" style={{ maxWidth: 560 }}>
        <div className="card card-pad form">
          <div>
            <h1 className="page-title">Meetings in this room</h1>
            <p className="page-sub">Choose a meeting to join. This list updates automatically.</p>
          </div>
          {error && <Notice kind="error">{error}</Notice>}
          {meetings === null && !error && <div className="empty" style={{ padding: 24 }}><Spinner large /></div>}
          {meetings && meetings.length === 0 && (
            <EmptyState icon={<Video />} title="No meetings in progress">When a host starts a meeting, it appears here.</EmptyState>
          )}
          {meetings && meetings.length > 0 && (
            <div className="now-list">
              {meetings.map((m) => (
                <a key={m.room_name} className="now-item" href={`/m/${encodeURIComponent(m.room_name)}`}>
                  <div className="now-main">
                    <span className="now-title">{m.title}</span>
                    <span className="row-meta">
                      <span className="badge badge-live badge-dot">Live</span>
                      <span className="chip"><People size={14} />{m.participants}</span>
                      {m.has_passcode && <span className="chip"><Lock size={14} />Passcode</span>}
                      {m.lobby && <span className="chip"><Shield size={14} />Host admits</span>}
                    </span>
                    <span className="field-hint">{since(m.started_at)}</span>
                  </div>
                  <span className="btn btn-primary btn-sm"><DoorIn size={16} />Join</span>
                </a>
              ))}
            </div>
          )}
        </div>
        {adminUrl && (
          <p className="field-hint" style={{ textAlign: 'center', marginTop: 16 }}>
            Host? <a href={adminUrl}>Sign in</a> to start or manage meetings (office Wi‑Fi only).
          </p>
        )}
      </div>
    </PublicLayout>
  )
}
