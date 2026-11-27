// Formatting and wording shared by the pages.

const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()

export function time(d) {
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

// "Today, 14:30" · "Tomorrow, 09:00" · "Mon 27 Nov, 14:30" (year added when it isn't this year)
export function when(iso) {
  if (!iso) return 'No fixed time'
  const d = new Date(iso)
  const now = new Date()
  const tomorrow = new Date(now)
  tomorrow.setDate(now.getDate() + 1)
  const yesterday = new Date(now)
  yesterday.setDate(now.getDate() - 1)
  if (sameDay(d, now)) return `Today, ${time(d)}`
  if (sameDay(d, tomorrow)) return `Tomorrow, ${time(d)}`
  if (sameDay(d, yesterday)) return `Yesterday, ${time(d)}`
  const opts = { weekday: 'short', day: 'numeric', month: 'short' }
  if (d.getFullYear() !== now.getFullYear()) opts.year = 'numeric'
  return `${d.toLocaleDateString([], opts)}, ${time(d)}`
}

export function dateParts(iso) {
  if (!iso) return null
  const d = new Date(iso)
  return { month: d.toLocaleDateString([], { month: 'short' }), day: d.getDate() }
}

export function duration(min) {
  const m = Number(min) || 0
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  const r = m % 60
  return r ? `${h} h ${r} min` : `${h} h`
}

export function elapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

export function initials(name) {
  const parts = String(name || '?').trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

export function hue(name) {
  let h = 0
  for (const c of String(name || '')) h = (h * 31 + c.charCodeAt(0)) % 360
  return h
}

// Datetime-local value for "now, rounded up to the next quarter hour".
export function nextQuarter() {
  const d = new Date()
  d.setSeconds(0, 0)
  d.setMinutes(Math.ceil((d.getMinutes() + 1) / 15) * 15)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export function randomPasscode() {
  const a = new Uint32Array(1)
  crypto.getRandomValues(a)
  return String(a[0] % 1000000).padStart(6, '0')
}

export function inviteText({ title, url, passcode, scheduled_at, duration_minutes, org }) {
  const lines = [`${org ? org + ' invites you to a VIPCON meeting' : 'You are invited to a VIPCON meeting'}`, '', title]
  if (scheduled_at) lines.push(`${when(scheduled_at)} (${duration(duration_minutes)})`)
  lines.push('', `Join: ${url}`)
  if (passcode) lines.push(`Passcode: ${passcode}`)
  return lines.join('\n')
}

// Backend messages in words a guest or host can act on.
const MESSAGES = {
  'invalid passcode': 'That passcode is not correct. Check it with the host and try again.',
  'room locked': 'The host has locked this meeting. No one else can join right now.',
  'unknown room': 'This meeting link is not valid. Check the link with the person who sent it.',
  'invalid credentials': 'The username or password is not correct.',
  locked: 'Too many failed attempts. This account is locked for 15 minutes.',
  'username exists': 'That username is already taken.',
  'cannot delete self': 'You cannot remove your own account.',
  'admin only': 'Only the Client Admin can do this.',
  'host only': 'Only the meeting host or the Client Admin can do this.',
  'missing token': 'Your session has expired. Please sign in again.',
  'invalid token': 'Your session has expired. Please sign in again.',
  'unknown user': 'Your session has expired. Please sign in again.',
  'Request failed': 'Something went wrong. Please try again.',
  'Failed to fetch': 'Cannot reach the VIPCON device. Check your network connection.',
  'current password incorrect': 'That password is not correct.',
  'invalid recovery code': 'That recovery code is not valid for this account, or it has already been used.',
  'too many attempts': 'Too many attempts. Wait 15 minutes, then try again.',
  'invalid screen code': 'That code does not match the one on the device screen.',
  'no reset code on screen': 'There is no valid code on the device screen. Press and hold the screen for 8 seconds to show a new one.',
  'screen reset disabled': 'Resetting from the device screen has been switched off by a Client Admin.',
  'office network only': 'This only works on the office network.',
  'backup already on': 'Backups are already on.',
  'use change password for your own account': 'Use “Change password” for your own account.',
}

export function friendly(err) {
  const msg = typeof err === 'string' ? err : err?.message || ''
  if (MESSAGES[msg]) return MESSAGES[msg]
  if (/user limit reached/.test(msg)) return `Your plan's user limit has been reached ${msg.replace(/^.*?(\(\d+\))$/, '$1')}. Remove a user first.`
  if (/meeting limit reached/.test(msg)) return 'Your plan allows only a few open meetings at once. End a meeting you no longer need, then try again.'
  if (/NetworkError|Failed to fetch|Load failed/i.test(msg)) return MESSAGES['Failed to fetch']
  return msg || 'Something went wrong. Please try again.'
}

export function sessionExpired(err) {
  return ['missing token', 'invalid token', 'unknown user'].includes(err?.message)
}
