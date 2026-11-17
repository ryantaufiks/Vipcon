import { Mic, Camera, Screen, Chat, People, Leave } from './icons.jsx'

export default function ControlBar({
  micOn, camOn, screenOn,
  onToggleMic, onToggleCam, onToggleScreen,
  onToggleChat, onTogglePeople, onLeave,
  chatActive, peopleActive, unread = 0,
}) {
  return (
    <footer className="controlbar">
      <button className={`ctl ${micOn ? '' : 'ctl-off'}`} onClick={onToggleMic} title="Toggle microphone">
        <Mic off={!micOn} /><span>Mic</span>
      </button>
      <button className={`ctl ${camOn ? '' : 'ctl-off'}`} onClick={onToggleCam} title="Toggle camera">
        <Camera off={!camOn} /><span>Cam</span>
      </button>
      <button className={`ctl ${screenOn ? 'ctl-on' : ''}`} onClick={onToggleScreen} title="Share screen">
        <Screen /><span>Share</span>
      </button>
      <button className={`ctl ${chatActive ? 'ctl-on' : ''}`} onClick={onToggleChat} title="Chat">
        <Chat /><span>Chat</span>
        {unread > 0 && <span className="badge">{unread > 99 ? '99+' : unread}</span>}
      </button>
      <button className={`ctl ${peopleActive ? 'ctl-on' : ''}`} onClick={onTogglePeople} title="Participants">
        <People /><span>People</span>
      </button>
      <button className="ctl ctl-leave" onClick={onLeave} title="Leave meeting">
        <Leave /><span>Leave</span>
      </button>
    </footer>
  )
}
