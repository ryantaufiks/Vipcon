import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Room as LKRoom, RoomEvent, Track, DisconnectReason } from 'livekit-client'
import VideoTile from './VideoTile.jsx'
import { ChatPanel, PeoplePanel } from './Panels.jsx'
import { Logo, Button, Menu, MenuItem, PublicLayout, useToast, useConfirm } from './ui.jsx'
import { Mic, Camera, Screen, Chat, People, Leave, Stop, Alert, CheckCircle, Refresh, Wifi, LogOut, ArrowLeft } from './icons.jsx'
import { playJoin, playChat, playScreenShare } from './sound.js'
import {
  getToken, meetingByRoom, listLobby, admitLobby, rejectLobby, kickParticipant, muteParticipant, endMeeting,
} from './api.js'
import { elapsed, friendly } from './format.js'

function useNow(interval = 1000) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), interval)
    return () => clearInterval(t)
  }, [interval])
  return now
}

// Largest 16:9 tiles that fit the stage for n people: tries every column count.
function useTileLayout(n) {
  // Callback ref: the grid appears only once the room is connected.
  const [el, ref] = useState(null)
  const [box, setBox] = useState({ w: 0, h: 0 })
  useEffect(() => {
    if (!el) return
    const ro = new ResizeObserver(([e]) => setBox({ w: e.contentRect.width, h: e.contentRect.height }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [el])
  const gap = 10
  let best = { cols: 1, w: 0 }
  const count = Math.max(1, n)
  for (let cols = 1; cols <= count; cols++) {
    const rows = Math.ceil(count / cols)
    const w = Math.min((box.w - gap * (cols - 1)) / cols, ((box.h - gap * (rows - 1)) / rows) * 16 / 9)
    if (w > best.w) best = { cols, w }
  }
  return { ref, cols: best.cols, w: Math.floor(Math.max(0, best.w)) }
}

const canShareScreen = !!navigator.mediaDevices?.getDisplayMedia

export default function Room({ session, onLeave }) {
  const toast = useToast()
  const confirm = useConfirm()
  const roomRef = useRef(null)
  const leavingRef = useRef(false)
  const onLeaveRef = useRef(onLeave)
  onLeaveRef.current = onLeave
  const [participants, setParticipants] = useState([])
  const [connected, setConnected] = useState(false)
  const [reconnecting, setReconnecting] = useState(false)
  const [error, setError] = useState('')
  const [version, setVersion] = useState(0)
  const [panel, setPanel] = useState(null) // 'chat' | 'people' | null
  const panelRef = useRef(null)
  const [messages, setMessages] = useState([])
  const [unread, setUnread] = useState(0)
  const [meetingId, setMeetingId] = useState(null)
  const [lobby, setLobby] = useState([])
  const seenLobby = useRef(new Set())
  const now = useNow()

  const refresh = useCallback(() => {
    const room = roomRef.current
    if (!room) return
    setParticipants([room.localParticipant, ...room.remoteParticipants.values()])
    setVersion((v) => v + 1)
  }, [])

  useEffect(() => {
    const room = new LKRoom({ adaptiveStream: true, dynacast: true })
    roomRef.current = room
    leavingRef.current = false
    let closed = false // this connection was closed by us (unmount, leave): its events are stale

    const onData = (payload) => {
      try {
        const msg = JSON.parse(new TextDecoder().decode(payload))
        if (typeof msg.text !== 'string') return
        setMessages((m) => [...m, { from: String(msg.from || 'Guest'), text: msg.text.slice(0, 2000), ts: Number(msg.ts) || Date.now() }])
        if (panelRef.current !== 'chat') setUnread((u) => u + 1)
        playChat()
      } catch { /* not a chat message */ }
    }

    room
      .on(RoomEvent.TrackSubscribed, (track, pub) => {
        refresh()
        if (pub?.source === Track.Source.ScreenShare) playScreenShare()
      })
      .on(RoomEvent.TrackUnsubscribed, refresh)
      .on(RoomEvent.TrackMuted, refresh)
      .on(RoomEvent.TrackUnmuted, refresh)
      .on(RoomEvent.TrackPublished, refresh)
      .on(RoomEvent.TrackUnpublished, refresh)
      .on(RoomEvent.LocalTrackPublished, refresh)
      .on(RoomEvent.LocalTrackUnpublished, refresh)
      .on(RoomEvent.ParticipantConnected, (p) => { refresh(); playJoin(); toast({ kind: 'info', message: `${p.identity} joined`, key: 'join' }) })
      .on(RoomEvent.ParticipantDisconnected, refresh)
      .on(RoomEvent.DataReceived, onData)
      .on(RoomEvent.Reconnecting, () => setReconnecting(true))
      .on(RoomEvent.Reconnected, () => { setReconnecting(false); refresh() })
      .on(RoomEvent.Disconnected, (reason) => {
        if (closed || leavingRef.current) return
        const done = onLeaveRef.current
        if (reason === DisconnectReason.PARTICIPANT_REMOVED) done('removed')
        else if (reason === DisconnectReason.ROOM_DELETED) done('ended')
        else if (reason === DisconnectReason.DUPLICATE_IDENTITY) done('duplicate')
        else done('lost')
      })

    // Through the device (https) LiveKit's signalling is proxied on the same host.
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const wsUrl = session.wsUrl?.startsWith('wss://') ? session.wsUrl : `${proto}://${window.location.host}`
    room.connect(wsUrl, session.token)
      .then(async () => {
        setConnected(true)
        refresh()
        const lp = room.localParticipant
        if (session.micOn !== false) {
          try { await lp.setMicrophoneEnabled(true, session.audioId ? { deviceId: session.audioId } : undefined) } catch { /* no mic */ }
        }
        if (session.camOn !== false) {
          try { await lp.setCameraEnabled(true, session.videoId ? { deviceId: session.videoId } : undefined) } catch { /* no camera */ }
        }
        refresh()
      })
      .catch((e) => setError(e?.message || 'Could not connect'))

    return () => {
      closed = true
      room.disconnect()
      if (roomRef.current === room) roomRef.current = null
    }
  }, [session, refresh, toast])

  // Host: meeting id and the waiting room, with a heads-up when someone new is waiting.
  useEffect(() => {
    if (!session.isHost) return
    let cancelled = false
    let id = null
    async function load() {
      try {
        if (!id) {
          id = (await meetingByRoom(session.room, getToken())).id
          if (!cancelled) setMeetingId(id)
        }
        const l = await listLobby(getToken(), id)
        if (cancelled) return
        setLobby(l)
        for (const r of l) {
          if (seenLobby.current.has(r.id)) continue
          seenLobby.current.add(r.id)
          playJoin()
          toast({
            kind: 'info', duration: 0, key: `lobby-${r.id}`, message: `${r.identity} is waiting to join`,
            actions: [
              { label: 'Admit', primary: true, onClick: () => admit(r.id) },
              { label: 'View', onClick: () => setPanel('people') },
            ],
          })
        }
      } catch { /* retried */ }
    }
    load()
    const t = setInterval(load, 3000)
    return () => { cancelled = true; clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session])

  useEffect(() => {
    panelRef.current = panel
    if (panel === 'chat') setUnread(0)
  }, [panel])

  const lp = roomRef.current?.localParticipant
  const micOn = !!lp?.isMicrophoneEnabled
  const camOn = !!lp?.isCameraEnabled
  const screenOn = !!lp?.isScreenShareEnabled

  async function toggle(kind) {
    const p = roomRef.current?.localParticipant
    if (!p) return
    try {
      if (kind === 'mic') await p.setMicrophoneEnabled(!p.isMicrophoneEnabled)
      if (kind === 'cam') await p.setCameraEnabled(!p.isCameraEnabled)
      if (kind === 'screen') await p.setScreenShareEnabled(!p.isScreenShareEnabled, { audio: true })
    } catch (e) {
      if (e?.name !== 'NotAllowedError') {
        toast({ kind: 'error', message: kind === 'screen' ? 'Screen sharing could not start.' : `Could not turn the ${kind === 'mic' ? 'microphone' : 'camera'} on. Check your browser’s permissions.` })
      }
    }
    refresh()
  }

  function sendMessage(text) {
    const msg = { from: session.identity, text, ts: Date.now() }
    roomRef.current?.localParticipant.publishData(new TextEncoder().encode(JSON.stringify(msg)), { reliable: true }).catch(() => {
      toast({ kind: 'error', message: 'Message not sent. Check your connection.' })
    })
    setMessages((m) => [...m, msg])
  }

  async function admit(reqId) {
    try {
      const id = meetingId || (await meetingByRoom(session.room, getToken())).id
      await admitLobby(getToken(), id, reqId)
      setLobby((l) => l.filter((r) => r.id !== reqId))
    } catch (err) { toast({ kind: 'error', message: friendly(err) }) }
  }
  async function reject(reqId) {
    try {
      await rejectLobby(getToken(), meetingId, reqId)
      setLobby((l) => l.filter((r) => r.id !== reqId))
    } catch (err) { toast({ kind: 'error', message: friendly(err) }) }
  }
  async function admitAll() {
    for (const r of lobby) await admit(r.id)
  }
  async function kick(identity) {
    const ok = await confirm({ title: `Remove ${identity}?`, message: 'They leave the meeting now. They can ask to join again if they still have the link.', confirmLabel: 'Remove', danger: true })
    if (!ok) return
    try { await kickParticipant(getToken(), meetingId, identity) } catch (err) { toast({ kind: 'error', message: friendly(err) }) }
  }
  async function mute(identity) {
    try {
      await muteParticipant(getToken(), meetingId, identity, true)
      toast({ message: `${identity} is muted.` })
    } catch (err) { toast({ kind: 'error', message: friendly(err) }) }
  }

  function leave() {
    leavingRef.current = true
    roomRef.current?.disconnect()
    onLeave('left')
  }
  async function endForAll() {
    const ok = await confirm({ title: 'End the meeting for everyone?', message: 'Everyone is disconnected and the link stops working.', confirmLabel: 'End meeting', danger: true })
    if (!ok) return
    try {
      await endMeeting(getToken(), meetingId)
      leavingRef.current = true
      roomRef.current?.disconnect()
      onLeave('ended-by-you')
    } catch (err) { toast({ kind: 'error', message: friendly(err) }) }
  }

  const layout = useTileLayout(participants.length)

  const sharer = useMemo(
    () => participants.find((p) => p.getTrackPublication(Track.Source.ScreenShare)?.track),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [participants, version],
  )

  if (error) {
    return (
      <PublicLayout footer={false}>
        <div className="state-card">
          <div className="state-icon danger"><Wifi size={26} /></div>
          <h1 className="state-title">Couldn’t connect to the meeting</h1>
          <p className="muted">Check that you are on a network that can reach the VIPCON device, then try again.</p>
          <p className="subtle mono" style={{ fontSize: 12 }}>{error}</p>
          <div className="state-actions">
            <Button variant="primary" icon={<Refresh size={16} />} onClick={() => window.location.reload()}>Try again</Button>
          </div>
        </div>
      </PublicLayout>
    )
  }

  const tiles = participants.map((p) => (
    <VideoTile key={p.sid || p.identity} participant={p} version={version} isLocal={p === lp} />
  ))
  const title = session.title || 'Meeting'

  return (
    <div className="room" data-theme="dark">
      <header className="room-top">
        <Logo onDark />
        <div className="room-info">
          <span className="room-title" title={title}>{title}</span>
        </div>
        <span className="room-pill"><People size={14} />{participants.length || 1}</span>
        <span className="room-pill hide-md">{elapsed(now - (session.joinedAt || now))}</span>
      </header>
      {reconnecting && (
        <div className="room-banner" role="status"><Refresh size={16} /> Connection lost — reconnecting…</div>
      )}

      <div className="room-body">
        <div className="stage">
          {!connected ? (
            <div className="room-center">
              <div className="state-card" style={{ color: '#a1a1aa' }}><span className="spinner spinner-lg" />Joining…</div>
            </div>
          ) : sharer ? (
            <div className="spotlight">
              <div className="spotlight-main">
                <VideoTile participant={sharer} version={version} isLocal={sharer === lp} source="screen" />
              </div>
              <div className="filmstrip">{tiles}</div>
            </div>
          ) : (
            <div className="grid" ref={layout.ref}
                 style={{ gridTemplateColumns: `repeat(${layout.cols}, ${layout.w || 1}px)` }}>{tiles}</div>
          )}
        </div>
        {panel === 'chat' && <ChatPanel messages={messages} me={session.identity} onSend={sendMessage} onClose={() => setPanel(null)} />}
        {panel === 'people' && (
          <PeoplePanel participants={participants} localIdentity={session.identity} isHost={session.isHost} lobby={lobby}
                       onAdmit={admit} onReject={reject} onAdmitAll={admitAll} onKick={kick} onMute={mute}
                       onClose={() => setPanel(null)} />
        )}
      </div>

      <footer className="controls">
        <div className="controls-side hide-md">
          <span className="clock">{new Date(now).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
        </div>
        <div className="controls-center">
          <button type="button" className={`cbtn ${micOn ? '' : 'off'}`} onClick={() => toggle('mic')}
                  aria-pressed={!micOn} title={micOn ? 'Mute' : 'Unmute'} aria-label={micOn ? 'Mute microphone' : 'Unmute microphone'}>
            <Mic off={!micOn} size={20} />
          </button>
          <button type="button" className={`cbtn ${camOn ? '' : 'off'}`} onClick={() => toggle('cam')}
                  aria-pressed={!camOn} title={camOn ? 'Stop video' : 'Start video'} aria-label={camOn ? 'Turn camera off' : 'Turn camera on'}>
            <Camera off={!camOn} size={20} />
          </button>
          {canShareScreen && (
            <button type="button" className={`cbtn hide-md ${screenOn ? 'on' : ''}`} onClick={() => toggle('screen')}
                    aria-pressed={screenOn} title={screenOn ? 'Stop sharing' : 'Share screen'}>
              <Screen size={20} /><span className="cbtn-label">{screenOn ? 'Stop sharing' : 'Share'}</span>
            </button>
          )}
          <button type="button" className={`cbtn ${panel === 'chat' ? 'on' : ''}`} onClick={() => setPanel((p) => (p === 'chat' ? null : 'chat'))}
                  aria-pressed={panel === 'chat'} title="Chat" aria-label="Chat">
            <Chat size={20} />
            {unread > 0 && <span className="count">{unread > 99 ? '99+' : unread}</span>}
          </button>
          <button type="button" className={`cbtn ${panel === 'people' ? 'on' : ''}`} onClick={() => setPanel((p) => (p === 'people' ? null : 'people'))}
                  aria-pressed={panel === 'people'} title="People" aria-label="People">
            <People size={20} />
            {session.isHost && lobby.length > 0 && <span className="count">{lobby.length}</span>}
          </button>
          {session.isHost ? (
            <Menu up trigger={({ toggle: t }) => (
              <button type="button" className="cbtn leave" onClick={t} title="Leave or end" aria-label="Leave or end the meeting">
                <Leave size={20} /><span className="cbtn-label">Leave</span>
              </button>
            )}>
              <MenuItem icon={<LogOut size={16} />} onClick={leave}>Leave meeting</MenuItem>
              <MenuItem icon={<Stop size={16} />} danger onClick={endForAll}>End meeting for everyone</MenuItem>
            </Menu>
          ) : (
            <button type="button" className="cbtn leave" onClick={leave} title="Leave meeting" aria-label="Leave meeting">
              <Leave size={20} /><span className="cbtn-label">Leave</span>
            </button>
          )}
        </div>
        <div className="controls-side right hide-md" />
      </footer>
    </div>
  )
}

const LEFT = {
  left: { icon: <CheckCircle size={26} />, tone: 'brand', title: 'You left the meeting', text: 'You can rejoin while the meeting is still running.' },
  'ended-by-you': { icon: <CheckCircle size={26} />, tone: 'brand', title: 'Meeting ended', text: 'Everyone has been disconnected and the link no longer works.' },
  ended: { icon: <Stop size={26} />, tone: '', title: 'The host ended the meeting', text: 'Thank you for joining.' },
  removed: { icon: <Alert size={26} />, tone: 'danger', title: 'You were removed from the meeting', text: 'The host removed you. Contact them if this was a mistake.' },
  duplicate: { icon: <Alert size={26} />, tone: 'danger', title: 'You joined from another window', text: 'This window was disconnected because the same name joined elsewhere.' },
  lost: { icon: <Wifi size={26} />, tone: 'danger', title: 'Connection lost', text: 'Your connection to the meeting dropped. Check your network and rejoin.' },
}

export function Left({ info, isHost, onRejoin }) {
  const s = LEFT[info.reason] || LEFT.left
  const canRejoin = ['left', 'lost', 'duplicate'].includes(info.reason)
  return (
    <PublicLayout>
      <div className="state-card">
        <div className={`state-icon ${s.tone}`}>{s.icon}</div>
        <h1 className="state-title">{s.title}</h1>
        <p className="muted">{s.text}</p>
        <div className="state-actions">
          {canRejoin && <Button variant="primary" icon={<Refresh size={16} />} onClick={onRejoin}>Rejoin</Button>}
          {isHost && <Button icon={<ArrowLeft size={16} />} onClick={() => { window.location.href = '/admin' }}>Back to meetings</Button>}
        </div>
      </div>
    </PublicLayout>
  )
}
