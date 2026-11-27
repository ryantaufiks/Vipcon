import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  login, createMeeting, listMeetings, endMeeting, deleteMeeting, listUsers, createUser, deleteUser,
  saveAuth, getToken, clearAuth, getUsername, me, getSettings, meetingsNow, resetUser2fa,
} from './api.js'
import {
  Logo, ThemeToggle, Button, TextInput, PasswordInput, Field, Switch, Choice, Notice, Avatar, EmptyState,
  Modal, Menu, MenuItem, Spinner, useToast, useConfirm, copyText,
} from './ui.jsx'
import {
  Video, People, Plus, Search, More, Copy, LinkIcon, Trash, Stop, Lock, Clock, Calendar, Shield, LogOut,
  DoorIn, Menu as MenuIcon, UserPlus, Refresh, Monitor, Settings,
} from './icons.jsx'
import { ForgotPassword, ChangePasswordModal, ResetUserPasswordModal, SecurityPage, useRecoveryStatus } from './Recovery.jsx'
import { TwoFactorModal, TwoFactorGate } from './TwoFactor.jsx'
import { when, dateParts, duration, nextQuarter, randomPasscode, inviteText, friendly, sessionExpired } from './format.js'

const USERNAME_RE = /^[a-z0-9][a-z0-9._-]{2,31}$/
const MIN_PASSWORD = 10

export default function Dashboard() {
  const [token, setToken] = useState(() => getToken())
  if (!token) return <SignIn onSignedIn={setToken} />
  return <Console token={token} onToken={(t) => { saveAuth(t, getUsername()); setToken(t) }}
                  onSignOut={() => { clearAuth(); setToken(null) }} />
}

// ---------------------------------------------------------------- sign in

function SignIn({ onSignedIn }) {
  // #reset comes from the QR on the device's admin-reset screen.
  const [forgot, setForgot] = useState(() => (window.location.hash === '#reset' ? 'screen' : null))
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const [needCode, setNeedCode] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(e) {
    e.preventDefault()
    if (!username.trim() || !password) {
      setError('Enter your username and password.')
      return
    }
    if (needCode && code.replace(/\D/g, '').length !== 6) {
      setError('Enter the 6-digit code from your authenticator app.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const d = await login(username.trim().toLowerCase(), password, needCode ? code : null)
      saveAuth(d.token, d.user.username)
      onSignedIn(d.token)
    } catch (err) {
      if (err.message === 'totp required') {
        setNeedCode(true)
      } else {
        setError(friendly(err))
        if (needCode) setCode('')
        else setPassword('')
      }
    } finally {
      setBusy(false)
    }
  }

  const back = () => { setForgot(null); window.history.replaceState(null, '', '/admin') }
  return (
    <div className="public">
      <header className="public-top"><span /><ThemeToggle /></header>
      <main className="public-main">
        <div className="public-card">
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 28 }}><Logo large /></div>
          {forgot ? <ForgotPassword initialMethod={forgot} onBack={back} /> : <>
          <form className="card card-pad form" onSubmit={submit} noValidate>
            <div>
              <h1 className="page-title">Sign in</h1>
              <p className="page-sub">Manage meetings and people on this VIPCON device.</p>
            </div>
            <TextInput label="Username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus
                       autoComplete="username" autoCapitalize="none" spellCheck={false} />
            <PasswordInput label="Password" value={password} onChange={(e) => setPassword(e.target.value)}
                           autoComplete="current-password" disabled={needCode} />
            {needCode && (
              <TextInput label="Code from your authenticator app" value={code} autoFocus
                         onChange={(e) => setCode(e.target.value.replace(/[^\d ]/g, '').slice(0, 7))}
                         inputMode="numeric" autoComplete="one-time-code" placeholder="123 456" className="input-mono"
                         hint="Lost your phone? A Client Admin can reset two-factor for you; admins can use a Recovery Kit code." />
            )}
            {error && <Notice kind="error">{error}</Notice>}
            <Button type="submit" variant="primary" size="lg" block loading={busy}>{needCode ? 'Verify' : 'Sign in'}</Button>
          </form>
          <p className="field-hint" style={{ textAlign: 'center', marginTop: 16 }}>
            Forgot your password? Hosts and members: ask a Client Admin.{' '}
            <button type="button" className="link-btn" onClick={() => setForgot('code')}>Client Admin: reset it here</button>
          </p>
          </>}
        </div>
      </main>
      <footer className="public-foot">Sign-in is available on the office network only</footer>
    </div>
  )
}

// ---------------------------------------------------------------- console shell

function Console({ token, onToken, onSignOut }) {
  const toast = useToast()
  const [page, setPage] = useState(() => ({ '#people': 'people', '#security': 'security' })[window.location.hash] || 'meetings')
  const [changingPw, setChangingPw] = useState(false)
  const [twoFactor, setTwoFactor] = useState(false)
  const [navOpen, setNavOpen] = useState(false)
  const [user, setUser] = useState({ username: getUsername(), display_name: '', role: '' })
  const [org, setOrg] = useState('')
  const [meetings, setMeetings] = useState(null)
  const [users, setUsers] = useState(null)
  const [live, setLive] = useState({})
  const isAdmin = user.role === 'admin'
  const [recovery, reloadRecovery] = useRecoveryStatus(token, isAdmin)

  const fail = useCallback((err) => {
    if (sessionExpired(err)) {
      onSignOut()
      return
    }
    if (err?.message === '2fa enrollment required') {
      setUser((u) => ({ ...u, must_enroll_2fa: true }))
      return
    }
    toast({ kind: 'error', message: friendly(err) })
  }, [onSignOut, toast])

  const loadMeetings = useCallback(async () => {
    try { setMeetings(await listMeetings(token)) } catch (err) { fail(err) }
  }, [token, fail])
  const loadUsers = useCallback(async () => {
    try { setUsers(await listUsers(token)) } catch (err) { if (err.status !== 403) fail(err) }
  }, [token, fail])

  useEffect(() => {
    me(token).then(setUser).catch(fail)
    getSettings(token).then((s) => setOrg(s.org_name || '')).catch(() => {})
    loadMeetings()
    loadUsers()
  }, [token, fail, loadMeetings, loadUsers])

  // Which meetings have people in them right now (from the room-screen list).
  useEffect(() => {
    let alive = true
    const poll = () => meetingsNow().then((d) => {
      if (!alive) return
      setLive(Object.fromEntries((d.meetings || []).map((m) => [m.room_name, m.participants])))
    }).catch(() => {})
    poll()
    const t = setInterval(poll, 15000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  function go(p) {
    setPage(p)
    setNavOpen(false)
    window.history.replaceState(null, '', p === 'meetings' ? '#' : `#${p}`)
  }

  if (user.must_enroll_2fa) {
    return <TwoFactorGate token={token} onSignOut={onSignOut}
                          onDone={() => me(token).then((u) => { setUser(u); loadMeetings(); loadUsers() }).catch(fail)} />
  }

  const name = user.display_name || user.username
  const nav = (
    <aside className={`sidebar ${navOpen ? 'open' : ''}`}>
      <div className="sidebar-brand">
        <Logo />
        {org && <span className="sidebar-org" title={org}>{org}</span>}
      </div>
      <div className="nav-label">Workspace</div>
      <button className="nav-item" aria-current={page === 'meetings' ? 'page' : undefined} onClick={() => go('meetings')}>
        <Video /> Meetings
      </button>
      {isAdmin && (
        <button className="nav-item" aria-current={page === 'people' ? 'page' : undefined} onClick={() => go('people')}>
          <People /> People
        </button>
      )}
      {isAdmin && (
        <button className="nav-item" aria-current={page === 'security' ? 'page' : undefined} onClick={() => go('security')}>
          <Shield /> Security
          {recovery && recovery.remaining === 0 && <span className="badge badge-dot" style={{ marginLeft: 'auto' }} aria-label="Needs attention" />}
        </button>
      )}
      <a className="nav-item" href="/now" target="_blank" rel="noreferrer" style={{ textDecoration: 'none' }}>
        <Monitor /> Room screen list
      </a>
      <div className="sidebar-spacer" />
      <div className="user-card">
        <Avatar name={name} size={34} />
        <div className="user-meta">
          <div className="user-name" title={name}>{name}</div>
          <div className="user-role">{user.role === 'admin' ? 'Client Admin' : user.role || '…'}</div>
        </div>
        <ThemeToggle className="btn btn-ghost btn-sm btn-icon" />
        <Menu trigger={({ toggle }) => (
          <button className="btn btn-ghost btn-sm btn-icon" onClick={toggle} title="Account" aria-label="Account">
            <Settings size={16} />
          </button>
        )}>
          <MenuItem icon={<Lock size={16} />} onClick={() => setChangingPw(true)}>Change password</MenuItem>
          <MenuItem icon={<Shield size={16} />} onClick={() => setTwoFactor(true)}>
            Two-factor sign-in{user.totp_enabled ? ' · on' : ''}
          </MenuItem>
          <MenuItem icon={<LogOut size={16} />} onClick={onSignOut}>Sign out</MenuItem>
        </Menu>
      </div>
    </aside>
  )

  return (
    <div className="shell">
      {nav}
      {navOpen && <div className="sidebar-scrim" onClick={() => setNavOpen(false)} />}
      <div className="main">
        <div className="mobile-bar">
          <Button variant="ghost" icon={<MenuIcon />} onClick={() => setNavOpen(true)} aria-label="Open menu" />
          <Logo />
          <ThemeToggle />
        </div>
        {isAdmin && recovery && recovery.remaining === 0 && page !== 'security' && (
          <div style={{ padding: '16px 24px 0' }}>
            <Notice kind="warning">
              You have no recovery codes. If you forget your password, only a factory reset would get you back in.{' '}
              <button type="button" className="link-btn" onClick={() => go('security')}>Create a Recovery Kit</button>
            </Notice>
          </div>
        )}
        {page === 'people' && isAdmin
          ? <PeoplePage users={users} me={user} token={token} reload={loadUsers} fail={fail} />
          : page === 'security' && isAdmin
            ? <SecurityPage token={token} user={user} fail={fail} recovery={recovery} reloadRecovery={reloadRecovery}
                            org={org} onSignOut={onSignOut} onMeChanged={() => me(token).then(setUser).catch(fail)} />
            : <MeetingsPage meetings={meetings} live={live} token={token} org={org} reload={loadMeetings} fail={fail} isAdmin={isAdmin}
                            canCreate={isAdmin || user.role === 'host'} />}
      </div>
      <ChangePasswordModal open={changingPw} onClose={() => setChangingPw(false)} token={token} username={user.username}
                           onChanged={onToken} />
      <TwoFactorModal open={twoFactor} onClose={() => setTwoFactor(false)} token={token} me={user}
                      onChanged={() => me(token).then(setUser).catch(fail)} />
    </div>
  )
}

// ---------------------------------------------------------------- meetings

function MeetingsPage({ meetings, live, token, org, reload, fail, isAdmin, canCreate }) {
  const toast = useToast()
  const confirm = useConfirm()
  const [tab, setTab] = useState('upcoming')
  const [query, setQuery] = useState('')
  const [creating, setCreating] = useState(false)
  const [ready, setReady] = useState(null)

  const groups = useMemo(() => {
    const list = meetings || []
    const upcoming = list.filter((m) => m.status !== 'ended').sort((a, b) => {
      const la = live[a.room_name] ? 0 : 1
      const lb = live[b.room_name] ? 0 : 1
      if (la !== lb) return la - lb
      return (a.scheduled_at || '9999').localeCompare(b.scheduled_at || '9999')
    })
    const past = list.filter((m) => m.status === 'ended')
    return { upcoming, past }
  }, [meetings, live])

  const shown = groups[tab].filter((m) => m.title.toLowerCase().includes(query.trim().toLowerCase()))

  async function copyInvite(m) {
    const ok = await copyText(inviteText({ title: m.title, url: m.url || m.local_url, scheduled_at: m.scheduled_at,
      duration_minutes: m.duration_minutes, org }))
    toast(ok ? { message: m.has_passcode ? 'Invitation copied. Send the passcode separately.' : 'Invitation copied.' }
      : { kind: 'error', message: 'Could not copy. Select the link and copy it manually.' })
  }

  async function copyLink(m) {
    const ok = await copyText(m.url || m.local_url)
    toast(ok ? { message: 'Meeting link copied.' } : { kind: 'error', message: 'Could not copy the link.' })
  }

  async function end(m) {
    const ok = await confirm({
      title: `End “${m.title}”?`,
      message: 'Everyone still in the meeting is disconnected and the link stops working.',
      confirmLabel: 'End meeting', danger: true,
    })
    if (!ok) return
    try {
      await endMeeting(token, m.id)
      toast({ message: 'Meeting ended.' })
      reload()
    } catch (err) { fail(err) }
  }

  async function remove(m) {
    const ok = await confirm({
      title: `Delete “${m.title}”?`,
      message: 'The meeting and its link are removed permanently. This cannot be undone.',
      confirmLabel: 'Delete', danger: true,
    })
    if (!ok) return
    try {
      await deleteMeeting(token, m.id)
      toast({ message: 'Meeting deleted.' })
      reload()
    } catch (err) { fail(err) }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Meetings</h1>
          <p className="page-sub">
            {canCreate ? 'Schedule meetings, share invitations and start them as host.'
              : 'Meetings on this device. Join straight in — no passcode or waiting room.'}
          </p>
        </div>
        {canCreate && <Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>New meeting</Button>}
      </div>

      <div className="toolbar">
        <div className="tabs" role="tablist">
          <button className="tab" role="tab" aria-selected={tab === 'upcoming'} onClick={() => setTab('upcoming')}>
            Upcoming <span className="tab-count">{groups.upcoming.length}</span>
          </button>
          <button className="tab" role="tab" aria-selected={tab === 'past'} onClick={() => setTab('past')}>
            Past <span className="tab-count">{groups.past.length}</span>
          </button>
        </div>
        <div className="search">
          <Search size={16} />
          <input className="input" placeholder="Search meetings" value={query} onChange={(e) => setQuery(e.target.value)}
                 aria-label="Search meetings" />
        </div>
      </div>

      <div className="card">
        {meetings === null ? (
          <div className="empty"><Spinner large /></div>
        ) : shown.length === 0 ? (
          query ? (
            <EmptyState icon={<Search />} title="No matching meetings">Try a different search.</EmptyState>
          ) : tab === 'upcoming' ? (
            <EmptyState icon={<Calendar />} title="No upcoming meetings"
                        action={canCreate && <Button variant="primary" icon={<Plus />} onClick={() => setCreating(true)}>New meeting</Button>}>
              {canCreate ? 'Create a meeting to get a private link and passcode to share.' : 'Meetings your hosts schedule appear here.'}
            </EmptyState>
          ) : (
            <EmptyState icon={<Clock />} title="No past meetings">Meetings you end appear here.</EmptyState>
          )
        ) : (
          <div className="list">
            {shown.map((m) => (
              <MeetingRow key={m.id} m={m} live={live[m.room_name]} onCopyInvite={copyInvite} onCopyLink={copyLink}
                          onEnd={end} onDelete={isAdmin ? remove : null} />
            ))}
          </div>
        )}
      </div>

      <NewMeetingModal open={creating} onClose={() => setCreating(false)} token={token}
                       onCreated={(m) => { setCreating(false); setReady(m); reload() }} fail={fail} />
      <MeetingReadyModal meeting={ready} org={org} onClose={() => setReady(null)} />
    </div>
  )
}

function MeetingRow({ m, live, onCopyInvite, onCopyLink, onEnd, onDelete }) {
  const ended = m.status === 'ended'
  const parts = dateParts(m.scheduled_at)
  const mine = m.can_host
  const start = () => { window.location.href = `/m/${encodeURIComponent(m.room_name)}?${mine ? 'host' : 'staff'}=1` }
  return (
    <div className="list-row">
      <div className="row-main">
        <div className={`date-tile ${ended ? 'dim' : ''}`} aria-hidden="true">
          <b>{parts ? parts.month : 'Any'}</b>
          <span>{parts ? parts.day : '—'}</span>
        </div>
        <div className="row-text">
          <span className="row-title" title={m.title}>{m.title}</span>
          <span className="row-meta">
            <span className="chip"><Clock size={14} />{when(m.scheduled_at)} · {duration(m.duration_minutes)}</span>
            {m.has_passcode && <span className="chip"><Lock size={14} />Passcode</span>}
            {m.lobby && <span className="chip hide-sm"><Shield size={14} />Waiting room</span>}
            {!mine && m.host_name && <span className="chip hide-sm"><People size={14} />Host: {m.host_name}</span>}
          </span>
        </div>
      </div>
      <div className="row-status">
        {live ? <span className="badge badge-live badge-dot">Live · {live}</span>
          : ended ? <span className="badge">Ended</span>
            : <span className="badge badge-brand">Scheduled</span>}
      </div>
      <div className="row-actions">
        {!ended && (
          <Button size="sm" variant={live ? 'primary' : 'secondary'} icon={<DoorIn size={16} />} onClick={start}
                  title={mine ? 'Join as host' : 'Join (no passcode or waiting room for staff)'}>
            <span className="hide-sm">{mine && !live ? 'Start' : 'Join'}</span>
          </Button>
        )}
        {!ended && (
          <Button size="sm" icon={<Copy size={16} />} onClick={() => onCopyInvite(m)} className="hide-sm">Invite</Button>
        )}
        {(!ended || onDelete) && <Menu trigger={({ toggle }) => (
          <Button size="sm" variant="ghost" icon={<More />} onClick={toggle} aria-label={`More actions for ${m.title}`} />
        )}>
          {!ended && <MenuItem icon={<Copy size={16} />} onClick={() => onCopyInvite(m)}>Copy invitation</MenuItem>}
          {!ended && <MenuItem icon={<LinkIcon size={16} />} onClick={() => onCopyLink(m)}>Copy link only</MenuItem>}
          {!ended && mine && <MenuItem icon={<Stop size={16} />} onClick={() => onEnd(m)}>End meeting</MenuItem>}
          {!ended && onDelete && <div className="menu-sep" />}
          {onDelete && <MenuItem icon={<Trash size={16} />} danger onClick={() => onDelete(m)}>Delete</MenuItem>}
        </Menu>}
      </div>
    </div>
  )
}

const DURATIONS = [15, 30, 45, 60, 90, 120, 180, 240]

function NewMeetingModal({ open, onClose, token, onCreated, fail }) {
  const [title, setTitle] = useState('')
  const [scheduled, setScheduled] = useState(nextQuarter)
  const [dur, setDur] = useState(60)
  const [usePass, setUsePass] = useState(true)
  const [passcode, setPasscode] = useState(randomPasscode)
  const [lobby, setLobby] = useState(true)
  const [show, setShow] = useState(true)
  const [busy, setBusy] = useState(false)
  const [errors, setErrors] = useState({})

  useEffect(() => {
    if (!open) return
    setTitle('')
    setScheduled(nextQuarter())
    setDur(60)
    setUsePass(true)
    setPasscode(randomPasscode())
    setLobby(true)
    setShow(true)
    setErrors({})
  }, [open])

  async function submit(e) {
    e.preventDefault()
    const errs = {}
    if (!title.trim()) errs.title = 'Give the meeting a title.'
    if (usePass && !/^\S{4,32}$/.test(passcode)) errs.passcode = 'Use 4–32 characters without spaces.'
    setErrors(errs)
    if (Object.keys(errs).length) return
    setBusy(true)
    try {
      const m = await createMeeting(token, {
        title: title.trim(),
        scheduled_at: scheduled ? new Date(scheduled).toISOString() : null,
        passcode: usePass ? passcode : null,
        lobby,
        show_on_screen: show,
        duration_minutes: Number(dur),
      })
      onCreated({ ...m, passcode: usePass ? passcode : null })
    } catch (err) {
      fail(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="New meeting" description="Guests join with the link; only you can start it as host."
           footer={<>
             <Button onClick={onClose}>Cancel</Button>
             <Button variant="primary" type="submit" form="new-meeting" loading={busy}>Create meeting</Button>
           </>}>
      <form id="new-meeting" className="form" onSubmit={submit} noValidate>
        <TextInput label="Title" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={120}
                   placeholder="e.g. Board meeting — Q4 review" error={errors.title} />
        <div className="row-2">
          <TextInput label="Date and time" type="datetime-local" value={scheduled} onChange={(e) => setScheduled(e.target.value)}
                     hint="Leave empty for a meeting without a fixed time." />
          <Field label="Duration">
            <select className="select" value={dur} onChange={(e) => setDur(e.target.value)}>
              {DURATIONS.map((d) => <option key={d} value={d}>{duration(d)}</option>)}
            </select>
          </Field>
        </div>
        <div>
          <div className="field-label" style={{ marginBottom: 2 }}>Security</div>
          <Switch checked={usePass} onChange={setUsePass} label="Require a passcode"
                  description="Guests enter it before they can join." />
          {usePass && (
            <div className="copy-field" style={{ paddingBottom: 12 }}>
              <input className="input input-mono" value={passcode} onChange={(e) => setPasscode(e.target.value.trim())}
                     aria-label="Passcode" aria-invalid={errors.passcode ? 'true' : undefined} maxLength={32} />
              <Button icon={<Refresh size={16} />} onClick={() => setPasscode(randomPasscode())} title="Generate a new passcode"
                      aria-label="Generate a new passcode" />
            </div>
          )}
          {errors.passcode && <div className="field-error" style={{ marginTop: -6, marginBottom: 8 }}>{errors.passcode}</div>}
          <Switch checked={lobby} onChange={setLobby} label="Waiting room"
                  description="Guests wait until the host lets them in." />
          <Switch checked={show} onChange={setShow} label="Show on the room screen"
                  description="Listed on the device's “Meetings in progress” page while it runs." />
        </div>
      </form>
    </Modal>
  )
}

function MeetingReadyModal({ meeting, org, onClose }) {
  const toast = useToast()
  if (!meeting) return null
  const url = meeting.url || meeting.local_url
  const copy = async (text, what) => {
    const ok = await copyText(text)
    toast(ok ? { message: `${what} copied.` } : { kind: 'error', message: 'Could not copy. Select the text and copy it manually.' })
  }
  return (
    <Modal open onClose={onClose} title="Meeting created" description={meeting.title}
           footer={<>
             <Button onClick={() => { window.location.href = `/m/${encodeURIComponent(meeting.room_name)}?host=1` }}
                     icon={<DoorIn size={16} />}>Start now</Button>
             <Button variant="primary" icon={<Copy size={16} />}
                     onClick={() => copy(inviteText({ ...meeting, url, org }), 'Invitation')}>Copy invitation</Button>
           </>}>
      <div className="form">
        <Field label="Meeting link">
          <div className="copy-field">
            <input className="input" readOnly value={url} onFocus={(e) => e.target.select()} aria-label="Meeting link" />
            <Button icon={<Copy size={16} />} onClick={() => copy(url, 'Link')} aria-label="Copy link" />
          </div>
        </Field>
        {meeting.passcode && (
          <Field label="Passcode">
            <div className="copy-field" style={{ alignItems: 'center', justifyContent: 'space-between' }}>
              <span className="secret">{meeting.passcode}</span>
              <Button size="sm" icon={<Copy size={16} />} onClick={() => copy(meeting.passcode, 'Passcode')}>Copy</Button>
            </div>
          </Field>
        )}
        <dl className="kv">
          <dt>When</dt><dd>{when(meeting.scheduled_at)} · {duration(meeting.duration_minutes)}</dd>
          <dt>Waiting room</dt><dd>{meeting.lobby ? 'On — you admit each guest' : 'Off — guests join directly'}</dd>
        </dl>
        {meeting.passcode && (
          <Notice kind="warning">Save the passcode now. For security it is not stored in readable form, so it cannot be shown again.</Notice>
        )}
      </div>
    </Modal>
  )
}

// ---------------------------------------------------------------- people

const ROLES = [
  { value: 'admin', label: 'Client Admin', description: 'Full control: people, meetings and security. A second admin can reset your password if you ever forget it.' },
  { value: 'host', label: 'Host', description: 'Creates meetings and moderates their own: admits guests, mutes, removes and ends. Joins other hosts’ meetings as staff.' },
  { value: 'member', label: 'Member', description: 'Internal staff. Joins any meeting from the console without a passcode or waiting room; cannot create or moderate meetings.' },
]

function PeoplePage({ users, me: current, token, reload, fail }) {
  const toast = useToast()
  const confirm = useConfirm()
  const [adding, setAdding] = useState(false)
  const [resetting, setResetting] = useState(null)
  const [query, setQuery] = useState('')
  const shown = (users || []).filter((u) => `${u.display_name} ${u.username}`.toLowerCase().includes(query.trim().toLowerCase()))

  async function reset2fa(u) {
    const ok = await confirm({
      title: `Reset two-factor for ${u.display_name || u.username}?`,
      message: 'Use this when they have lost their phone. Two-factor is switched off and they are signed out; they can set it up again on their next sign-in.',
      confirmLabel: 'Reset two-factor',
    })
    if (!ok) return
    try {
      await resetUser2fa(token, u.id)
      toast({ message: 'Two-factor reset.' })
      reload()
    } catch (err) { fail(err) }
  }

  async function remove(u) {
    const ok = await confirm({
      title: `Remove ${u.display_name || u.username}?`,
      message: 'They can no longer sign in. Meetings they created stay available to the Client Admin.',
      confirmLabel: 'Remove user', danger: true,
    })
    if (!ok) return
    try {
      await deleteUser(token, u.id)
      toast({ message: 'User removed.' })
      reload()
    } catch (err) { fail(err) }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>People</h1>
          <p className="page-sub">Accounts that can sign in on this device.</p>
        </div>
        <Button variant="primary" icon={<UserPlus />} onClick={() => setAdding(true)}>Add user</Button>
      </div>
      <div className="toolbar">
        <span className="muted">{users ? `${users.length} ${users.length === 1 ? 'account' : 'accounts'}` : ''}</span>
        <div className="search">
          <Search size={16} />
          <input className="input" placeholder="Search people" value={query} onChange={(e) => setQuery(e.target.value)}
                 aria-label="Search people" />
        </div>
      </div>
      <div className="card">
        {users === null ? (
          <div className="empty"><Spinner large /></div>
        ) : shown.length === 0 ? (
          <EmptyState icon={<People />} title={query ? 'No matching people' : 'No users yet'} />
        ) : (
          <div className="list">
            {shown.map((u) => (
              <div key={u.id} className="list-row">
                <div className="row-main">
                  <Avatar name={u.display_name || u.username} size={36} />
                  <div className="row-text">
                    <span className="row-title">
                      {u.display_name || u.username}
                      {u.username === current.username && <span className="subtle" style={{ fontWeight: 500 }}> · You</span>}
                    </span>
                    <span className="row-meta subtle">@{u.username}{u.totp_enabled ? ' · 2FA on' : ''}</span>
                  </div>
                </div>
                <div className="row-status">
                  <span className={`badge ${u.role === 'admin' ? 'badge-brand' : ''}`}>
                    {u.role === 'admin' ? 'Client Admin' : u.role === 'host' ? 'Host' : 'Member'}
                  </span>
                </div>
                <div className="row-actions">
                  {u.username !== current.username ? (
                    <Menu trigger={({ toggle }) => (
                      <Button size="sm" variant="ghost" icon={<More />} onClick={toggle} aria-label={`Actions for ${u.display_name || u.username}`} />
                    )}>
                      <MenuItem icon={<Lock size={16} />} onClick={() => setResetting(u)}>Reset password</MenuItem>
                      {u.totp_enabled && <MenuItem icon={<Shield size={16} />} onClick={() => reset2fa(u)}>Reset two-factor</MenuItem>}
                      {u.role !== 'admin' && <div className="menu-sep" />}
                      {u.role !== 'admin' && <MenuItem icon={<Trash size={16} />} danger onClick={() => remove(u)}>Remove</MenuItem>}
                    </Menu>
                  ) : <span style={{ width: 32 }} />}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      <ResetUserPasswordModal user={resetting} onClose={() => setResetting(null)} token={token} fail={fail} />
      <AddUserModal open={adding} onClose={() => setAdding(false)} token={token} fail={fail}
                    onAdded={(u) => { setAdding(false); toast({ message: `${u} can now sign in.` }); reload() }} />
    </div>
  )
}

function AddUserModal({ open, onClose, token, onAdded, fail }) {
  const [displayName, setDisplayName] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('host')
  const [errors, setErrors] = useState({})
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setDisplayName('')
    setUsername('')
    setPassword('')
    setRole('host')
    setErrors({})
  }, [open])

  async function submit(e) {
    e.preventDefault()
    const user = username.trim().toLowerCase()
    const errs = {}
    if (!displayName.trim()) errs.displayName = 'Enter their full name.'
    if (!USERNAME_RE.test(user)) errs.username = '3–32 characters: lowercase letters, numbers, dots, hyphens or underscores.'
    if (password.length < MIN_PASSWORD) errs.password = `At least ${MIN_PASSWORD} characters.`
    else if (password.toLowerCase() === user) errs.password = 'The password must not be the username.'
    setErrors(errs)
    if (Object.keys(errs).length) return
    setBusy(true)
    try {
      await createUser(token, { username: user, password, role, display_name: displayName.trim() })
      onAdded(displayName.trim())
    } catch (err) {
      if (err.message === 'username exists') setErrors({ username: friendly(err) })
      else fail(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Add user" description="Share the username and password with them in person or over a secure channel."
           footer={<>
             <Button onClick={onClose}>Cancel</Button>
             <Button variant="primary" type="submit" form="add-user" loading={busy}>Add user</Button>
           </>}>
      <form id="add-user" className="form" onSubmit={submit} noValidate>
        <TextInput label="Full name" value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={80}
                   placeholder="e.g. Rina Wijaya" error={errors.displayName} autoComplete="off" />
        <TextInput label="Username" value={username} onChange={(e) => setUsername(e.target.value)} maxLength={32}
                   placeholder="e.g. rina.wijaya" error={errors.username} autoCapitalize="none" spellCheck={false} autoComplete="off" />
        <PasswordInput label="Temporary password" value={password} onChange={(e) => setPassword(e.target.value)}
                       hint={`At least ${MIN_PASSWORD} characters.`} error={errors.password} autoComplete="new-password" />
        <Field label="Role">
          <Choice value={role} onChange={setRole} options={ROLES} />
        </Field>
      </form>
    </Modal>
  )
}
