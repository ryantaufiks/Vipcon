// End-to-end encryption keys for meetings.
//
// The key is made in the host's browser when an encrypted meeting is created and travels only in
// the invitation link's fragment (…/m/<room>#k=<key>). Browsers never send the fragment to a server,
// so the device (which relays the media) never has it and cannot see or hear the meeting. Each
// browser encrypts what it sends and decrypts what it receives (LiveKit E2EE, insertable streams).
import { isE2EESupported } from 'livekit-client'

const STORE = 'vipcon-e2ee:' // + room. localStorage on the host's browser, sessionStorage for guests

export function e2eeSupported() {
  try { return isE2EESupported() } catch { return false }
}

export function newMeetingKey() {
  const bytes = crypto.getRandomValues(new Uint8Array(32))
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export function withKey(url, key) {
  return key ? `${url.split('#')[0]}#k=${key}` : url
}

// The creator's browser keeps the key so "Start", "Copy invitation" and "Copy link" can include it.
export function rememberKey(room, key) {
  try { localStorage.setItem(STORE + room, key) } catch { /* private mode: the saved invitation is the copy */ }
}

export function knownKey(room) {
  try { return localStorage.getItem(STORE + room) || sessionStorage.getItem(STORE + room) || '' } catch { return '' }
}

// On a meeting page: take #k=… out of the address bar (so it does not show in a screen share or
// get copied around by accident) and keep it for this tab, so "Rejoin" works.
export function takeKeyFromLink(room) {
  const m = window.location.hash.match(/[#&]k=([A-Za-z0-9_-]{20,})/)
  if (m) {
    try { sessionStorage.setItem(STORE + room, m[1]) } catch { /* fine */ }
    window.history.replaceState(null, '', window.location.pathname + window.location.search)
    return m[1]
  }
  return knownKey(room)
}

export function normalizeKey(text) {
  const t = (text || '').trim()
  const m = t.match(/[#&]k=([A-Za-z0-9_-]{20,})/) // a whole pasted link
  return m ? m[1] : t.replace(/\s/g, '')
}
