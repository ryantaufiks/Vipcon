import { useEffect, useRef, useState } from 'react'
import { Room as LKRoom, RoomEvent, Track } from 'livekit-client'
import VideoTile from './VideoTile.jsx'
import ControlBar from './ControlBar.jsx'
import { ChatPanel, PeoplePanel } from './Panels.jsx'
import { Sun, Moon } from './icons.jsx'
import { playJoin, playChat, playScreenShare } from './sound.js'
import {
  getToken, meetingByRoom, listLobby, admitLobby, rejectLobby, kickParticipant, muteParticipant,
} from './api.js'

export default function Room({ session, theme, onToggleTheme, onLeave }) {
  const roomRef = useRef(null)
  const [participants, setParticipants] = useState([])
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState('')
  const [micOn, setMicOn] = useState(true)
  const [camOn, setCamOn] = useState(true)
  const [screenOn, setScreenOn] = useState(false)
  const [showChat, setShowChat] = useState(false)
  const [showPeople, setShowPeople] = useState(false)
  const [messages, setMessages] = useState([])
  const [version, setVersion] = useState(0)
  const [unread, setUnread] = useState(0)
  const [meetingId, setMeetingId] = useState(null)
  const [lobby, setLobby] = useState([])
  const showChatRef = useRef(false)

  useEffect(() => {
    const room = new LKRoom({ adaptiveStream: true, dynacast: true })
    roomRef.current = room

    const refresh = () => {
      setParticipants([room.localParticipant, ...room.remoteParticipants.values()])
      setVersion((v) => v + 1)
    }
    const onData = (payload) => {
      try {
        const msg = JSON.parse(new TextDecoder().decode(payload))
        setMessages((m) => [...m, msg])
        if (!showChatRef.current) setUnread((u) => u + 1)
        playChat()
      } catch {}
    }

    room
      .on(RoomEvent.TrackSubscribed, (track, publication) => {
        refresh()
        if (publication && publication.source === Track.Source.ScreenShare) playScreenShare()
      })
      .on(RoomEvent.TrackUnsubscribed, refresh)
      .on(RoomEvent.TrackMuted, refresh)
      .on(RoomEvent.TrackUnmuted, refresh)
      .on(RoomEvent.ParticipantConnected, () => {
        refresh()
        playJoin()
      })
      .on(RoomEvent.ParticipantDisconnected, refresh)
      .on(RoomEvent.LocalTrackPublished, refresh)
      .on(RoomEvent.LocalTrackUnpublished, refresh)
      .on(RoomEvent.DataReceived, onData)

    const wsUrl = session.wsUrl && session.wsUrl.startsWith('wss://')
      ? session.wsUrl
      : `wss://${window.location.host}`
    room.connect(wsUrl, session.token)
      .then(async () => {
        setConnected(true)
        refresh()
        try { await room.localParticipant.setMicrophoneEnabled(true) } catch {}
        try { await room.localParticipant.setCameraEnabled(true) } catch {}
      })
      .catch((e) => setError(e.message))

    return () => {
      room.disconnect()
      roomRef.current = null
    }
  }, [session])

  useEffect(() => {
    if (!session.isHost) return
    let cancelled = false
    async function load() {
      try {
        const m = await meetingByRoom(session.room, getToken())
        if (cancelled) return
        setMeetingId(m.id)
        const l = await listLobby(getToken(), m.id)
        if (!cancelled) setLobby(l)
      } catch {}
    }
    load()
    const t = setInterval(load, 3000)
    return () => { cancelled = true; clearInterval(t) }
  }, [session])

  async function toggleMic() {
    const next = !micOn
    setMicOn(next)
    try { await roomRef.current.localParticipant.setMicrophoneEnabled(next) } catch {}
  }
  async function toggleCam() {
    const next = !camOn
    setCamOn(next)
    try { await roomRef.current.localParticipant.setCameraEnabled(next) } catch {}
  }
  async function toggleScreen() {
    const next = !screenOn
    setScreenOn(next)
    try {
      await roomRef.current.localParticipant.setScreenShareEnabled(next)
    } catch (e) {
      console.error('screen share failed:', e && e.message ? e.message : e)
      setScreenOn(false)
    }
  }
  function sendMessage(text) {
    const msg = { from: session.identity, text, ts: Date.now() }
    const payload = new TextEncoder().encode(JSON.stringify(msg))
    try { roomRef.current.localParticipant.publishData(payload, { reliable: true }).catch(() => {}) } catch {}
    setMessages((m) => [...m, msg])
  }
  function toggleChatPanel() {
    const next = !showChat
    showChatRef.current = next
    setShowChat(next)
    if (next) setUnread(0)
    setShowPeople(false)
  }
  function togglePeoplePanel() {
    setShowPeople((v) => !v)
    setShowChat(false)
    showChatRef.current = false
  }
  async function handleKick(identity) {
    try { await kickParticipant(getToken(), meetingId, identity) } catch {}
  }
  async function handleMute(identity, muted) {
    try { await muteParticipant(getToken(), meetingId, identity, muted) } catch {}
  }
  async function handleAdmit(reqId) {
    try {
      await admitLobby(getToken(), meetingId, reqId)
      setLobby(await listLobby(getToken(), meetingId))
    } catch {}
  }
  async function handleReject(reqId) {
    try {
      await rejectLobby(getToken(), meetingId, reqId)
      setLobby(await listLobby(getToken(), meetingId))
    } catch {}
  }

  if (error) {
    return (
      <div className="room-error">
        <p>Unable to connect</p>
        <span>{error}</span>
      </div>
    )
  }

  return (
    <div className="room">
      <header className="room-header">
        <div className="brand">VIP<span>CON</span></div>
        <div className="room-title">{session.room}</div>
        <button className="icon-btn" onClick={onToggleTheme} title="Toggle theme">
          {theme === 'dark' ? <Sun /> : <Moon />}
        </button>
      </header>

      <main className="room-main">
        <div className="grid">
          {connected && participants.map((p) => (
            <VideoTile key={p.sid || p.identity} participant={p} isLocal={p === roomRef.current?.localParticipant} version={version} />
          ))}
          {!connected && <div className="room-connecting">Connecting…</div>}
        </div>

        {showChat && <ChatPanel messages={messages} onSend={sendMessage} />}
        {showPeople && (
          <PeoplePanel
            participants={participants}
            localIdentity={session.identity}
            isHost={session.isHost}
            lobby={lobby}
            onAdmit={handleAdmit}
            onReject={handleReject}
            onKick={handleKick}
            onMute={handleMute}
          />
        )}
      </main>

      <ControlBar
        micOn={micOn}
        camOn={camOn}
        screenOn={screenOn}
        onToggleMic={toggleMic}
        onToggleCam={toggleCam}
        onToggleScreen={toggleScreen}
        onToggleChat={toggleChatPanel}
        onTogglePeople={togglePeoplePanel}
        onLeave={onLeave}
        chatActive={showChat}
        peopleActive={showPeople}
        unread={unread}
      />
    </div>
  )
}
