import { useEffect, useState } from 'react'
import { Sun, Moon } from './icons.jsx'
import {
  login, createMeeting, listMeetings, endMeeting, deleteMeeting,
  listUsers, createUser, deleteUser, saveAuth, getToken, clearAuth, getUsername,
} from './api.js'

export default function Dashboard({ theme, onToggleTheme }) {
  const [token, setTok] = useState(() => getToken())
  const [meetings, setMeetings] = useState([])
  const [users, setUsers] = useState([])
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  const [mTitle, setMTitle] = useState('')
  const [mScheduled, setMScheduled] = useState('')
  const [mPasscode, setMPasscode] = useState('')
  const [mLobby, setMLobby] = useState(true)
  const [mShow, setMShow] = useState(true)
  const [mDuration, setMDuration] = useState(60)

  const [uName, setUName] = useState('')
  const [uPass, setUPass] = useState('')
  const [uRole, setURole] = useState('host')

  async function refresh(t) {
    try {
      const [m, u] = await Promise.all([listMeetings(t), listUsers(t)])
      setMeetings(m)
      setUsers(u)
    } catch (e) {
      setError(e.message)
    }
  }

  useEffect(() => {
    if (token) refresh(token)
  }, [token])

  async function doLogin(e) {
    e.preventDefault()
    setError('')
    try {
      const d = await login(username, password)
      saveAuth(d.token, d.user.username)
      setTok(d.token)
      refresh(d.token)
    } catch (err) {
      setError(err.message)
    }
  }

  function doLogout() {
    clearAuth()
    setTok(null)
    setMeetings([])
    setUsers([])
  }

  async function doCreateMeeting(e) {
    e.preventDefault()
    setError('')
    try {
      const m = await createMeeting(token, {
        title: mTitle,
        scheduled_at: mScheduled ? new Date(mScheduled).toISOString() : null,
        passcode: mPasscode || null,
        lobby: mLobby,
        show_on_screen: mShow,
        duration_minutes: mDuration,
      })
      setMTitle('')
      setMPasscode('')
      const url = m.local_url || m.internet_url
      navigator.clipboard.writeText(`${url}\nPasscode: ${m.passcode || '-'}`).catch(() => {})
      setInfo(`Created. Invite link + passcode copied to clipboard (${m.passcode || 'no passcode'})`)
      refresh(token)
    } catch (err) {
      setError(err.message)
    }
  }

  async function doCreateUser(e) {
    e.preventDefault()
    setError('')
    try {
      await createUser(token, { username: uName, password: uPass, role: uRole })
      setUName('')
      setUPass('')
      refresh(token)
    } catch (err) {
      setError(err.message)
    }
  }

  async function doEnd(m) {
    try {
      await endMeeting(token, m.id)
      refresh(token)
    } catch (err) {
      setError(err.message)
    }
  }

  async function doDelete(m) {
    try {
      await deleteMeeting(token, m.id)
      refresh(token)
    } catch (err) {
      setError(err.message)
    }
  }

  async function doDeleteUser(u) {
    try {
      await deleteUser(token, u.id)
      refresh(token)
    } catch (err) {
      setError(err.message)
    }
  }

  function copyInvite(m) {
    navigator.clipboard.writeText(m.local_url || m.internet_url).catch(() => {})
    setInfo('Invite link copied')
  }

  function joinAsHost(m) {
    window.location.href = `/m/${m.room_name}?host=1`
  }

  if (!token) {
    return (
      <div className="dashboard">
        <header className="topbar">
          <div className="brand">VIP<span>CON</span></div>
          <button className="icon-btn" onClick={onToggleTheme}>{theme === 'dark' ? <Sun /> : <Moon />}</button>
        </header>
        <form className="setup-card" onSubmit={doLogin}>
          <h1>Host sign in</h1>
          <label className="field">
            <span className="field-label">Username</span>
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
          </label>
          <label className="field">
            <span className="field-label">Password</span>
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          {error && <p className="form-error">{error}</p>}
          <button className="btn btn-primary">Sign in</button>
        </form>
      </div>
    )
  }

  return (
    <div className="dashboard">
      <header className="topbar">
        <div className="brand">VIP<span>CON</span></div>
        <div className="dash-user">{getUsername()}</div>
        <button className="icon-btn" onClick={onToggleTheme}>{theme === 'dark' ? <Sun /> : <Moon />}</button>
        <button className="btn" onClick={doLogout}>Logout</button>
      </header>

      <div className="dash-body">
        {error && <p className="form-error">{error}</p>}
        {info && <p className="form-info">{info}</p>}

        <section className="dash-card">
          <h2>New meeting</h2>
          <form className="dash-form" onSubmit={doCreateMeeting}>
            <input placeholder="Title" value={mTitle} onChange={(e) => setMTitle(e.target.value)} required />
            <input type="datetime-local" value={mScheduled} onChange={(e) => setMScheduled(e.target.value)} />
            <input placeholder="Passcode (optional)" value={mPasscode} onChange={(e) => setMPasscode(e.target.value)} />
            <input type="number" min="5" max="240" value={mDuration} onChange={(e) => setMDuration(e.target.value)} title="Duration (minutes)" />
            <label className="dash-check"><input type="checkbox" checked={mLobby} onChange={(e) => setMLobby(e.target.checked)} /> Lobby</label>
            <label className="dash-check" title="List this meeting on the room screen's 'Meetings in progress' page while it runs"><input type="checkbox" checked={mShow} onChange={(e) => setMShow(e.target.checked)} /> Show on room screen</label>
            <button className="btn btn-primary">Create</button>
          </form>
        </section>

        <section className="dash-card">
          <h2>Meetings ({meetings.filter((m) => m.status !== 'ended').length} active)</h2>
          <ul className="dash-list">
            {meetings.map((m) => (
              <li key={m.id} className="dash-row">
                <div className="dash-row-main">
                  <span className="dash-title">{m.title}</span>
                  <span className="dash-meta">
                    {m.status} · {m.scheduled_at ? new Date(m.scheduled_at).toLocaleString() : 'no schedule'}
                    {' · '}{m.duration_minutes}min{m.has_passcode ? ' · 🔒' : ''}
                  </span>
                </div>
                <div className="dash-row-actions">
                  <button className="btn" onClick={() => joinAsHost(m)}>Join as host</button>
                  <button className="btn" onClick={() => copyInvite(m)}>Copy link</button>
                  {m.status !== 'ended' && <button className="btn" onClick={() => doEnd(m)}>End</button>}
                  <button className="btn" onClick={() => doDelete(m)}>Delete</button>
                </div>
              </li>
            ))}
          </ul>
        </section>

        <section className="dash-card">
          <h2>Users ({users.length})</h2>
          <form className="dash-form" onSubmit={doCreateUser}>
            <input placeholder="Username" value={uName} onChange={(e) => setUName(e.target.value)} required />
            <input placeholder="Password" type="password" value={uPass} onChange={(e) => setUPass(e.target.value)} required />
            <select value={uRole} onChange={(e) => setURole(e.target.value)}>
              <option value="host">host</option>
              <option value="member">member</option>
            </select>
            <button className="btn btn-primary">Add</button>
          </form>
          <ul className="dash-list">
            {users.map((u) => (
              <li key={u.id} className="dash-row">
                <div className="dash-row-main">
                  <span className="dash-title">{u.username}</span>
                  <span className="dash-meta">{u.role}</span>
                </div>
                <div className="dash-row-actions">
                  {u.role !== 'admin' && <button className="btn" onClick={() => doDeleteUser(u)}>Delete</button>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  )
}
