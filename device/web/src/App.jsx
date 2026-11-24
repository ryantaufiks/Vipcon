import { lazy, Suspense, useState } from 'react'
import PreJoin from './PreJoin.jsx'
import Onboarding from './Onboarding.jsx'
import Dashboard from './Dashboard.jsx'
import Claim from './Claim.jsx'
import Now from './Now.jsx'
import { Spinner } from './ui.jsx'
import { getToken } from './api.js'

// The meeting room (and LiveKit with it) loads only when someone joins.
const Room = lazy(() => import('./Room.jsx'))
const Left = lazy(() => import('./Room.jsx').then((m) => ({ default: m.Left })))

function Loading() {
  return <div style={{ height: '100%', display: 'grid', placeItems: 'center' }}><Spinner large /></div>
}

function parseRoom() {
  const m = window.location.pathname.match(/\/m\/([^/]+)/)
  return m ? decodeURIComponent(m[1]) : null
}

function Meeting({ room, signedIn, asHost }) {
  // prejoin -> room -> left (and back to the room on "Rejoin")
  const [session, setSession] = useState(null)
  const [left, setLeft] = useState(null)

  if (left) {
    return (
      <Suspense fallback={<Loading />}>
        <Left info={left} signedIn={signedIn} onRejoin={() => { setLeft(null); setSession(left.session) }} />
      </Suspense>
    )
  }
  if (!session) {
    return <PreJoin room={room} signedIn={signedIn} asHost={asHost} onJoined={setSession} />
  }
  return (
    <Suspense fallback={<Loading />}>
      <Room
        session={session}
        onLeave={(reason) => { setLeft({ session, reason: reason || 'left' }); setSession(null) }}
      />
    </Suspense>
  )
}

export default function App() {
  const path = window.location.pathname
  if (path === '/now') return <Now />
  if (path === '/claim') return <Claim />
  if (path === '/admin' || path.startsWith('/admin/')) return <Dashboard />
  const room = parseRoom()
  if (!room) return <Onboarding />
  // From the console: ?host=1 (your own meeting) or ?staff=1 (anyone else's); otherwise a guest.
  const q = new URLSearchParams(window.location.search)
  const asHost = q.get('host') === '1'
  const signedIn = (asHost || q.get('staff') === '1') && !!getToken()
  return <Meeting room={room} signedIn={signedIn} asHost={asHost} />
}
