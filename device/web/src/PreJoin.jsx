import { useCallback, useEffect, useRef, useState } from 'react'
import { joinRoom, hostToken, lobbyStatus, getToken, getUsername, meetingInfo, me } from './api.js'
import { PublicLayout, Button, TextInput, PasswordInput, Field, Notice, Avatar, Spinner } from './ui.jsx'
import { Mic, Camera, Lock, Shield, Clock, Alert, ArrowLeft } from './icons.jsx'
import { when, duration, friendly } from './format.js'

const NAME_KEY = 'vipcon-display-name'

// Camera and microphone preview with device choice; the choice is carried into the meeting.
function useMediaPreview() {
  const [stream, setStream] = useState(null)
  const [micOn, setMicOn] = useState(true)
  const [camOn, setCamOn] = useState(true)
  const [devices, setDevices] = useState({ audio: [], video: [] })
  const [audioId, setAudioId] = useState('')
  const [videoId, setVideoId] = useState('')
  const [blocked, setBlocked] = useState('')
  const [level, setLevel] = useState(0)
  const streamRef = useRef(null)

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    setStream(null)
  }, [])

  useEffect(() => {
    let cancelled = false
    async function start() {
      if (!navigator.mediaDevices?.getUserMedia) {
        setBlocked('This browser cannot use a camera or microphone on this page. You can still join and listen.')
        return
      }
      try {
        const s = await navigator.mediaDevices.getUserMedia({
          audio: audioId ? { deviceId: { exact: audioId } } : true,
          video: videoId ? { deviceId: { exact: videoId }, width: 1280, height: 720 } : { width: 1280, height: 720 },
        })
        if (cancelled) { s.getTracks().forEach((t) => t.stop()); return }
        streamRef.current?.getTracks().forEach((t) => t.stop())
        streamRef.current = s
        setStream(s)
        setBlocked('')
        const list = await navigator.mediaDevices.enumerateDevices()
        if (!cancelled) {
          setDevices({ audio: list.filter((d) => d.kind === 'audioinput'), video: list.filter((d) => d.kind === 'videoinput') })
          if (!audioId) setAudioId(s.getAudioTracks()[0]?.getSettings().deviceId || '')
          if (!videoId) setVideoId(s.getVideoTracks()[0]?.getSettings().deviceId || '')
        }
      } catch (err) {
        if (cancelled) return
        setBlocked(err?.name === 'NotAllowedError'
          ? 'Camera and microphone are blocked. Allow them in your browser’s address bar, or join without them.'
          : 'No camera or microphone was found. You can still join and listen.')
      }
    }
    start()
    return () => { cancelled = true }
  }, [audioId, videoId])

  useEffect(() => () => streamRef.current?.getTracks().forEach((t) => t.stop()), [])

  useEffect(() => {
    stream?.getAudioTracks().forEach((t) => { t.enabled = micOn })
    stream?.getVideoTracks().forEach((t) => { t.enabled = camOn })
  }, [stream, micOn, camOn])

  // Microphone level, so people can see their mic works before joining.
  useEffect(() => {
    if (!stream || !stream.getAudioTracks().length) return
    const AC = window.AudioContext || window.webkitAudioContext
    if (!AC) return
    const ctx = new AC()
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 256
    ctx.createMediaStreamSource(stream).connect(analyser)
    const data = new Uint8Array(analyser.frequencyBinCount)
    let raf
    const tick = () => {
      analyser.getByteTimeDomainData(data)
      let peak = 0
      for (const v of data) peak = Math.max(peak, Math.abs(v - 128))
      setLevel(Math.min(1, peak / 64))
      raf = requestAnimationFrame(tick)
    }
    tick()
    return () => { cancelAnimationFrame(raf); ctx.close() }
  }, [stream])

  return { stream, micOn, setMicOn, camOn, setCamOn, devices, audioId, setAudioId, videoId, setVideoId, blocked, level, stop }
}

function Preview({ media, name }) {
  const videoRef = useRef(null)
  useEffect(() => {
    if (videoRef.current) videoRef.current.srcObject = media.stream
  }, [media.stream])
  const showVideo = media.stream && media.camOn && media.stream.getVideoTracks().length > 0
  return (
    <div>
      <div className="preview">
        <video ref={videoRef} autoPlay playsInline muted style={{ display: showVideo ? 'block' : 'none' }} />
        {!showVideo && (
          <div className="preview-off">
            <Avatar name={name || 'Guest'} size={88} />
            <span>{media.blocked ? 'Camera unavailable' : media.camOn ? 'Starting camera…' : 'Camera is off'}</span>
          </div>
        )}
        {name && <span className="preview-name">{name}</span>}
        <div className="preview-bar">
          <button type="button" className={`mbtn ${media.micOn && !media.blocked ? '' : 'off'}`} onClick={() => media.setMicOn((v) => !v)}
                  disabled={!!media.blocked} aria-pressed={!media.micOn} title={media.micOn ? 'Turn microphone off' : 'Turn microphone on'}
                  aria-label={media.micOn ? 'Turn microphone off' : 'Turn microphone on'}>
            <Mic off={!media.micOn || !!media.blocked} size={20} />
          </button>
          <button type="button" className={`mbtn ${media.camOn && !media.blocked ? '' : 'off'}`} onClick={() => media.setCamOn((v) => !v)}
                  disabled={!!media.blocked} aria-pressed={!media.camOn} title={media.camOn ? 'Turn camera off' : 'Turn camera on'}
                  aria-label={media.camOn ? 'Turn camera off' : 'Turn camera on'}>
            <Camera off={!media.camOn || !!media.blocked} size={20} />
          </button>
        </div>
      </div>
      {media.blocked ? (
        <div style={{ marginTop: 12 }}><Notice kind="warning">{media.blocked}</Notice></div>
      ) : (
        <div className="devices">
          <Field label={<span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>Microphone
            <span className="level" aria-hidden="true"><i style={{ width: `${media.micOn ? media.level * 100 : 0}%` }} /></span></span>}>
            <select className="select" value={media.audioId} onChange={(e) => media.setAudioId(e.target.value)} aria-label="Microphone">
              {media.devices.audio.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `Microphone ${i + 1}`}</option>)}
            </select>
          </Field>
          <Field label="Camera">
            <select className="select" value={media.videoId} onChange={(e) => media.setVideoId(e.target.value)} aria-label="Camera">
              {media.devices.video.map((d, i) => <option key={d.deviceId || i} value={d.deviceId}>{d.label || `Camera ${i + 1}`}</option>)}
            </select>
          </Field>
        </div>
      )}
    </div>
  )
}

// signedIn: opened from the console (the account joins directly); asHost: it is your own meeting.
export default function PreJoin({ room, signedIn, asHost, onJoined }) {
  const [info, setInfo] = useState(null) // null loading, {error} when the link is invalid
  const [name, setName] = useState(() => (signedIn ? getUsername() : localStorage.getItem(NAME_KEY) || ''))
  const [passcode, setPasscode] = useState('')
  const [busy, setBusy] = useState(false)
  const [waiting, setWaiting] = useState(false)
  const [error, setError] = useState('')
  const [nameError, setNameError] = useState('')
  const media = useMediaPreview()

  useEffect(() => {
    if (signedIn) me(getToken()).then((u) => { if (u.display_name) setName((n) => (n === getUsername() ? u.display_name : n)) }).catch(() => {})
  }, [signedIn])

  useEffect(() => {
    meetingInfo(room).then(setInfo).catch((err) => setInfo({ error: err.status === 404 ? 'gone' : friendly(err) }))
  }, [room])

  const enter = useCallback((data) => {
    media.stop()
    onJoined({
      room, identity: name.trim(), token: data.token, wsUrl: data.ws_url, isHost: !!data.host, signedIn,
      title: info?.title || '', micOn: media.micOn, camOn: media.camOn,
      audioId: media.audioId, videoId: media.videoId, joinedAt: Date.now(),
    })
  }, [media, onJoined, room, name, signedIn, info])

  async function submit(e) {
    e.preventDefault()
    const n = name.trim()
    if (!n) {
      setNameError('Enter the name others will see.')
      return
    }
    setNameError('')
    setError('')
    if (!signedIn) localStorage.setItem(NAME_KEY, n)
    setBusy(true)
    try {
      if (signedIn) {
        enter(await hostToken(room, n, getToken()))
      } else {
        const data = await joinRoom(room, n, passcode.trim())
        if (data.status === 'waiting') setWaiting(true)
        else enter(data)
      }
    } catch (err) {
      setError(friendly(err))
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
        if (data.token) enter(data)
        else if (data.status === 'rejected') {
          setWaiting(false)
          setError('The host declined your request to join.')
        }
      } catch { /* keep waiting */ }
    }
    poll()
    const t = setInterval(poll, 2000)
    return () => { cancelled = true; clearInterval(t) }
  }, [waiting, room, name, enter])

  if (info?.error === 'gone') {
    return (
      <PublicLayout>
        <div className="state-card">
          <div className="state-icon"><Alert size={26} /></div>
          <h1 className="state-title">This meeting is not available</h1>
          <p className="muted">The link may be wrong, or the meeting has ended. Check the link with the person who invited you.</p>
        </div>
      </PublicLayout>
    )
  }

  return (
    <PublicLayout footer={false}>
      <div className="prejoin">
        <Preview media={media} name={name.trim()} />
        <div className="join-panel">
          {info === null ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }} className="muted"><Spinner /> Loading meeting…</div>
          ) : (
            <div>
              {info.org_name && <div className="join-org">{info.org_name}</div>}
              <h1 className="join-title">{info.title || 'Meeting'}</h1>
              {!info.error && (
                <div className="chips" style={{ marginTop: 10, gap: '6px 14px' }}>
                  {info.scheduled_at && <span className="chip"><Clock size={14} />{when(info.scheduled_at)} · {duration(info.duration_minutes)}</span>}
                  {info.has_passcode && !signedIn && <span className="chip"><Lock size={14} />Passcode required</span>}
                  {info.lobby && !signedIn && <span className="chip"><Shield size={14} />The host admits guests</span>}
                </div>
              )}
              {info.error && <div style={{ marginTop: 12 }}><Notice kind="warning">{info.error}</Notice></div>}
            </div>
          )}

          {waiting ? (
            <div className="waiting" role="status">
              <div className="waiting-head"><Spinner /> Waiting for the host to let you in</div>
              <p className="muted">You’ll join automatically. Keep this page open.</p>
              <Button variant="ghost" size="sm" icon={<ArrowLeft size={16} />} onClick={() => setWaiting(false)}>Cancel</Button>
            </div>
          ) : (
            <form className="form" onSubmit={submit} noValidate>
              <TextInput label="Your name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60}
                         placeholder="How others will see you" autoComplete="name" error={nameError}
                         autoFocus={!name} />
              {!signedIn && info?.has_passcode !== false && (
                <PasswordInput label="Passcode" value={passcode} onChange={(e) => setPasscode(e.target.value)}
                               autoComplete="off" hint="From your meeting invitation." />
              )}
              {error && <Notice kind="error">{error}</Notice>}
              <Button type="submit" variant="primary" size="lg" block loading={busy} disabled={info?.locked}>
                {asHost ? 'Start meeting' : signedIn ? 'Join meeting' : info?.lobby ? 'Ask to join' : 'Join meeting'}
              </Button>
              {info?.locked && <p className="field-hint">The host has locked this meeting.</p>}
              {asHost && <p className="field-hint">You join as host and can admit guests from the People panel.</p>}
              {signedIn && !asHost && <p className="field-hint">You’re signed in, so you join directly — no passcode or waiting room.</p>}
            </form>
          )}
        </div>
      </div>
    </PublicLayout>
  )
}
