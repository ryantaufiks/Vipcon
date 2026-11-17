import { useState } from 'react'

export function ChatPanel({ messages, onSend }) {
  const [text, setText] = useState('')
  function send(e) {
    e.preventDefault()
    if (!text.trim()) return
    onSend(text.trim())
    setText('')
  }
  return (
    <aside className="panel">
      <header className="panel-head">Chat</header>
      <div className="chat-messages">
        {messages.length === 0 && <p className="panel-empty">No messages yet</p>}
        {messages.map((m, i) => (
          <div key={i} className="chat-msg">
            <span className="chat-from">{m.from}</span>
            <span className="chat-text">{m.text}</span>
          </div>
        ))}
      </div>
      <form className="chat-input" onSubmit={send}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Message…" />
        <button className="btn btn-primary">Send</button>
      </form>
    </aside>
  )
}

export function PeoplePanel({ participants, localIdentity, isHost, lobby, onAdmit, onReject, onKick, onMute }) {
  return (
    <aside className="panel">
      <header className="panel-head">Participants · {participants.length}</header>
      <ul className="people-list">
        {participants.map((p) => (
          <li key={p.sid || p.identity}>
            <span className="people-name">{p.identity}</span>
            {p.identity === localIdentity && <span className="people-you">you</span>}
            {isHost && p.identity !== localIdentity && (
              <span className="people-actions">
                <button className="btn btn-mini" onClick={() => onMute(p.identity, true)}>Mute</button>
                <button className="btn btn-mini" onClick={() => onKick(p.identity)}>Kick</button>
              </span>
            )}
          </li>
        ))}
      </ul>

      {isHost && (
        <div className="lobby">
          <header className="panel-head">Lobby ({lobby.length})</header>
          {lobby.length === 0 && <p className="panel-empty">No one waiting</p>}
          <ul className="people-list">
            {lobby.map((r) => (
              <li key={r.id}>
                <span className="people-name">{r.identity}</span>
                <span className="people-actions">
                  <button className="btn btn-mini btn-primary" onClick={() => onAdmit(r.id)}>Admit</button>
                  <button className="btn btn-mini" onClick={() => onReject(r.id)}>Reject</button>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </aside>
  )
}
