import { useEffect, useRef, useState } from 'react'
import { Track } from 'livekit-client'
import { Avatar, Button, Menu, MenuItem } from './ui.jsx'
import { X, Send, Mic, Camera, More, DoorIn, Lock } from './icons.jsx'
import { time } from './format.js'

function PanelShell({ title, onClose, children }) {
  return (
    <aside className="side-panel" aria-label={title}>
      <div className="side-head">
        <span>{title}</span>
        <Button variant="ghost" size="sm" icon={<X size={16} />} onClick={onClose} aria-label={`Close ${title}`} />
      </div>
      {children}
    </aside>
  )
}

export function ChatPanel({ messages, me, onSend, onClose }) {
  const [text, setText] = useState('')
  const endRef = useRef(null)
  useEffect(() => { endRef.current?.scrollIntoView({ block: 'end' }) }, [messages.length])

  function send(e) {
    e.preventDefault()
    const t = text.trim()
    if (!t) return
    onSend(t.slice(0, 2000))
    setText('')
  }

  return (
    <PanelShell title="Chat" onClose={onClose}>
      <div className="chat">
        {messages.length === 0 && (
          <p className="subtle" style={{ textAlign: 'center', marginTop: 24 }}>
            Messages are visible to everyone in the meeting and are not saved.
          </p>
        )}
        {messages.map((m, i) => {
          const mine = m.from === me
          const grouped = i > 0 && messages[i - 1].from === m.from && m.ts - messages[i - 1].ts < 120000
          return (
            <div key={i} className={`msg ${mine ? 'mine' : ''}`}>
              {!grouped && <span className="msg-meta">{mine ? 'You' : <b>{m.from}</b>} · {time(new Date(m.ts))}</span>}
              <span className="msg-text">{m.text}</span>
            </div>
          )
        })}
        <div ref={endRef} />
      </div>
      <form className="chat-form" onSubmit={send}>
        <input className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Message everyone"
               aria-label="Message" maxLength={2000} />
        <Button type="submit" variant="primary" icon={<Send size={16} />} aria-label="Send" disabled={!text.trim()} />
      </form>
    </PanelShell>
  )
}

export function PeoplePanel({ participants, localIdentity, isHost, lobby, locked, onAdmit, onReject, onAdmitAll, onKick, onMute,
  onMuteAll, onLock, onClose }) {
  const isHostOf = (p) => { try { return !!JSON.parse(p.metadata || '{}').host } catch { return false } }
  const othersTalking = participants.some((p) => p.identity !== localIdentity && !isHostOf(p)
    && p.getTrackPublication(Track.Source.Microphone) && !p.getTrackPublication(Track.Source.Microphone).isMuted)
  return (
    <PanelShell title={`People (${participants.length})`} onClose={onClose}>
      {isHost && (
        <div className="host-tools">
          <Button size="sm" icon={<Mic off size={16} />} onClick={onMuteAll} disabled={!othersTalking}
                  title="Mute everyone except the hosts. They can unmute themselves.">Mute all</Button>
          <Button size="sm" variant={locked ? 'primary' : 'secondary'} icon={<Lock size={16} />} onClick={() => onLock(!locked)}
                  aria-pressed={!!locked} title={locked ? 'Let new people join or ask to join again' : 'Stop anyone new from joining'}>
            {locked ? 'Unlock meeting' : 'Lock meeting'}
          </Button>
        </div>
      )}
      {isHost && locked && <p className="host-note"><Lock size={13} /> Locked: no one new can join or ask to join.</p>}
      <div className="side-body">
        {isHost && lobby.length > 0 && (
          <>
            <div className="side-section">
              <span>Waiting to join ({lobby.length})</span>
              {lobby.length > 1 && <Button size="sm" variant="ghost" onClick={onAdmitAll}>Admit all</Button>}
            </div>
            {lobby.map((r) => (
              <div key={r.id} className="lobby-card">
                <div className="person" style={{ padding: 0 }}>
                  <Avatar name={r.identity} size={30} />
                  <span className="person-name">{r.identity}</span>
                </div>
                <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                  <Button size="sm" variant="primary" icon={<DoorIn size={14} />} onClick={() => onAdmit(r.id)}>Admit</Button>
                  <Button size="sm" variant="ghost" onClick={() => onReject(r.id)}>Decline</Button>
                </div>
              </div>
            ))}
          </>
        )}
        <div className="side-section"><span>In the meeting</span></div>
        {participants.map((p) => {
          const you = p.identity === localIdentity
          const mic = p.getTrackPublication(Track.Source.Microphone)
          const cam = p.getTrackPublication(Track.Source.Camera)
          const micOn = !!mic && !mic.isMuted
          const camOn = !!cam && !cam.isMuted
          const host = isHostOf(p)
          return (
            <div key={p.sid || p.identity} className="person">
              <Avatar name={p.identity} size={30} />
              <span className="person-name">
                {p.identity}
                {you && <small>(you)</small>}
                {host && !you && <small>Host</small>}
              </span>
              <span className="person-icons">
                <Mic off={!micOn} size={16} className={micOn ? '' : 'off'} />
                <Camera off={!camOn} size={16} className={camOn ? '' : 'off'} />
              </span>
              {isHost && !you && (
                <Menu trigger={({ toggle }) => (
                  <Button size="sm" variant="ghost" icon={<More size={16} />} onClick={toggle} aria-label={`Actions for ${p.identity}`} />
                )}>
                  <MenuItem icon={<Mic off size={16} />} onClick={() => onMute(p.identity)} disabled={!micOn}>Mute microphone</MenuItem>
                  <MenuItem icon={<X size={16} />} danger onClick={() => onKick(p.identity)}>Remove from meeting</MenuItem>
                </Menu>
              )}
            </div>
          )
        })}
      </div>
    </PanelShell>
  )
}
