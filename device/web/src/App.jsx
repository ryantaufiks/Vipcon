import { useEffect, useState } from 'react'
import PreJoin from './PreJoin.jsx'
import Room from './Room.jsx'
import Onboarding from './Onboarding.jsx'
import Dashboard from './Dashboard.jsx'

function useTheme() {
  const [theme, setTheme] = useState(() => localStorage.getItem('vipcon-theme') || 'dark')
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('vipcon-theme', theme)
  }, [theme])
  return [theme, setTheme]
}

function parseRoom() {
  const m = window.location.pathname.match(/\/m\/([^/]+)/)
  return m ? decodeURIComponent(m[1]) : null
}

export default function App() {
  const [theme, setTheme] = useTheme()
  const [session, setSession] = useState(null)
  const room = parseRoom()
  const isHost = new URLSearchParams(window.location.search).get('host') === '1'

  const toggleTheme = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'))

  if (window.location.pathname === '/admin') {
    return <Dashboard theme={theme} onToggleTheme={toggleTheme} />
  }

  if (!room) {
    return <Onboarding theme={theme} onToggleTheme={toggleTheme} />
  }

  if (!session) {
    return <PreJoin room={room} isHost={isHost} onJoined={setSession} theme={theme} onToggleTheme={toggleTheme} />
  }

  return <Room session={session} theme={theme} onToggleTheme={toggleTheme} onLeave={() => setSession(null)} />
}
