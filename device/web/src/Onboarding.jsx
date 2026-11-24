import { PublicLayout } from './ui.jsx'
import { Download, Shield } from './icons.jsx'

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
    steps: ['Download the installer.', 'Right-click it and choose Run as administrator.', 'Confirm the prompt.'],
  },
  macos: {
    name: 'macOS',
    download: '/api/v1/ca.mobileconfig',
    steps: ['Download the profile.', 'Open the .mobileconfig file.', 'System Settings → General → Device Management → Install.'],
  },
  ios: {
    name: 'iPhone / iPad',
    download: '/api/v1/ca.mobileconfig',
    steps: ['Download the profile.', 'Settings → Profile Downloaded → Install.', 'Settings → General → About → Certificate Trust Settings → turn on full trust.'],
  },
  android: {
    name: 'Android',
    download: '/api/v1/ca.crt',
    steps: ['Download the certificate.', 'Settings → Security → Install a certificate → CA certificate.', 'Choose the downloaded file.'],
  },
  linux: {
    name: 'Linux',
    download: '/api/v1/install-ca.sh',
    steps: ['Download the installer.', 'Run: sudo sh install-ca.sh'],
  },
  other: {
    name: 'this device',
    download: '/api/v1/ca.crt',
    steps: ['Download the certificate.', 'Add it to your system’s trusted certificates.'],
  },
}

// Shown at the device's root address: lets older setups trust the device's local certificate.
export default function Onboarding() {
  const os = detectOS()
  const info = OS_INFO[os] || OS_INFO.other

  return (
    <PublicLayout>
      <div className="public-card" style={{ maxWidth: 500 }}>
        <div className="card card-pad form">
          <div className="state-icon brand" style={{ margin: 0 }}><Shield size={26} /></div>
          <div>
            <h1 className="page-title">Trust this VIPCON device</h1>
            <p className="page-sub">Install the device’s certificate once, so meetings open without browser security warnings.</p>
          </div>
          <div className="os-pick">
            <span><span className="subtle">Detected:</span> <strong>{info.name}</strong></span>
          </div>
          <a className="btn btn-primary btn-lg btn-block" href={info.download}><Download size={18} />Download for {info.name}</a>
          <ol className="steps">
            {info.steps.map((s) => <li key={s}>{s}</li>)}
          </ol>
          <details className="more">
            <summary>Other platforms</summary>
            <ul>
              {Object.entries(OS_INFO).filter(([key]) => key !== os && key !== 'other').map(([key, v]) => (
                <li key={key}><a className="btn btn-sm" href={v.download}>{v.name}</a></li>
              ))}
            </ul>
          </details>
          <p className="field-hint">Then open your meeting link again.</p>
        </div>
      </div>
    </PublicLayout>
  )
}
