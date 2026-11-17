import { Sun, Moon } from './icons.jsx'

function detectOS() {
  const ua = navigator.userAgent
  if (/windows/i.test(ua)) return 'windows'
  if (/iphone|ipad|ipod/i.test(ua)) return 'ios'
  if (/mac os x/i.test(ua)) return 'macos'
  if (/android/i.test(ua)) return 'android'
  if (/linux/i.test(ua)) return 'linux'
  return 'other'
}

const OS_INFO = {
  windows: {
    name: 'Windows',
    download: '/api/v1/install-ca.bat',
    steps: ['Download the installer', 'Right-click it → Run as administrator', 'Confirm the prompt'],
  },
  macos: {
    name: 'macOS',
    download: '/api/v1/ca.mobileconfig',
    steps: ['Download the profile', 'Double-click the .mobileconfig', 'System Settings → General → Device Management → Install'],
  },
  ios: {
    name: 'iOS / iPadOS',
    download: '/api/v1/ca.mobileconfig',
    steps: ['Download the profile', 'Settings → Profile Downloaded → Install', 'Settings → General → About → Certificate Trust Settings → enable full trust'],
  },
  android: {
    name: 'Android',
    download: '/api/v1/ca.crt',
    steps: ['Download the certificate', 'Settings → Security → Install a certificate → CA certificate', 'Select the downloaded file'],
  },
  linux: {
    name: 'Linux',
    download: '/api/v1/install-ca.sh',
    steps: ['Download the installer', 'Run: sudo sh install-ca.sh'],
  },
  other: {
    name: 'this device',
    download: '/api/v1/ca.crt',
    steps: ['Download the certificate', 'Install it into your system trust store'],
  },
}

export default function Onboarding({ theme, onToggleTheme }) {
  const os = detectOS()
  const info = OS_INFO[os] || OS_INFO.other

  return (
    <div className="onboarding">
      <header className="topbar">
        <div className="brand">VIP<span>CON</span></div>
        <button className="icon-btn" onClick={onToggleTheme} title="Toggle theme">
          {theme === 'dark' ? <Sun /> : <Moon />}
        </button>
      </header>

      <div className="setup-card">
        <h1>Secure this device</h1>
        <p className="setup-sub">
          Install the local certificate once so meetings open without browser warnings.
        </p>

        <div className="setup-os">
          <span className="setup-os-label">Detected</span>
          <span className="setup-os-name">{info.name}</span>
        </div>

        <a className="btn btn-primary setup-download" href={info.download}>
          Download for {info.name}
        </a>

        <ol className="setup-steps">
          {info.steps.map((s) => <li key={s}>{s}</li>)}
        </ol>

        <details className="setup-others">
          <summary>Other platforms</summary>
          <ul className="setup-links">
            {Object.entries(OS_INFO)
              .filter(([key]) => key !== os)
              .map(([, v]) => (
                <li key={v.name}><a href={v.download}>{v.name}</a></li>
              ))}
          </ul>
        </details>

        <p className="setup-hint">After installing, open a meeting link (e.g. /m/room).</p>
      </div>
    </div>
  )
}
