const AUTH_KEY = 'vipcon-auth'
const USER_KEY = 'vipcon-user'

export function getToken() {
  return localStorage.getItem(AUTH_KEY)
}

export function getUsername() {
  return localStorage.getItem(USER_KEY) || ''
}

export function saveAuth(token, username) {
  localStorage.setItem(AUTH_KEY, token)
  localStorage.setItem(USER_KEY, username)
}

export function clearAuth() {
  localStorage.removeItem(AUTH_KEY)
  localStorage.removeItem(USER_KEY)
}

async function api(method, path, body, token) {
  const headers = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = 'Bearer ' + token
  const res = await fetch('/api/v1' + path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.detail || 'Request failed')
  return data
}

export async function joinRoom(room, identity, passcode) {
  return api('POST', '/join', { room, identity, passcode: passcode || null })
}

export async function hostToken(room, identity, token) {
  return api('POST', '/token', { room, identity }, token)
}

export async function lobbyStatus(room, identity) {
  return api('GET', `/lobby/status?room=${encodeURIComponent(room)}&identity=${encodeURIComponent(identity)}`)
}

export async function login(username, password) {
  return api('POST', '/login', { username, password })
}

export async function listMeetings(token) {
  return api('GET', '/meetings', undefined, token)
}

export async function createMeeting(token, data) {
  return api('POST', '/meetings', data, token)
}

export async function endMeeting(token, id) {
  return api('POST', `/meetings/${id}/end`, undefined, token)
}

export async function deleteMeeting(token, id) {
  return api('DELETE', `/meetings/${id}`, undefined, token)
}

export async function listUsers(token) {
  return api('GET', '/users', undefined, token)
}

export async function createUser(token, data) {
  return api('POST', '/users', data, token)
}

export async function deleteUser(token, id) {
  return api('DELETE', `/users/${id}`, undefined, token)
}

export async function meetingByRoom(room, token) {
  return api('GET', `/meetings/by-room/${room}`, undefined, token)
}

export async function listLobby(token, meetingId) {
  return api('GET', `/meetings/${meetingId}/lobby`, undefined, token)
}

export async function admitLobby(token, meetingId, reqId) {
  return api('POST', `/meetings/${meetingId}/lobby/${reqId}/admit`, undefined, token)
}

export async function rejectLobby(token, meetingId, reqId) {
  return api('POST', `/meetings/${meetingId}/lobby/${reqId}/reject`, undefined, token)
}

export async function kickParticipant(token, meetingId, identity) {
  return api('POST', `/meetings/${meetingId}/moderate/kick`, { identity }, token)
}

export async function muteParticipant(token, meetingId, identity, muted) {
  return api('POST', `/meetings/${meetingId}/moderate/mute`, { identity, muted }, token)
}

export async function claimStatus() {
  return api('GET', '/claim/status')
}

export async function claimDevice(data) {
  return api('POST', '/claim', data)
}

export async function meetingsNow() {
  return api('GET', '/meetings/now')
}
