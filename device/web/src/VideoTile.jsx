import { useEffect, useRef, useState } from 'react'
import { Track, TrackEvent, ParticipantEvent } from 'livekit-client'
import { Expand } from './icons.jsx'

export default function VideoTile({ participant, isLocal, version }) {
  const camRef = useRef(null)
  const screenRef = useRef(null)
  const audioRef = useRef(null)
  const [micOn, setMicOn] = useState(true)
  const [camOn, setCamOn] = useState(true)
  const [screenOn, setScreenOn] = useState(false)
  const [speaking, setSpeaking] = useState(false)

  useEffect(() => {
    const cam = participant.getTrackPublication(Track.Source.Camera)?.track
    const mic = participant.getTrackPublication(Track.Source.Microphone)?.track
    const scr = participant.getTrackPublication(Track.Source.ScreenShare)?.track

    if (cam && camRef.current) cam.attach(camRef.current)
    if (scr && screenRef.current) {
      scr.attach(screenRef.current)
      screenRef.current.play().catch(() => {})
    }
    if (mic && audioRef.current) mic.attach(audioRef.current)

    const sync = () => {
      setMicOn(mic ? !mic.isMuted : false)
      setCamOn(cam ? !cam.isMuted : false)
      setScreenOn(!!scr)
    }
    sync()

    const cleanups = []
    const onTrack = (t, cb) => {
      if (!t) return
      const h = () => cb()
      t.on(TrackEvent.Muted, h)
      t.on(TrackEvent.Unmuted, h)
      cleanups.push(() => {
        t.off(TrackEvent.Muted, h)
        t.off(TrackEvent.Unmuted, h)
      })
    }
    onTrack(cam, sync)
    onTrack(mic, sync)

    let spk = null
    if (participant.on) {
      spk = (s) => setSpeaking(s)
      participant.on(ParticipantEvent.IsSpeakingChanged, spk)
    }

    return () => {
      if (cam && camRef.current) cam.detach(camRef.current)
      if (scr && screenRef.current) scr.detach(screenRef.current)
      if (mic && audioRef.current) mic.detach(audioRef.current)
      cleanups.forEach((fn) => fn())
      if (spk && participant.off) participant.off(ParticipantEvent.IsSpeakingChanged, spk)
    }
  }, [participant, version])

  useEffect(() => {
    if (screenOn && screenRef.current) {
      screenRef.current.play().catch(() => {})
    }
  }, [screenOn])

  function toggleFullscreen() {
    if (document.fullscreenElement) {
      document.exitFullscreen()
    } else if (screenRef.current) {
      screenRef.current.requestFullscreen().catch(() => {})
    }
  }

  const name = participant.identity || '—'
  const initials = name.trim().slice(0, 2).toUpperCase()

  return (
    <div className={`tile ${speaking ? 'tile-speaking' : ''}`}>
      <video ref={screenRef} autoPlay playsInline className="tile-screen" />
      <video ref={camRef} autoPlay playsInline className={`tile-cam ${screenOn ? 'tile-cam-pip' : ''}`} />
      <audio ref={audioRef} autoPlay />
      {screenOn && (
        <button className="tile-expand" onClick={toggleFullscreen} title="Enlarge screen">
          <Expand />
        </button>
      )}
      {!camOn && !screenOn && <div className="tile-avatar">{initials}</div>}
      {screenOn && !camOn && <div className="tile-cam-pip-avatar">{initials}</div>}
      <div className="tile-meta">
        <span className="tile-name">{name}{isLocal ? ' (you)' : ''}</span>
        {!micOn && <span className="tile-badge">muted</span>}
      </div>
    </div>
  )
}
