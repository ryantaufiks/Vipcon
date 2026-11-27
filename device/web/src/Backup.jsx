// Encrypted off-site backup (opt-in): turn on (shows the Backup Key once), back up now, turn off,
// and restore from a Backup Key. The device does the encryption; the key never leaves the client.
import { useCallback, useEffect, useState } from 'react'
import { backupStatus, backupEnable, backupRun, backupDisable, backupRestore } from './api.js'
import { Button, TextInput, PasswordInput, Notice, Modal, Spinner, useToast, useConfirm, copyText } from './ui.jsx'
import { Download, Copy, Refresh } from './icons.jsx'
import { friendly } from './format.js'

function ago(epoch) {
  if (!epoch) return 'never'
  return new Date(epoch * 1000).toLocaleString()
}

function size(bytes) {
  if (!bytes) return ''
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

function keyText(key, org) {
  return [
    'VIPCON Backup Key',
    org ? `Organisation: ${org}` : null,
    `Created: ${new Date().toLocaleString()}`,
    '',
    'This key decrypts the off-site backups of this VIPCON device (accounts, meetings and settings).',
    'Only you have it: without it nobody, including VIPCON, can read or restore the backups.',
    'To restore: console > Security > Restore from backup, on this or a replacement device.',
    '',
    key,
    '',
  ].filter((l) => l !== null).join('\n')
}

function BackupKey({ value, org, onDone }) {
  const toast = useToast()
  const [saved, setSaved] = useState(false)
  function download() {
    const url = URL.createObjectURL(new Blob([keyText(value, org)], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = 'vipcon-backup-key.txt'
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  return (
    <div className="form">
      <Notice kind="warning">Save this key now. It is shown only once, and it is the only way to read or restore your backups.</Notice>
      <div className="backup-key mono">{value}</div>
      <div className="kit-actions">
        <Button icon={<Download size={16} />} onClick={download}>Download</Button>
        <Button icon={<Copy size={16} />} onClick={async () => {
          const ok = await copyText(value)
          toast(ok ? { message: 'Backup Key copied.' } : { kind: 'error', message: 'Could not copy. Download it instead.' })
        }}>Copy</Button>
      </div>
      <label className="check-row">
        <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} />
        <span>I have saved the Backup Key somewhere safe</span>
      </label>
      <Button variant="primary" size="lg" block disabled={!saved} onClick={onDone}>Done</Button>
    </div>
  )
}

export function BackupCard({ token, fail, org, onChanged, onRestored }) {
  const toast = useToast()
  const confirm = useConfirm()
  const [status, setStatus] = useState(null)
  const [asking, setAsking] = useState(null) // 'enable' | 'disable'
  const [password, setPassword] = useState('')
  const [pwError, setPwError] = useState('')
  const [busy, setBusy] = useState(false)
  const [key, setKey] = useState(null)
  const [restoring, setRestoring] = useState(false)

  const load = useCallback(() => { backupStatus(token).then(setStatus).catch(fail) }, [token, fail])
  useEffect(() => { load() }, [load])

  async function submitPassword(e) {
    e.preventDefault()
    if (!password) { setPwError('Enter your password.'); return }
    setBusy(true)
    setPwError('')
    try {
      if (asking === 'enable') {
        const r = await backupEnable(token, password)
        setStatus({ ...r.status, available: true })
        setKey(r.backup_key)
        if (r.status.last_error) toast({ kind: 'error', message: `Backups are on, but the first one failed: ${r.status.last_error}` })
      } else {
        const r = await backupDisable(token, password)
        toast(r.remote_deleted ? { message: 'Backups are off and the off-site copies were deleted.' }
          : { kind: 'error', message: 'Backups are off, but the off-site copies could not be deleted. Contact VIPCON support to remove them.' })
        load()
      }
      setAsking(null)
      setPassword('')
      onChanged?.()
    } catch (err) {
      setPwError(friendly(err))
    } finally {
      setBusy(false)
    }
  }

  async function runNow() {
    setBusy(true)
    try {
      const r = await backupRun(token)
      setStatus((s) => ({ ...s, ...r }))
      toast(r.last_error ? { kind: 'error', message: r.last_error } : { message: 'Backup stored.' })
      onChanged?.()
    } catch (err) { fail(err) } finally { setBusy(false) }
  }

  async function turnOff() {
    const ok = await confirm({
      title: 'Turn off off-site backup?',
      message: 'The copies stored off-site are deleted, and this device forgets its Backup Key. You can turn backups on again later with a new key.',
      confirmLabel: 'Turn off', danger: true,
    })
    if (ok) { setAsking('disable'); setPwError('') }
  }

  return (
    <div className="card card-pad form" style={{ marginTop: 16 }}>
      <div className="sec-head">
        <Refresh size={20} />
        <div>
          <h2 className="sec-title">Encrypted off-site backup</h2>
          <p className="muted" style={{ margin: 0 }}>
            A daily copy of accounts, meetings and settings, encrypted on this device with a key only you hold. VIPCON stores the
            encrypted copy and cannot read it. Meeting audio, video and chat are never recorded, so they are never in a backup.
          </p>
        </div>
      </div>
      {status === null ? <Spinner /> : !status.available ? (
        <Notice kind="info">This device works without the VIPCON service, so off-site backup is not available.</Notice>
      ) : status.enabled ? (
        <>
          <Notice kind={status.last_error ? 'error' : 'success'}>
            {status.last_error ? `Last attempt failed: ${status.last_error}`
              : `On · last backup ${ago(status.last_ok_at)}${status.last_size ? ` · ${size(status.last_size)}` : ''}`}
          </Notice>
          <div className="kit-actions">
            <Button icon={<Refresh size={16} />} loading={busy} onClick={runNow}>Back up now</Button>
            <Button onClick={() => setRestoring(true)}>Restore from backup</Button>
            <Button variant="danger-ghost" onClick={turnOff}>Turn off</Button>
          </div>
        </>
      ) : (
        <div className="kit-actions">
          <Button variant="primary" onClick={() => { setAsking('enable'); setPwError('') }}>Turn on backup</Button>
          <Button onClick={() => setRestoring(true)}>Restore from backup</Button>
        </div>
      )}

      <Modal open={!!asking} onClose={() => setAsking(null)}
             title={asking === 'enable' ? 'Turn on off-site backup' : 'Turn off off-site backup'}
             description={asking === 'enable' ? 'Enter your password. You will get a Backup Key to save; the first backup runs straight away.'
               : 'Enter your password to confirm.'}
             footer={<>
               <Button onClick={() => setAsking(null)}>Cancel</Button>
               <Button variant={asking === 'enable' ? 'primary' : 'danger'} type="submit" form="backup-password" loading={busy}>
                 {asking === 'enable' ? 'Turn on' : 'Turn off'}
               </Button>
             </>}>
        <form id="backup-password" className="form" onSubmit={submitPassword} noValidate>
          <PasswordInput label="Password" value={password} onChange={(e) => setPassword(e.target.value)}
                         autoComplete="current-password" error={pwError} />
        </form>
      </Modal>

      <Modal open={!!key} onClose={() => {}} title="Your Backup Key" description="Keep it with your Recovery Kit, somewhere safe and private.">
        {key && <BackupKey value={key} org={org} onDone={() => setKey(null)} />}
      </Modal>

      <RestoreModal open={restoring} onClose={() => setRestoring(false)} token={token} onRestored={onRestored} />
    </div>
  )
}

function RestoreModal({ open, onClose, token, onRestored }) {
  const [backupKey, setBackupKey] = useState('')
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!open) return
    setBackupKey('')
    setPassword('')
    setErrors({})
    setError('')
  }, [open])

  async function submit(e) {
    e.preventDefault()
    setError('')
    const errs = {}
    if (!backupKey.trim()) errs.backupKey = 'Paste your Backup Key.'
    if (!password) errs.password = 'Enter your password.'
    setErrors(errs)
    if (Object.keys(errs).length) return
    setBusy(true)
    try {
      await backupRestore(token, backupKey, password)
      onRestored()
    } catch (err) {
      setError(friendly(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Restore from backup"
           description="Replaces every account, meeting and setting on this device with the newest backup. Everyone is signed out."
           footer={<>
             <Button onClick={onClose}>Cancel</Button>
             <Button variant="danger" type="submit" form="restore-backup" loading={busy}>Restore</Button>
           </>}>
      <form id="restore-backup" className="form" onSubmit={submit} noValidate>
        <Notice kind="warning">After the restore, sign in with an account from the backup. Accounts created on this device since then are gone.</Notice>
        <TextInput label="Backup Key" value={backupKey} onChange={(e) => setBackupKey(e.target.value)} className="input-mono"
                   placeholder="VBK1-…" autoCapitalize="characters" spellCheck={false} autoComplete="off" error={errors.backupKey} />
        <PasswordInput label="Your password" value={password} onChange={(e) => setPassword(e.target.value)}
                       autoComplete="current-password" error={errors.password} />
        {error && <Notice kind="error">{error}</Notice>}
      </form>
    </Modal>
  )
}
