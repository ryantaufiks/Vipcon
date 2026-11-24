import { useEffect, useRef, useState } from 'react'
import { Track, TrackEvent, ParticipantEvent } from 'livekit-client'
import { Avatar } from './ui.jsx'
import { Mic, Expand, Monitor } from './icons.jsx'

// One participant's camera (or, with source="screen", their shared screen).
export default function VideoTile({ participant, isLocal, version, source = 'camera', displayName }) {
  const videoRef = useRef(null)
  const audioRef = useRef(null)
  const tileRef = useRef(null)
  const [micOn, setMicOn] = useState(true)
  const [videoOn, setVideoOn] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const screen = source === 'screen'

  useEffect(() => {
    const video = participant.getTrackPublication(screen ? Track.Source.ScreenShare : Track.Source.Camera)?.track
    // Audio plays from the camera tile only (and never for yourself).
    const mic = !screen && !isLocal ? participant.getTrackPublication(Track.Source.Microphone)?.track : null
    const micPub = participant.getTrackPublication(Track.Source.Microphone)

    if (video && videoRef.current) video.attach(videoRef.current)
    if (mic && audioRef.current) mic.attach(audioRef.current)

    const sync = () => {
      setMicOn(!!micPub && !micPub.isMuted)
      setVideoOn(!!video && !video.isMuted)
    }
    sync()
    const tracks = [video, micPub?.track].filter(Boolean)
    tracks.forEach((t) => { t.on(TrackEvent.Muted, sync); t.on(TrackEvent.Unmuted, sync) })
    const spk = (s) => setSpeaking(s)
    participant.on?.(ParticipantEvent.IsSpeakingChanged, spk)

    return () => {
      if (video && videoRef.current) video.detach(videoRef.current)
      if (mic && audioRef.current) mic.detach(audioRef.current)
      tracks.forEach((t) => { t.off(TrackEvent.Muted, sync); t.off(TrackEvent.Unmuted, sync) })
      participant.off?.(ParticipantEvent.IsSpeakingChanged, spk)
    }
  }, [participant, version, screen, isLocal])

  function fullscreen() {
    if (document.fullscreenElement) document.exitFullscreen()
    else tileRef.current?.requestFullscreen?.().catch(() => {})
  }

  const name = displayName || participant.name || participant.identity || 'Guest'
  return (
    <div ref={tileRef} className={`tile ${speaking && !screen ? 'speaking' : ''}`}>
      <video ref={videoRef} autoPlay playsInline muted
             className={screen ? 'screen' : isLocal ? 'mirror' : ''}
             style={{ display: videoOn ? 'block' : 'none' }} />
      {!screen && <audio ref={audioRef} autoPlay />}
      {!videoOn && <div className="tile-avatar"><Avatar name={name} size={96} /></div>}
      <div className="tile-label">
        {screen ? <Monitor size={14} /> : !micOn && <Mic off size={14} className="muted-ic" />}
        <span>{screen ? `${name}’s screen` : `${name}${isLocal ? ' (you)' : ''}`}</span>
      </div>
      {screen && (
        <div className="tile-corner">
          <button type="button" className="tile-btn" onClick={fullscreen} title="Full screen" aria-label="Full screen">
            <Expand size={16} />
          </button>
        </div>
      )}
    </div>
  )
}
