let ctx = null

function getCtx() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext
    if (!AC) return null
    ctx = new AC()
  }
  if (ctx.state === 'suspended') ctx.resume()
  return ctx
}

function tone(freq, delay, duration, volume = 0.12) {
  const c = getCtx()
  if (!c) return
  const t = c.currentTime + delay
  const osc = c.createOscillator()
  const gain = c.createGain()
  osc.type = 'sine'
  osc.frequency.value = freq
  gain.gain.setValueAtTime(0.0001, t)
  gain.gain.exponentialRampToValueAtTime(volume, t + 0.02)
  gain.gain.exponentialRampToValueAtTime(0.0001, t + duration)
  osc.connect(gain)
  gain.connect(c.destination)
  osc.start(t)
  osc.stop(t + duration + 0.05)
}

export function playJoin() {
  tone(659.25, 0, 0.16)
  tone(987.77, 0.12, 0.2)
}

export function playChat() {
  tone(523.25, 0, 0.09)
}

export function playScreenShare() {
  tone(440, 0, 0.14)
  tone(554.37, 0.1, 0.18)
}
